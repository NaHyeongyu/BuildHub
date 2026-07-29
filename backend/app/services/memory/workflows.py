from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session as DBSession

from app.core.config import settings
from app.core.security import is_admin_user
from app.models.artifacts import Artifact
from app.models.projects import Project
from app.models.sessions import Session
from app.models.users import User
from app.services.memory.constants import (
    LOCAL_MEMORY_GENERATOR,
    PENDING_MEMORY_DRAFT_GENERATOR,
)
from app.services.memory.providers import (
    configured_generator_for_provider,
    provider_is_configured,
)
from app.services.memory.serializers import (
    serialize_memory_artifact,
    serialize_memory_artifact_summary,
)
from app.services.memory.artifacts import (
    approve_project_memory_snapshot,
    list_project_memory_artifacts,
    generate_due_memory_artifacts_for_session,
    get_latest_project_memory,
    list_project_memory_pending_ranges,
    update_project_memory_snapshot,
)
from app.services.memory.batches import (
    ProjectMemoryReviewRequiredError,
    build_project_memory_generation_review,
    generate_project_memory_batch,
    lock_project_memory,
    preview_project_memory_batch,
    read_project_memory_batch,
    read_latest_project_memory_batch,
    serialize_project_memory_batch,
)
from app.services.memory.session_completion import complete_session_if_ready
from app.services.projects.management import list_project_summaries
from app.services.projects.views import read_project_detail_response


logger = logging.getLogger(__name__)


def project_for_user(
    db: DBSession,
    project_id: UUID,
    user: User,
    *,
    allow_admin: bool = False,
) -> Project:
    project = db.get(Project, project_id)
    if project is None or (
        project.owner_id != user.id and not (allow_admin and is_admin_user(user))
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return project


def session_for_project(db: DBSession, project: Project, session_id: UUID) -> Session:
    session = db.get(Session, session_id)
    if session is None or session.project_id != project.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return session


def serialize_project_memory_snapshot(artifact: Artifact | None) -> dict[str, Any] | None:
    if artifact is None:
        return None
    metadata = artifact.metadata_ if isinstance(artifact.metadata_, dict) else {}
    return {
        "artifact": serialize_memory_artifact(artifact),
        "snapshot": metadata.get("project_memory_snapshot"),
    }


def memory_generator_status() -> dict[str, Any]:
    return {
        "fallback_generator": LOCAL_MEMORY_GENERATOR,
        "gemini_configured": bool(settings.gemini_api_key),
        "gemini_max_retries": 0,
        "gemini_model": settings.gemini_model,
        "openai_configured": bool(settings.openai_api_key),
        "openai_max_retries": 0,
        "openai_model": settings.openai_model,
        "generators": {
            "draft": configured_generator_for_provider(
                settings.memory_draft_generator,
                stage="draft",
            ),
            "pending_draft": PENDING_MEMORY_DRAFT_GENERATOR,
            "project": configured_generator_for_provider(
                settings.project_memory_generator,
                stage="project",
            ),
        },
        "requested_generators": {
            "draft": settings.memory_draft_generator,
            "pending_draft": "local",
            "project": settings.project_memory_generator,
        },
        "timeout_seconds": {
            "gemini": settings.gemini_timeout_seconds,
            "openai": settings.openai_timeout_seconds,
        },
    }


def list_pending_memory_ranges_response(
    db: DBSession,
    *,
    limit: int,
    project_id: UUID,
    user: User,
) -> list[dict[str, Any]]:
    project = project_for_user(db, project_id, user, allow_admin=True)
    return list_project_memory_pending_ranges(db, project_id=project.id, limit=limit)


def read_project_workspace_response(
    db: DBSession,
    *,
    pending_limit: int,
    project_id: UUID,
    user: User,
) -> dict[str, Any]:
    detail = read_project_detail_response(project_id, user, db)
    latest_batch = read_latest_project_memory_batch(db, project_id=project_id)
    return {
        **detail,
        "memory": {
            **detail["memory"],
            "drafts": [],
            "latest_batch": (
                serialize_project_memory_batch(db, latest_batch, replayed=True)
                if latest_batch is not None
                else None
            ),
            "pending_ranges": list_project_memory_pending_ranges(
                db,
                project_id=project_id,
                limit=pending_limit,
            ),
        },
    }


def refresh_memory_review_queue_response(
    db: DBSession,
    *,
    limit: int,
    user: User,
) -> dict[str, Any]:
    project_summaries = list_project_summaries(db, current_user=user)
    errors: list[dict[str, str]] = []
    failed_project_ids: set[str] = set()
    ranges_by_project: dict[str, list[dict[str, Any]]] = {}

    for summary in project_summaries:
        if summary["pending_memory_count"] <= 0:
            ranges_by_project[summary["id"]] = []
            continue
        project_id = UUID(summary["id"])
        try:
            with db.begin_nested():
                ranges_by_project[summary["id"]] = list_project_memory_pending_ranges(
                    db,
                    limit=limit,
                    project_id=project_id,
                )
        except Exception:
            failed_project_ids.add(summary["id"])
            logger.exception(
                "Review queue materialization failed for project %s",
                summary["id"],
            )
            errors.append(
                {
                    "message": "Captured work could not be checked for this project.",
                    "project_id": summary["id"],
                }
            )
            ranges_by_project[summary["id"]] = []

    queue_projects = [
        {
            "pending_count": summary["pending_memory_count"],
            "project_id": summary["id"],
            "ranges": ranges_by_project.get(summary["id"], []),
        }
        for summary in project_summaries
        if summary["pending_memory_count"] > 0 and summary["id"] not in failed_project_ids
    ]
    return {
        "errors": errors,
        "project_summaries": project_summaries,
        "projects": queue_projects,
        "total_pending_count": sum(item["pending_memory_count"] for item in project_summaries),
    }


def complete_project_session_response(
    db: DBSession,
    *,
    force: bool,
    project_id: UUID,
    session_id: UUID,
    user: User,
) -> dict[str, Any]:
    project = project_for_user(db, project_id, user)
    session = session_for_project(db, project, session_id)
    completion = complete_session_if_ready(db, session, force=force)
    if not completion["completed"]:
        return {
            "artifact": None,
            "completion": {
                "completed": False,
                "completed_at": None,
                "reason": completion["reason"],
            },
            "pending_range": None,
            "status": "session_open",
        }

    generate_due_memory_artifacts_for_session(
        db,
        session,
        finalize=True,
    )

    pending_range = next(
        (
            item
            for item in list_project_memory_pending_ranges(db, project_id=project.id, limit=100)
            if item["session_id"] == str(session.id)
        ),
        None,
    )
    return {
        "artifact": None,
        "completion": {
            "completed": True,
            "completed_at": completion["completed_at"].isoformat()
            if completion["completed_at"]
            else None,
            "reason": completion["reason"],
        },
        "message": "Session completed. Captured work is ready for Project Memory.",
        "pending_range": pending_range,
        "status": "pending_memory",
    }


def generate_project_memory_response(
    db: DBSession,
    *,
    idempotency_key: str,
    project_id: UUID,
    user: User,
    excluded_prompt_event_ids: list[UUID] | None = None,
    review_token: str = "",
) -> dict[str, Any]:
    project = project_for_user(db, project_id, user)
    if not provider_is_configured(
        settings.memory_draft_generator
    ) or not provider_is_configured(settings.project_memory_generator):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI generation is not configured. Captured work is safe; try again shortly.",
        )
    try:
        return generate_project_memory_batch(
            db,
            idempotency_key=idempotency_key,
            project_id=project.id,
            user_id=user.id,
            excluded_prompt_event_ids=excluded_prompt_event_ids,
            review_token=review_token,
        )
    except ProjectMemoryReviewRequiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def project_memory_generation_review_response(
    db: DBSession,
    *,
    project_id: UUID,
    user: User,
) -> dict[str, Any]:
    project = project_for_user(db, project_id, user)
    return build_project_memory_generation_review(
        db,
        project_id=project.id,
        user_id=user.id,
    )


def read_latest_project_memory_batch_response(
    db: DBSession,
    *,
    project_id: UUID,
    user: User,
) -> dict[str, Any] | None:
    project = project_for_user(db, project_id, user)
    batch = read_latest_project_memory_batch(db, project_id=project.id)
    if batch is None:
        return None
    return serialize_project_memory_batch(db, batch, replayed=True)


def preview_project_memory_generation_response(
    db: DBSession,
    *,
    project_id: UUID,
    user: User,
) -> dict[str, Any]:
    project = project_for_user(db, project_id, user)
    return preview_project_memory_batch(db, project_id=project.id)


def read_project_memory_batch_response(
    db: DBSession,
    *,
    batch_id: UUID,
    project_id: UUID,
    user: User,
) -> dict[str, Any]:
    project = project_for_user(db, project_id, user)
    batch = read_project_memory_batch(
        db,
        batch_id=batch_id,
        project_id=project.id,
    )
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project Memory batch not found",
        )
    return serialize_project_memory_batch(db, batch, replayed=True)


def read_project_memory_response(
    db: DBSession,
    *,
    project_id: UUID,
    user: User,
) -> dict[str, Any] | None:
    project = project_for_user(db, project_id, user, allow_admin=True)
    return serialize_project_memory_snapshot(get_latest_project_memory(db, project_id=project.id))


def update_project_memory_response(
    db: DBSession,
    *,
    body_markdown: str,
    project_id: UUID,
    user: User,
) -> dict[str, Any]:
    project = project_for_user(db, project_id, user)
    lock_project_memory(db, project.id)
    artifact = update_project_memory_snapshot(
        db,
        approved_by=user.id,
        body_markdown=body_markdown,
        project_id=project.id,
    )
    return serialize_project_memory_snapshot(artifact) or {"artifact": None, "snapshot": None}


def approve_project_memory_response(
    db: DBSession,
    *,
    project_id: UUID,
    user: User,
) -> dict[str, Any]:
    project = project_for_user(db, project_id, user)
    lock_project_memory(db, project.id)
    artifact = approve_project_memory_snapshot(
        db,
        approved_by=user.id,
        project_id=project.id,
    )
    return serialize_project_memory_snapshot(artifact) or {"artifact": None, "snapshot": None}


def list_project_artifacts_response(
    db: DBSession,
    *,
    limit: int,
    project_id: UUID,
    user: User,
) -> list[dict[str, Any]]:
    project = project_for_user(db, project_id, user, allow_admin=True)
    artifacts = list_project_memory_artifacts(
        db,
        limit=limit,
        project_id=project.id,
    )
    return [serialize_memory_artifact_summary(artifact) for artifact in artifacts]
