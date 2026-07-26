from __future__ import annotations

from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import timedelta
import hmac
import hashlib
import json
import logging
from math import ceil
import re
import time
from threading import Event as ThreadEvent, Thread
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import cast, delete, desc, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.core.config import settings
from app.core.encoding import base64_urldecode, base64_urlencode
from app.core.policies import configured_external_ai_providers
from app.core.time import utc_now
from app.db.session import SessionLocal
from app.models.artifact_versions import ArtifactVersion
from app.models.artifacts import Artifact
from app.models.project_memory_batches import (
    ProjectMemoryBatch,
    ProjectMemoryBatchItem,
    ProjectMemoryBatchRequest,
)
from app.models.projects import Project
from app.models.sessions import Session
from app.services.memory.artifacts import (
    _pending_draft_range,
    _pending_draft_generation_context,
)
from app.services.memory.context import dedupe_files
from app.services.memory.constants import (
    MEMORY_ARTIFACT_TYPE,
    MEMORY_DRAFT_ARTIFACT_TYPE,
    PENDING_DRAFT_STAGE,
    REVIEW_STATE_DRAFT,
    REVIEW_STATE_GENERATION_FAILED,
    REVIEW_STATE_GENERATED,
    REVIEW_STATE_IGNORED,
)
from app.services.memory.draft_payloads import build_memory_draft_payloads_from_context
from app.services.memory.project_memory import (
    generate_project_memory_compilation,
    prepare_project_memory_compilation,
    project_memory_compilation_guard,
    write_project_memory_compilation,
)
from app.services.memory.providers import provider_name
from app.services.memory.repository import write_memory_artifact_payload
from app.services.memory.text import truncate


logger = logging.getLogger(__name__)

PROJECT_MEMORY_BATCH_CHUNK_SIZE = 6
PROJECT_MEMORY_BATCH_GENERATOR = "project-memory-batch-v1"
PROJECT_MEMORY_BATCH_LEASE = timedelta(minutes=10)
PROJECT_MEMORY_BATCH_TRIGGER = "project_batch"
MEMORY_GROUPING_SESSION = "session"
MEMORY_GROUPING_CHRONOLOGICAL = "chronological"
HTTP_STATUS_PATTERN = re.compile(r"\bHTTP(?: status)?\s+([1-5][0-9]{2})\b")
MEMORY_REVIEW_TOKEN_TTL_SECONDS = 15 * 60
MEMORY_REVIEW_TOKEN_MAX_CHARS = 4_096


class ProjectMemoryBatchError(RuntimeError):
    code = "project_memory_batch_failed"


class ProjectMemoryBatchGenerationError(ProjectMemoryBatchError):
    code = "generation_failed"


class ProjectMemoryBatchProviderUnavailableError(ProjectMemoryBatchGenerationError):
    code = "provider_unavailable"


class ProjectMemoryBatchInvariantError(ProjectMemoryBatchError):
    code = "snapshot_invalid"


class ProjectMemoryBatchLeaseLostError(ProjectMemoryBatchGenerationError):
    code = "lease_lost"


class ProjectMemoryReviewRequiredError(ProjectMemoryBatchError):
    code = "prompt_review_required"


def _heartbeat_interval_seconds() -> float:
    return max(settings.memory_worker_heartbeat_seconds, 1.0)


def _extend_batch_lease(batch_id: UUID, attempt_count: int) -> bool:
    db = SessionLocal()
    try:
        now = utc_now()
        result = db.execute(
            update(ProjectMemoryBatch)
            .where(
                ProjectMemoryBatch.id == batch_id,
                ProjectMemoryBatch.status == "running",
                ProjectMemoryBatch.attempt_count == attempt_count,
                ProjectMemoryBatch.lease_expires_at.is_not(None),
                ProjectMemoryBatch.lease_expires_at > now,
            )
            .values(
                lease_expires_at=now + PROJECT_MEMORY_BATCH_LEASE,
                updated_at=now,
            )
        )
        db.commit()
        return bool(result.rowcount)
    except Exception:
        db.rollback()
        logger.exception("Project Memory batch %s heartbeat failed", batch_id)
        return False
    finally:
        db.close()


@contextmanager
def _batch_lease_heartbeat(batch_id: UUID, attempt_count: int):
    stopped = ThreadEvent()

    def heartbeat() -> None:
        interval = _heartbeat_interval_seconds()
        while not stopped.wait(interval):
            if not _extend_batch_lease(batch_id, attempt_count):
                return

    thread = Thread(
        target=heartbeat,
        name=f"project-memory-heartbeat-{batch_id}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=_heartbeat_interval_seconds() + 1.0)


@dataclass(frozen=True)
class PendingDraftSnapshot:
    id: UUID
    version_id: UUID
    title: str
    summary: str | None
    metadata_: dict[str, Any]
    created_at: Any


@dataclass(frozen=True)
class GeneratedChunkPayload:
    first_event_at: str | None
    last_event_at: str | None
    metadata: dict[str, Any]
    payload: dict[str, Any]
    source_draft_ids: list[str]
    source_draft_version_ids: list[str]
    source_session_id: str


@dataclass(frozen=True)
class PendingChunkGeneration:
    context: dict[str, Any]
    snapshots: list[PendingDraftSnapshot]
    source_session_id: str


@dataclass(frozen=True)
class BatchAttemptSnapshot:
    batch_id: UUID
    chunk_generations: list[PendingChunkGeneration]
    project_id: UUID
    project_name: str
    snapshot_manifest: list[dict[str, Any]]
    source_session_ids: list[str]
    grouping_mode: str = MEMORY_GROUPING_SESSION
    chunk_results: dict[str, list[GeneratedChunkPayload]] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedBatchMemory:
    artifact_id: UUID
    event_id: UUID | None
    extra_metadata: dict[str, Any]
    payload: dict[str, Any]
    project_id: UUID
    storage_key: str
    timestamp: Any


def _pending_draft_filters(project_id: UUID) -> tuple[Any, ...]:
    return (
        Artifact.project_id == project_id,
        Artifact.type == MEMORY_DRAFT_ARTIFACT_TYPE,
        Artifact.metadata_["artifact_stage"].astext == PENDING_DRAFT_STAGE,
        Artifact.metadata_["review_state"].astext == REVIEW_STATE_DRAFT,
        Artifact.metadata_["sent_to_ai_at"].astext.is_(None),
    )


def _project_memory_lock_key(project_id: UUID) -> int:
    return int.from_bytes(
        hashlib.blake2b(project_id.bytes, digest_size=8).digest(),
        byteorder="big",
        signed=True,
    )


def lock_project_memory(db: DBSession, project_id: UUID) -> Project:
    db.execute(select(func.pg_advisory_xact_lock(_project_memory_lock_key(project_id))))
    project = db.get(Project, project_id)
    if project is None:
        raise ProjectMemoryBatchInvariantError("Project not found")
    return project


def _batch_by_idempotency_key(
    db: DBSession,
    *,
    idempotency_key: str,
    project_id: UUID,
) -> ProjectMemoryBatch | None:
    mapped = db.execute(
        select(ProjectMemoryBatch)
        .join(
            ProjectMemoryBatchRequest,
            ProjectMemoryBatchRequest.batch_id == ProjectMemoryBatch.id,
        )
        .where(
            ProjectMemoryBatchRequest.project_id == project_id,
            ProjectMemoryBatchRequest.idempotency_key == idempotency_key,
            ProjectMemoryBatch.project_id == ProjectMemoryBatchRequest.project_id,
        )
        .with_for_update(of=ProjectMemoryBatch)
    ).scalar_one_or_none()
    if mapped is not None:
        return mapped
    return db.execute(
        select(ProjectMemoryBatch)
        .where(
            ProjectMemoryBatch.project_id == project_id,
            or_(
                ProjectMemoryBatch.idempotency_key == idempotency_key,
                ProjectMemoryBatch.idempotency_keys.contains([idempotency_key]),
            ),
        )
        .with_for_update()
    ).scalar_one_or_none()


def _in_progress_batch_by_idempotency_key(
    db: DBSession,
    *,
    idempotency_key: str,
    project_id: UUID,
) -> ProjectMemoryBatch | None:
    mapped = db.execute(
        select(ProjectMemoryBatch)
        .join(
            ProjectMemoryBatchRequest,
            ProjectMemoryBatchRequest.batch_id == ProjectMemoryBatch.id,
        )
        .where(
            ProjectMemoryBatchRequest.project_id == project_id,
            ProjectMemoryBatchRequest.idempotency_key == idempotency_key,
            ProjectMemoryBatch.project_id == ProjectMemoryBatchRequest.project_id,
            ProjectMemoryBatch.status == "running",
            ProjectMemoryBatch.lease_expires_at > utc_now(),
        )
        .order_by(desc(ProjectMemoryBatch.updated_at))
        .limit(1)
    ).scalar_one_or_none()
    if mapped is not None:
        return mapped
    return db.execute(
        select(ProjectMemoryBatch)
        .where(
            ProjectMemoryBatch.project_id == project_id,
            ProjectMemoryBatch.status == "running",
            ProjectMemoryBatch.lease_expires_at > utc_now(),
            or_(
                ProjectMemoryBatch.idempotency_key == idempotency_key,
                ProjectMemoryBatch.idempotency_keys.contains([idempotency_key]),
            ),
        )
        .order_by(desc(ProjectMemoryBatch.updated_at))
        .limit(1)
    ).scalar_one_or_none()


def _active_batch(
    db: DBSession,
    *,
    project_id: UUID,
) -> ProjectMemoryBatch | None:
    return db.execute(
        select(ProjectMemoryBatch)
        .where(
            ProjectMemoryBatch.project_id == project_id,
            ProjectMemoryBatch.status.in_(["pending", "running"]),
        )
        .order_by(desc(ProjectMemoryBatch.updated_at))
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()


def _attach_idempotency_key(
    db: DBSession,
    batch: ProjectMemoryBatch,
    idempotency_key: str,
    *,
    update_legacy_keys: bool = True,
) -> ProjectMemoryBatch:
    request = db.get(
        ProjectMemoryBatchRequest,
        {
            "project_id": batch.project_id,
            "idempotency_key": idempotency_key,
        },
    )
    if request is None:
        try:
            with db.begin_nested():
                request = ProjectMemoryBatchRequest(
                    batch_id=batch.id,
                    idempotency_key=idempotency_key,
                    project_id=batch.project_id,
                )
                db.add(request)
                db.flush()
        except IntegrityError:
            request = db.get(
                ProjectMemoryBatchRequest,
                {
                    "project_id": batch.project_id,
                    "idempotency_key": idempotency_key,
                },
            )
    if request is None:
        raise ProjectMemoryBatchInvariantError("Idempotency request could not be recorded")
    if request.batch_id != batch.id:
        mapped = db.get(ProjectMemoryBatch, request.batch_id)
        if mapped is not None and mapped.project_id == batch.project_id:
            return mapped
        request.batch_id = batch.id

    if update_legacy_keys:
        keys = list(batch.idempotency_keys or [])
        if idempotency_key not in keys:
            batch.idempotency_keys = [*keys, idempotency_key]
    return batch


def _visible_active_batch(
    db: DBSession,
    *,
    project_id: UUID,
) -> ProjectMemoryBatch | None:
    return db.execute(
        select(ProjectMemoryBatch)
        .where(
            ProjectMemoryBatch.project_id == project_id,
            ProjectMemoryBatch.status.in_(["pending", "running"]),
        )
        .order_by(desc(ProjectMemoryBatch.updated_at))
        .limit(1)
    ).scalar_one_or_none()


def _latest_versions_by_artifact(
    db: DBSession,
    draft_ids: list[UUID],
) -> dict[UUID, ArtifactVersion]:
    if not draft_ids:
        return {}

    versions = db.execute(
        select(ArtifactVersion)
        .where(ArtifactVersion.artifact_id.in_(draft_ids))
        .distinct(ArtifactVersion.artifact_id)
        .order_by(
            ArtifactVersion.artifact_id,
            desc(ArtifactVersion.version),
        )
    ).scalars()
    return {version.artifact_id: version for version in versions}


def _memory_review_secret() -> bytes:
    secret = (
        settings.app_encryption_key
        or settings.jwt_secret
        or settings.oauth_state_secret
        or settings.api_token
    )
    if not secret:
        raise ProjectMemoryReviewRequiredError(
            "Prompt review signing is not configured"
        )
    return secret.encode("utf-8")


def _available_pending_drafts(db: DBSession, project_id: UUID) -> list[Artifact]:
    claimed_drafts = select(ProjectMemoryBatchItem.draft_id)
    return list(
        db.execute(
            select(Artifact)
            .where(
                *_pending_draft_filters(project_id),
                Artifact.id.not_in(claimed_drafts),
            )
            .order_by(Artifact.created_at, Artifact.id)
            .limit(settings.project_memory_batch_max_drafts)
        ).scalars()
    )


def _review_manifest_digest(
    drafts: list[Artifact],
    versions: dict[UUID, ArtifactVersion],
) -> str:
    manifest: list[dict[str, Any]] = []
    for draft in drafts:
        version = versions.get(draft.id)
        if version is None:
            raise ProjectMemoryBatchInvariantError(
                f"Pending draft {draft.id} has no immutable version"
            )
        content = {
            "changed_files": version.changed_files,
            "metadata": version.metadata_,
            "prompt_event_ids": version.prompt_event_ids,
            "summary": version.summary,
            "title": version.title,
        }
        content_digest = hashlib.sha256(
            json.dumps(
                content,
                default=str,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        manifest.append(
            {
                "content_digest": content_digest,
                "draft_id": str(draft.id),
                "version_id": str(version.id),
            }
        )
    return hashlib.sha256(
        json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _encode_memory_review_token(
    *,
    digest: str,
    project_id: UUID,
    user_id: UUID,
) -> str:
    payload = {
        "digest": digest,
        "iat": int(time.time()),
        "project_id": str(project_id),
        "user_id": str(user_id),
    }
    body = base64_urlencode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(
        _memory_review_secret(),
        body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{body}.{base64_urlencode(signature)}"


def _decode_memory_review_token(token: str) -> dict[str, Any]:
    if not token or len(token) > MEMORY_REVIEW_TOKEN_MAX_CHARS:
        raise ProjectMemoryReviewRequiredError("Review prompts again before generating memory")
    try:
        body, signature = token.split(".", 1)
        actual_signature = base64_urldecode(signature)
        payload = json.loads(base64_urldecode(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProjectMemoryReviewRequiredError(
            "Review prompts again before generating memory"
        ) from exc
    expected_signature = hmac.new(
        _memory_review_secret(),
        body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(actual_signature, expected_signature):
        raise ProjectMemoryReviewRequiredError("Review prompts again before generating memory")
    issued_at = payload.get("iat") if isinstance(payload, dict) else None
    now = int(time.time())
    if (
        not isinstance(issued_at, int)
        or issued_at > now + 60
        or now - issued_at > MEMORY_REVIEW_TOKEN_TTL_SECONDS
    ):
        raise ProjectMemoryReviewRequiredError("Prompt review expired; review prompts again")
    return payload


def _validate_memory_review_token(
    *,
    drafts: list[Artifact],
    excluded_prompt_event_ids: set[str],
    project_id: UUID,
    review_token: str = "",
    user_id: UUID,
    versions: dict[UUID, ArtifactVersion],
) -> set[str]:
    payload = _decode_memory_review_token(review_token)
    expected_digest = _review_manifest_digest(drafts, versions)
    if (
        payload.get("project_id") != str(project_id)
        or payload.get("user_id") != str(user_id)
        or not hmac.compare_digest(str(payload.get("digest") or ""), expected_digest)
    ):
        raise ProjectMemoryReviewRequiredError(
            "Captured work changed; review prompts again before generating memory"
        )

    reviewable_prompt_ids = {
        str(prompt_id)
        for draft in drafts
        for prompt_id in _prompt_event_ids_from_version(versions[draft.id])
    }
    unexpected_ids = excluded_prompt_event_ids - reviewable_prompt_ids
    if unexpected_ids:
        raise ProjectMemoryReviewRequiredError(
            "Prompt exclusions changed; review the AI input again before generating memory"
        )
    return excluded_prompt_event_ids


def _prompt_event_ids_from_version(version: ArtifactVersion) -> list[UUID]:
    raw_ids: list[Any] = list(getattr(version, "prompt_event_ids", None) or [])
    raw_metadata = getattr(version, "metadata_", None)
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    evidence = metadata.get("draft_evidence")
    if isinstance(evidence, dict):
        prompts = evidence.get("prompts")
        if isinstance(prompts, list):
            raw_ids.extend(
                prompt.get("event_id")
                for prompt in prompts
                if isinstance(prompt, dict)
            )
    event_ids: list[UUID] = []
    for raw_id in raw_ids:
        try:
            event_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))
        except (TypeError, ValueError):
            continue
        if event_id not in event_ids:
            event_ids.append(event_id)
    return event_ids


def _review_evidence(version: ArtifactVersion) -> dict[str, Any]:
    raw_metadata = getattr(version, "metadata_", None)
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    evidence = metadata.get("draft_evidence")
    return evidence if isinstance(evidence, dict) else {}


def _review_prompt_records(
    draft: Artifact,
    version: ArtifactVersion,
) -> list[dict[str, Any]]:
    evidence = _review_evidence(version)
    prompts = evidence.get("prompts") if isinstance(evidence.get("prompts"), list) else []
    responses = (
        evidence.get("responses") if isinstance(evidence.get("responses"), list) else []
    )
    events = evidence.get("events") if isinstance(evidence.get("events"), list) else []
    responses_by_id = {
        str(response.get("event_id")): response
        for response in responses
        if isinstance(response, dict) and response.get("event_id")
    }
    timestamps_by_sequence = {
        event.get("sequence"): event.get("timestamp")
        for event in events
        if isinstance(event, dict)
        and event.get("event_type") == "PromptSubmitted"
        and isinstance(event.get("timestamp"), str)
    }
    metadata = version.metadata_ if isinstance(version.metadata_, dict) else {}
    records: list[dict[str, Any]] = []
    for prompt in prompts:
        if not isinstance(prompt, dict) or not prompt.get("event_id"):
            continue
        ai_input = prompt.get("ai_input") if isinstance(prompt.get("ai_input"), dict) else {}
        raw_text = ai_input.get("text") or prompt.get("original_input") or ""
        text = raw_text if isinstance(raw_text, str) else ""
        response = responses_by_id.get(str(prompt.get("paired_response_event_id")))
        raw_response = None
        if response is not None:
            raw_response = response.get("output_preview") or response.get("original_output")
        response_text = raw_response if isinstance(raw_response, str) else None
        created_at = timestamps_by_sequence.get(prompt.get("sequence"))
        if not isinstance(created_at, str):
            created_at = version.created_at.isoformat()
        records.append(
            {
                "created_at": created_at,
                "event_id": str(prompt["event_id"]),
                "prompt_truncated": (
                    ai_input.get("truncated_for_ai") is True
                    or prompt.get("storage_truncated") is True
                    or len(text) > 700
                ),
                "response_preview": truncate(response_text, 320),
                "response_truncated": bool(
                    response_text
                    and (
                        (response or {}).get("storage_truncated") is True
                        or len(response_text) > 320
                    )
                ),
                "sequence": int(prompt.get("sequence") or 0),
                "session_id": str(draft.session_id),
                "text": truncate(text, 700) or "",
                "tool": str(metadata.get("tool") or "unknown"),
            }
        )
    return records


def build_project_memory_generation_review(
    db: DBSession,
    *,
    project_id: UUID,
    user_id: UUID,
) -> dict[str, Any]:
    drafts = _available_pending_drafts(db, project_id)
    versions = _latest_versions_by_artifact(db, [draft.id for draft in drafts])
    missing_versions = [str(draft.id) for draft in drafts if draft.id not in versions]
    if missing_versions:
        raise ProjectMemoryBatchInvariantError(
            f"Pending drafts have no immutable version: {', '.join(missing_versions)}"
        )

    prompts_by_id: dict[str, dict[str, Any]] = {}
    response_ids: set[str] = set()
    changed_file_paths: set[str] = set()
    commit_keys: set[tuple[str, str]] = set()
    for draft in drafts:
        version = versions[draft.id]
        for prompt in _review_prompt_records(draft, version):
            prompts_by_id.setdefault(prompt["event_id"], prompt)
        evidence = _review_evidence(version)
        for response in evidence.get("responses") or []:
            if isinstance(response, dict) and response.get("event_id"):
                response_ids.add(str(response["event_id"]))
        for changed_file in evidence.get("changed_files") or []:
            if isinstance(changed_file, dict) and changed_file.get("path"):
                changed_file_paths.add(str(changed_file["path"]))
        for commit in evidence.get("commits") or []:
            if isinstance(commit, dict):
                commit_keys.add((str(commit.get("hash") or ""), str(commit.get("message") or "")))

    prompts = sorted(
        prompts_by_id.values(),
        key=lambda prompt: (prompt["created_at"], prompt["sequence"], prompt["event_id"]),
    )
    digest = _review_manifest_digest(drafts, versions)
    return {
        "changed_file_count": len(changed_file_paths),
        "commit_count": len(commit_keys),
        "draft_count": len(drafts),
        "prompt_count": len(prompts),
        "prompts": prompts,
        "providers": configured_external_ai_providers(),
        "response_count": len(response_ids),
        "review_token": _encode_memory_review_token(
            digest=digest,
            project_id=project_id,
            user_id=user_id,
        ),
        "source_code_included": False,
    }


def _pending_draft_claim_statement(project_id: UUID, snapshot_at: Any):
    claimed_drafts = select(ProjectMemoryBatchItem.draft_id)
    return (
        select(Artifact)
        .where(
            *_pending_draft_filters(project_id),
            Artifact.updated_at <= snapshot_at,
            Artifact.id.not_in(claimed_drafts),
        )
        .order_by(Artifact.created_at, Artifact.id)
        .limit(settings.project_memory_batch_max_drafts)
        .with_for_update()
    )


def _prepare_batch(
    db: DBSession,
    *,
    idempotency_key: str,
    project_id: UUID,
    user_id: UUID,
    excluded_prompt_event_ids: list[UUID] | None = None,
    review_token: str = "",
) -> ProjectMemoryBatch:
    snapshot_at = utc_now()
    project = db.get(Project, project_id)
    if project is None:
        raise ProjectMemoryBatchInvariantError("Project not found")
    drafts = list(db.execute(_pending_draft_claim_statement(project_id, snapshot_at)).scalars())
    versions = _latest_versions_by_artifact(db, [draft.id for draft in drafts])
    missing_versions = [str(draft.id) for draft in drafts if draft.id not in versions]
    if missing_versions:
        raise ProjectMemoryBatchInvariantError(
            f"Pending drafts have no immutable version: {', '.join(missing_versions)}"
        )

    requested_exclusions = {
        str(event_id) for event_id in (excluded_prompt_event_ids or [])
    }
    if review_token:
        _validate_memory_review_token(
            drafts=drafts,
            excluded_prompt_event_ids=requested_exclusions,
            project_id=project_id,
            review_token=review_token,
            user_id=user_id,
            versions=versions,
        )
    elif requested_exclusions:
        raise ProjectMemoryReviewRequiredError(
            "Review the AI input before excluding prompts from memory generation"
        )

    source_session_ids = list(
        dict.fromkeys(str(draft.session_id) for draft in drafts if draft.session_id is not None)
    )
    batch = ProjectMemoryBatch(
        idempotency_key=idempotency_key,
        idempotency_keys=[idempotency_key],
        grouping_mode=project.memory_grouping_mode,
        project_id=project_id,
        requested_by_user_id=user_id,
        snapshot_at=snapshot_at,
        source_session_ids=source_session_ids,
        snapshot_manifest=[
            {
                "draft_id": str(draft.id),
                "draft_version_id": str(versions[draft.id].id),
                "excluded_prompt_event_ids": sorted(
                    requested_exclusions
                    & {
                        str(event_id)
                        for event_id in _prompt_event_ids_from_version(versions[draft.id])
                    }
                ),
                "ordinal": ordinal,
                "source_session_id": str(draft.session_id) if draft.session_id else None,
            }
            for ordinal, draft in enumerate(drafts, start=1)
        ],
        status="pending",
    )
    db.add(batch)
    db.flush()
    _attach_idempotency_key(db, batch, idempotency_key)
    for ordinal, draft in enumerate(drafts, start=1):
        db.add(
            ProjectMemoryBatchItem(
                batch_id=batch.id,
                draft_id=draft.id,
                draft_version_id=versions[draft.id].id,
                ordinal=ordinal,
                source_session_id=draft.session_id,
            )
        )

    if not drafts:
        now = utc_now()
        batch.status = "succeeded"
        batch.result_status = "no_pending"
        batch.completed_at = now
        batch.updated_at = now
    db.flush()
    db.commit()
    return batch


def _item_count(db: DBSession, batch_id: UUID) -> int:
    return (
        db.scalar(
            select(func.count(ProjectMemoryBatchItem.draft_id)).where(
                ProjectMemoryBatchItem.batch_id == batch_id
            )
        )
        or 0
    )


def _remaining_pending_count(db: DBSession, project_id: UUID) -> int:
    return (
        db.scalar(select(func.count(Artifact.id)).where(*_pending_draft_filters(project_id))) or 0
    )


def _provider_generation_estimate(
    *,
    calls: int,
    prompt_max_bytes: int,
    provider: str,
    stage: str,
) -> dict[str, Any]:
    configured = (
        provider == "openai" and bool(settings.openai_api_key)
    ) or (
        provider == "gemini" and bool(settings.gemini_api_key)
    )
    billed_calls = calls if configured else 0
    estimated_input_tokens = billed_calls * ceil(max(prompt_max_bytes, 0) / 3)
    estimated_output_tokens = billed_calls * max(
        settings.memory_provider_output_max_tokens,
        0,
    )
    if provider == "openai":
        model = settings.openai_model
        input_rate = max(settings.openai_input_usd_per_million_tokens, 0)
        output_rate = max(settings.openai_output_usd_per_million_tokens, 0)
    elif provider == "gemini":
        model = settings.gemini_model
        input_rate = max(settings.gemini_input_usd_per_million_tokens, 0)
        output_rate = max(settings.gemini_output_usd_per_million_tokens, 0)
    else:
        model = "local"
        input_rate = 0
        output_rate = 0
    estimated_cost_microusd = round(
        estimated_input_tokens * input_rate
        + estimated_output_tokens * output_rate
    )
    return {
        "calls": billed_calls,
        "configured": configured,
        "estimated_cost_microusd": estimated_cost_microusd,
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_output_tokens": estimated_output_tokens,
        "model": model,
        "provider": provider,
        "requested_calls": calls,
        "stage": stage,
    }


def preview_project_memory_batch(
    db: DBSession,
    *,
    project_id: UUID,
) -> dict[str, Any]:
    project = db.get(Project, project_id) if hasattr(db, "get") else None
    grouping_mode = getattr(project, "memory_grouping_mode", MEMORY_GROUPING_SESSION)
    claimed_drafts = select(ProjectMemoryBatchItem.draft_id)
    available_filters = (
        *_pending_draft_filters(project_id),
        Artifact.id.not_in(claimed_drafts),
    )
    available_count = (
        db.scalar(select(func.count(Artifact.id)).where(*available_filters)) or 0
    )
    selected_drafts = list(
        db.execute(
            select(Artifact)
            .where(*available_filters)
            .order_by(Artifact.created_at, Artifact.id)
            .limit(settings.project_memory_batch_max_drafts)
        ).scalars()
    )
    session_draft_counts: dict[str, int] = defaultdict(int)
    for draft in selected_drafts:
        session_key = str(draft.session_id or draft.id)
        session_draft_counts[session_key] += 1
    draft_calls = (
        ceil(len(selected_drafts) / PROJECT_MEMORY_BATCH_CHUNK_SIZE)
        if grouping_mode == MEMORY_GROUPING_CHRONOLOGICAL
        else sum(
            ceil(count / PROJECT_MEMORY_BATCH_CHUNK_SIZE)
            for count in session_draft_counts.values()
        )
    )
    ranges = [_pending_draft_range(draft) for draft in selected_drafts]
    draft_estimate = _provider_generation_estimate(
        calls=draft_calls,
        prompt_max_bytes=settings.memory_draft_prompt_max_bytes,
        provider=provider_name(settings.memory_draft_generator),
        stage="memory_draft_generation",
    )
    project_estimate = _provider_generation_estimate(
        calls=1 if selected_drafts and draft_estimate["configured"] else 0,
        prompt_max_bytes=settings.project_memory_prompt_max_bytes,
        provider=provider_name(settings.project_memory_generator),
        stage="project_memory_generation",
    )
    estimates = [draft_estimate, project_estimate]
    return {
        "can_generate": bool(selected_drafts) and bool(draft_estimate["configured"]),
        "currency": "USD",
        "draft_count": len(selected_drafts),
        "estimated_cost_microusd": sum(
            estimate["estimated_cost_microusd"] for estimate in estimates
        ),
        "estimated_input_tokens": sum(
            estimate["estimated_input_tokens"] for estimate in estimates
        ),
        "estimated_output_tokens": sum(
            estimate["estimated_output_tokens"] for estimate in estimates
        ),
        "estimated_provider_calls": sum(estimate["calls"] for estimate in estimates),
        "event_count": sum(item["event_count"] for item in ranges),
        "file_change_event_count": sum(
            item["file_change_event_count"] for item in ranges
        ),
        "one_time_generation": True,
        "overflow_draft_count": max(available_count - len(selected_drafts), 0),
        "prompt_count": sum(item["prompt_count"] for item in ranges),
        "providers": estimates,
        "ranges": ranges,
        "retryable": False,
        "session_count": len(session_draft_counts),
    }


def serialize_project_memory_batch(
    db: DBSession,
    batch: ProjectMemoryBatch,
    *,
    replayed: bool,
) -> dict[str, Any]:
    result_status = batch.result_status
    if batch.status in {"pending", "running"}:
        result_status = "generation_in_progress"
    elif batch.status in {"failed", "superseded"}:
        result_status = "generation_failed"
    retryable = batch.status == "failed" and batch.error_code != "snapshot_invalid"
    messages = {
        "generation_failed": (
            "Project Memory was not updated. Captured work is still available; try again."
        ),
        "generation_in_progress": "Project Memory is being updated.",
        "memory_generated": "Project Memory was updated from the captured project work.",
        "no_memory": "No durable project context was found in the captured work.",
        "no_pending": "There is no captured work waiting for Project Memory.",
    }
    message = messages.get(result_status or "", "Project Memory batch completed.")
    if batch.error_code == "snapshot_invalid":
        message = "Captured work changed before generation. Refresh and try again."
    elif batch.status == "failed" and batch.error_message:
        message = batch.error_message
    return {
        "batch_id": str(batch.id),
        "batch_status": batch.status,
        "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
        "error": (
            {
                "code": batch.error_code or "generation_failed",
                "message": (
                    "Captured work changed before generation. Refresh and try again."
                    if batch.error_code == "snapshot_invalid"
                    else message
                ),
                "retryable": retryable,
            }
            if batch.status in {"failed", "superseded"}
            else None
        ),
        "generated_artifact_ids": list(batch.generated_artifact_ids or []),
        "message": message,
        "project_memory_artifact_id": (
            str(batch.project_memory_artifact_id) if batch.project_memory_artifact_id else None
        ),
        "remaining_pending_count": _remaining_pending_count(db, batch.project_id),
        "replayed": replayed,
        "snapshot_at": batch.snapshot_at.isoformat(),
        "retryable": retryable,
        "source_draft_count": max(
            _item_count(db, batch.id),
            len(batch.snapshot_manifest or []),
        ),
        "source_session_ids": list(batch.source_session_ids or []),
        "status": result_status,
    }


def read_project_memory_batch(
    db: DBSession,
    *,
    batch_id: UUID,
    project_id: UUID,
) -> ProjectMemoryBatch | None:
    return db.execute(
        select(ProjectMemoryBatch).where(
            ProjectMemoryBatch.id == batch_id,
            ProjectMemoryBatch.project_id == project_id,
        )
    ).scalar_one_or_none()


def read_latest_project_memory_batch(
    db: DBSession,
    *,
    project_id: UUID,
) -> ProjectMemoryBatch | None:
    return db.execute(
        select(ProjectMemoryBatch)
        .where(ProjectMemoryBatch.project_id == project_id)
        .order_by(desc(ProjectMemoryBatch.created_at), desc(ProjectMemoryBatch.id))
        .limit(1)
    ).scalar_one_or_none()


def _claim_batch_run(
    db: DBSession,
    batch_id: UUID,
) -> tuple[ProjectMemoryBatch, bool, int]:
    batch = db.execute(
        select(ProjectMemoryBatch).where(ProjectMemoryBatch.id == batch_id).with_for_update()
    ).scalar_one()
    now = utc_now()
    if batch.status != "pending":
        attempt_count = batch.attempt_count
        db.commit()
        return batch, False, attempt_count
    batch.status = "running"
    batch.result_status = None
    batch.attempt_count += 1
    batch.started_at = now
    batch.lease_expires_at = now + PROJECT_MEMORY_BATCH_LEASE
    batch.completed_at = None
    batch.error_code = None
    batch.error_message = None
    batch.updated_at = now
    db.flush()
    db.commit()
    return batch, True, batch.attempt_count


def _load_batch_snapshots(
    db: DBSession,
    batch: ProjectMemoryBatch,
    *,
    lock_drafts: bool = False,
) -> list[tuple[ProjectMemoryBatchItem, Artifact, PendingDraftSnapshot, Session]]:
    exclusions_by_draft_id = {
        str(entry.get("draft_id")): {
            str(event_id)
            for event_id in entry.get("excluded_prompt_event_ids", [])
            if event_id
        }
        for entry in (batch.snapshot_manifest or [])
        if isinstance(entry, dict) and entry.get("draft_id")
    }
    statement = (
        select(
            ProjectMemoryBatchItem,
            Artifact,
            ArtifactVersion,
            Session,
        )
        .join(Artifact, Artifact.id == ProjectMemoryBatchItem.draft_id)
        .join(
            ArtifactVersion,
            ArtifactVersion.id == ProjectMemoryBatchItem.draft_version_id,
        )
        .join(Session, Session.id == ProjectMemoryBatchItem.source_session_id)
        .where(ProjectMemoryBatchItem.batch_id == batch.id)
        .order_by(ProjectMemoryBatchItem.ordinal)
    )
    if lock_drafts:
        statement = statement.with_for_update(of=Artifact)
    rows = db.execute(statement).all()
    snapshots: list[tuple[ProjectMemoryBatchItem, Artifact, PendingDraftSnapshot, Session]] = []
    for item, draft, version, session in rows:
        metadata = draft.metadata_ if isinstance(draft.metadata_, dict) else {}
        if metadata.get("review_state") != REVIEW_STATE_DRAFT:
            raise ProjectMemoryBatchInvariantError(f"Draft {draft.id} is no longer pending")
        if version.artifact_id != draft.id or version.project_id != batch.project_id:
            raise ProjectMemoryBatchInvariantError(
                f"Draft {draft.id} no longer matches its captured version"
            )
        latest_version_id = metadata.get("latest_version_id")
        if latest_version_id and latest_version_id != str(version.id):
            raise ProjectMemoryBatchInvariantError(
                f"Draft {draft.id} changed after the batch snapshot"
            )
        if session.project_id != batch.project_id:
            raise ProjectMemoryBatchInvariantError(
                f"Session {session.id} does not belong to the batch project"
            )
        excluded_prompt_ids = exclusions_by_draft_id.get(str(draft.id), set())
        snapshot_title, snapshot_summary, snapshot_metadata = _filtered_snapshot_values(
            version,
            excluded_prompt_ids=excluded_prompt_ids,
        )
        snapshots.append(
            (
                item,
                draft,
                PendingDraftSnapshot(
                    id=draft.id,
                    version_id=version.id,
                    title=snapshot_title,
                    summary=snapshot_summary,
                    metadata_=snapshot_metadata,
                    created_at=version.created_at,
                ),
                session,
            )
        )
    if len(snapshots) != _item_count(db, batch.id):
        raise ProjectMemoryBatchInvariantError("Batch source sessions are incomplete")
    return snapshots


def _filtered_snapshot_values(
    version: ArtifactVersion,
    *,
    excluded_prompt_ids: set[str],
) -> tuple[str, str | None, dict[str, Any]]:
    metadata = deepcopy(version.metadata_) if isinstance(version.metadata_, dict) else {}
    if not excluded_prompt_ids:
        return version.title, version.summary, metadata

    evidence = metadata.get("draft_evidence")
    if not isinstance(evidence, dict):
        raise ProjectMemoryBatchInvariantError(
            f"Draft version {version.id} has no reviewable evidence"
        )

    prompts = evidence.get("prompts") if isinstance(evidence.get("prompts"), list) else []
    removed_prompts = [
        prompt
        for prompt in prompts
        if isinstance(prompt, dict)
        and str(prompt.get("event_id")) in excluded_prompt_ids
    ]
    found_prompt_ids = {
        str(prompt.get("event_id")) for prompt in removed_prompts if prompt.get("event_id")
    }
    missing_prompt_ids = excluded_prompt_ids - found_prompt_ids
    if missing_prompt_ids:
        raise ProjectMemoryBatchInvariantError(
            "Reviewed prompt exclusions no longer match the captured evidence"
        )

    removed_response_ids = {
        str(prompt.get("paired_response_event_id"))
        for prompt in removed_prompts
        if prompt.get("paired_response_event_id")
    }
    removed_turn_ids = {
        str(prompt.get("turn_id"))
        for prompt in removed_prompts
        if prompt.get("turn_id") is not None
    }
    removed_prompt_sequences = {
        prompt.get("sequence")
        for prompt in removed_prompts
        if isinstance(prompt.get("sequence"), int)
    }

    responses = (
        evidence.get("responses") if isinstance(evidence.get("responses"), list) else []
    )
    removed_responses = [
        response
        for response in responses
        if isinstance(response, dict)
        and (
            str(response.get("event_id")) in removed_response_ids
            or (
                response.get("turn_id") is not None
                and str(response.get("turn_id")) in removed_turn_ids
            )
        )
    ]
    removed_response_sequences = {
        response.get("sequence")
        for response in removed_responses
        if isinstance(response.get("sequence"), int)
    }

    evidence["prompts"] = [
        prompt
        for prompt in prompts
        if not (
            isinstance(prompt, dict)
            and str(prompt.get("event_id")) in excluded_prompt_ids
        )
    ]
    evidence["responses"] = [
        response
        for response in responses
        if response not in removed_responses
    ]
    events = evidence.get("events") if isinstance(evidence.get("events"), list) else []

    def event_was_excluded(event: Any) -> bool:
        if not isinstance(event, dict):
            return False
        event_type = event.get("event_type")
        sequence = event.get("sequence")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        turn_id = payload.get("turn_id")
        if event_type == "PromptSubmitted":
            return sequence in removed_prompt_sequences or (
                turn_id is not None and str(turn_id) in removed_turn_ids
            )
        if event_type == "ResponseReceived":
            return sequence in removed_response_sequences or (
                turn_id is not None and str(turn_id) in removed_turn_ids
            )
        return False

    evidence["events"] = [event for event in events if not event_was_excluded(event)]
    metadata["draft_evidence"] = evidence
    metadata["review_excluded_prompt_event_ids"] = sorted(excluded_prompt_ids)
    metadata["event_count"] = len(evidence["events"])
    return "Reviewed pending activity", None, metadata


def _chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _multiple_or_single(values: list[Any], *, fallback: str) -> str:
    unique = list(dict.fromkeys(value for value in values if isinstance(value, str) and value))
    if not unique:
        return fallback
    return unique[0] if len(unique) == 1 else "multiple"


def _merge_chronological_contexts(contexts: list[dict[str, Any]]) -> dict[str, Any]:
    if not contexts:
        raise ProjectMemoryBatchInvariantError("Chronological memory chunk has no context")

    def items(key: str) -> list[Any]:
        return [
            item
            for context in contexts
            for item in (context.get(key) if isinstance(context.get(key), list) else [])
        ]

    started_at = [context.get("started_at") for context in contexts if context.get("started_at")]
    ended_at = [context.get("ended_at") for context in contexts if context.get("ended_at")]
    first_event_ids = [context.get("first_event_id") for context in contexts]
    last_event_ids = [context.get("last_event_id") for context in contexts]
    source_session_ids = list(
        dict.fromkeys(
            context["session_id"]
            for context in contexts
            if isinstance(context.get("session_id"), str) and context["session_id"]
        )
    )
    first = contexts[0]
    return {
        "changed_files": dedupe_files(items("changed_files")),
        "commits": items("commits"),
        "ended_at": max(ended_at) if ended_at else None,
        "event_count": sum(
            value
            for context in contexts
            if isinstance((value := context.get("event_count")), int)
        ),
        "events": items("events"),
        "first_event_id": next(
            (value for value in first_event_ids if isinstance(value, str) and value),
            None,
        ),
        "last_event_id": next(
            (
                value
                for value in reversed(last_event_ids)
                if isinstance(value, str) and value
            ),
            None,
        ),
        "model": _multiple_or_single(
            [context.get("model") for context in contexts],
            fallback="unknown",
        ),
        "output_locale": first.get("output_locale") or "en",
        "pending_drafts": items("pending_drafts"),
        "project_id": first["project_id"],
        "project_name": first["project_name"],
        "prompt_events": items("prompt_events"),
        "response_count": sum(
            value
            for context in contexts
            if isinstance((value := context.get("response_count")), int)
        ),
        "responses": items("responses"),
        "session_id": "chronological",
        "source_session_ids": source_session_ids,
        "started_at": min(started_at) if started_at else None,
        "tool": _multiple_or_single(
            [context.get("tool") for context in contexts],
            fallback="promty",
        ),
        "window": {"end_sequence": None, "start_sequence": None},
    }


def _chunk_generations_for_rows(
    db: DBSession,
    rows: list[tuple[ProjectMemoryBatchItem, Artifact, PendingDraftSnapshot, Session]],
    *,
    grouping_mode: str,
) -> list[PendingChunkGeneration]:
    chunk_generations: list[PendingChunkGeneration] = []
    if grouping_mode == MEMORY_GROUPING_CHRONOLOGICAL:
        for chunk_rows in _chunks(rows, PROJECT_MEMORY_BATCH_CHUNK_SIZE):
            contexts = [
                _pending_draft_generation_context(db, session, [snapshot])  # type: ignore[list-item]
                for _item, _draft, snapshot, session in chunk_rows
            ]
            chunk_generations.append(
                PendingChunkGeneration(
                    context=_merge_chronological_contexts(contexts),
                    snapshots=[snapshot for _, _, snapshot, _ in chunk_rows],
                    source_session_id="chronological",
                )
            )
        return chunk_generations

    grouped: dict[UUID, list[tuple[Artifact, PendingDraftSnapshot, Session]]] = defaultdict(list)
    for _item, draft, snapshot, session in rows:
        grouped[session.id].append((draft, snapshot, session))
    for session_rows in grouped.values():
        for chunk_rows in _chunks(session_rows, PROJECT_MEMORY_BATCH_CHUNK_SIZE):
            snapshots = [snapshot for _, snapshot, _ in chunk_rows]
            session = chunk_rows[0][2]
            context = _pending_draft_generation_context(
                db,
                session,
                snapshots,  # type: ignore[arg-type]
            )
            chunk_generations.append(
                PendingChunkGeneration(
                    context=context,
                    snapshots=snapshots,
                    source_session_id=str(session.id),
                )
            )
    return chunk_generations


def _coverage_bounds(snapshots: list[PendingDraftSnapshot]) -> tuple[str | None, str | None]:
    timestamps: list[str] = []
    for snapshot in snapshots:
        evidence = snapshot.metadata_.get("draft_evidence")
        if not isinstance(evidence, dict):
            continue
        events = evidence.get("events") if isinstance(evidence.get("events"), list) else []
        timestamps.extend(
            timestamp
            for event in events
            if isinstance(event, dict)
            and isinstance((timestamp := event.get("timestamp")), str)
            and timestamp
        )
    timestamps.sort()
    if timestamps:
        return timestamps[0], timestamps[-1]
    created = sorted(
        snapshot.created_at.isoformat() for snapshot in snapshots if snapshot.created_at is not None
    )
    return (created[0], created[-1]) if created else (None, None)


def _event_id(value: Any) -> UUID | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _generate_chunk_payloads(
    *,
    context: dict[str, Any],
    snapshots: list[PendingDraftSnapshot],
    source_session_id: str,
) -> list[GeneratedChunkPayload]:
    payloads, generation_metadata = build_memory_draft_payloads_from_context(
        context,
        trigger_reason=PROJECT_MEMORY_BATCH_TRIGGER,
    )
    if not payloads:
        if "draft_generation" in generation_metadata:
            return []
        fallback_reason = str(
            generation_metadata.get("fallback_reason") or "Memory generation failed"
        )
        if fallback_reason.endswith("_disabled") or "API key is not configured" in fallback_reason:
            raise ProjectMemoryBatchProviderUnavailableError(
                "AI generation provider is not configured"
            )
        raise ProjectMemoryBatchGenerationError(
            fallback_reason
        )

    source_draft_ids = [str(snapshot.id) for snapshot in snapshots]
    source_draft_version_ids = [str(snapshot.version_id) for snapshot in snapshots]
    first_event_at, last_event_at = _coverage_bounds(snapshots)
    return [
        GeneratedChunkPayload(
            first_event_at=first_event_at,
            last_event_at=last_event_at,
            metadata={
                **draft_metadata,
                **generation_metadata,
                "commit_metadata": context["commits"],
            },
            payload=payload,
            source_draft_ids=source_draft_ids,
            source_draft_version_ids=source_draft_version_ids,
            source_session_id=source_session_id,
        )
        for payload, draft_metadata in payloads
    ]


def _chunk_key(generation: PendingChunkGeneration) -> str:
    version_ids = [str(snapshot.version_id) for snapshot in generation.snapshots]
    digest = hashlib.sha256("\x1f".join(version_ids).encode("utf-8")).hexdigest()
    return f"draft-versions-v1:{digest}"


def _serialize_generated_chunk(chunk: GeneratedChunkPayload) -> dict[str, Any]:
    return {
        "first_event_at": chunk.first_event_at,
        "last_event_at": chunk.last_event_at,
        "metadata": chunk.metadata,
        "payload": chunk.payload,
        "source_draft_ids": chunk.source_draft_ids,
        "source_draft_version_ids": chunk.source_draft_version_ids,
        "source_session_id": chunk.source_session_id,
    }


def _deserialize_generated_chunk(value: Any) -> GeneratedChunkPayload:
    if not isinstance(value, dict):
        raise ProjectMemoryBatchInvariantError("Stored chunk result is invalid")
    metadata = value.get("metadata")
    payload = value.get("payload")
    source_draft_ids = value.get("source_draft_ids")
    source_draft_version_ids = value.get("source_draft_version_ids")
    source_session_id = value.get("source_session_id")
    if (
        not isinstance(metadata, dict)
        or not isinstance(payload, dict)
        or not isinstance(source_draft_ids, list)
        or not all(isinstance(item, str) for item in source_draft_ids)
        or not isinstance(source_draft_version_ids, list)
        or not all(isinstance(item, str) for item in source_draft_version_ids)
        or not isinstance(source_session_id, str)
    ):
        raise ProjectMemoryBatchInvariantError("Stored chunk result is invalid")
    first_event_at = value.get("first_event_at")
    last_event_at = value.get("last_event_at")
    if first_event_at is not None and not isinstance(first_event_at, str):
        raise ProjectMemoryBatchInvariantError("Stored chunk result is invalid")
    if last_event_at is not None and not isinstance(last_event_at, str):
        raise ProjectMemoryBatchInvariantError("Stored chunk result is invalid")
    return GeneratedChunkPayload(
        first_event_at=first_event_at,
        last_event_at=last_event_at,
        metadata=metadata,
        payload=payload,
        source_draft_ids=source_draft_ids,
        source_draft_version_ids=source_draft_version_ids,
        source_session_id=source_session_id,
    )


def _deserialize_chunk_result(
    value: Any,
    *,
    generation: PendingChunkGeneration,
) -> list[GeneratedChunkPayload]:
    if not isinstance(value, dict):
        raise ProjectMemoryBatchInvariantError("Stored chunk progress is invalid")
    expected_version_ids = [str(snapshot.version_id) for snapshot in generation.snapshots]
    if value.get("draft_version_ids") != expected_version_ids:
        raise ProjectMemoryBatchInvariantError(
            "Stored chunk progress does not match the captured draft versions"
        )
    payloads = value.get("payloads")
    if not isinstance(payloads, list):
        raise ProjectMemoryBatchInvariantError("Stored chunk progress is invalid")
    chunks = [_deserialize_generated_chunk(payload) for payload in payloads]
    expected_draft_ids = [str(snapshot.id) for snapshot in generation.snapshots]
    if any(
        chunk.source_draft_version_ids != expected_version_ids
        or chunk.source_draft_ids != expected_draft_ids
        or chunk.source_session_id != generation.source_session_id
        for chunk in chunks
    ):
        raise ProjectMemoryBatchInvariantError(
            "Stored chunk result does not match the captured draft versions"
        )
    return chunks


def _persist_chunk_result(
    *,
    batch_id: UUID,
    chunk_key: str,
    chunks: list[GeneratedChunkPayload],
    expected_attempt_count: int,
    generation: PendingChunkGeneration,
) -> None:
    expected_version_ids = [str(snapshot.version_id) for snapshot in generation.snapshots]
    stored_result = {
        chunk_key: {
            "draft_version_ids": expected_version_ids,
            "payloads": [_serialize_generated_chunk(chunk) for chunk in chunks],
        }
    }
    db = SessionLocal()
    try:
        now = utc_now()
        result = db.execute(
            update(ProjectMemoryBatch)
            .where(
                ProjectMemoryBatch.id == batch_id,
                ProjectMemoryBatch.status == "running",
                ProjectMemoryBatch.attempt_count == expected_attempt_count,
                ProjectMemoryBatch.lease_expires_at.is_not(None),
                ProjectMemoryBatch.lease_expires_at > now,
            )
            .values(
                chunk_results=ProjectMemoryBatch.chunk_results.op("||")(cast(stored_result, JSONB)),
                lease_expires_at=now + PROJECT_MEMORY_BATCH_LEASE,
                updated_at=now,
            )
        )
        if not result.rowcount:
            raise ProjectMemoryBatchLeaseLostError(
                "Project Memory batch lease was lost before chunk progress was saved"
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _prepare_batch_attempt(
    db: DBSession,
    *,
    batch_id: UUID,
    expected_attempt_count: int,
) -> BatchAttemptSnapshot | None:
    batch = db.get(ProjectMemoryBatch, batch_id)
    now = utc_now()
    if (
        batch is None
        or batch.status != "running"
        or batch.attempt_count != expected_attempt_count
        or batch.lease_expires_at is None
        or batch.lease_expires_at <= now
    ):
        db.commit()
        return None

    rows = _load_batch_snapshots(db, batch)
    sent_to_ai_at = utc_now().isoformat()
    for _item, draft, _snapshot, _session in rows:
        metadata = draft.metadata_ if isinstance(draft.metadata_, dict) else {}
        draft.metadata_ = {
            **metadata,
            "sent_to_ai_at": metadata.get("sent_to_ai_at") or sent_to_ai_at,
        }
        draft.updated_at = utc_now()
    db.flush()
    chunk_generations = _chunk_generations_for_rows(
        db,
        rows,
        grouping_mode=batch.grouping_mode or MEMORY_GROUPING_SESSION,
    )

    project = db.get(Project, batch.project_id)
    if project is None:
        raise ProjectMemoryBatchInvariantError("Project not found")
    stored_chunk_results = batch.chunk_results if isinstance(batch.chunk_results, dict) else {}
    chunk_results = {
        key: _deserialize_chunk_result(stored_chunk_results[key], generation=generation)
        for generation in chunk_generations
        if (key := _chunk_key(generation)) in stored_chunk_results
    }
    prepared = BatchAttemptSnapshot(
        batch_id=batch.id,
        chunk_results=chunk_results,
        chunk_generations=chunk_generations,
        project_id=batch.project_id,
        project_name=project.name,
        snapshot_manifest=list(batch.snapshot_manifest or []),
        source_session_ids=list(batch.source_session_ids or []),
        grouping_mode=batch.grouping_mode or MEMORY_GROUPING_SESSION,
    )
    db.commit()
    return prepared


def _generate_batch_chunks(
    prepared: BatchAttemptSnapshot,
    *,
    expected_attempt_count: int | None = None,
) -> list[GeneratedChunkPayload]:
    ordered_results: list[list[GeneratedChunkPayload] | None] = [
        prepared.chunk_results.get(_chunk_key(generation))
        for generation in prepared.chunk_generations
    ]
    missing = [
        (index, generation)
        for index, (generation, result) in enumerate(
            zip(prepared.chunk_generations, ordered_results, strict=True)
        )
        if result is None
    ]

    def generate(
        index: int, generation: PendingChunkGeneration
    ) -> tuple[int, list[GeneratedChunkPayload]]:
        chunks = _generate_chunk_payloads(
            context=generation.context,
            snapshots=generation.snapshots,
            source_session_id=generation.source_session_id,
        )
        if expected_attempt_count is not None:
            _persist_chunk_result(
                batch_id=prepared.batch_id,
                chunk_key=_chunk_key(generation),
                chunks=chunks,
                expected_attempt_count=expected_attempt_count,
                generation=generation,
            )
        return index, chunks

    if missing:
        concurrency = min(settings.memory_worker_chunk_concurrency, len(missing))
        with ThreadPoolExecutor(
            max_workers=max(1, concurrency),
            thread_name_prefix=f"project-memory-chunk-{prepared.batch_id}",
        ) as executor:
            remaining = iter(missing)
            in_flight = {}

            def submit_next() -> bool:
                try:
                    index, generation = next(remaining)
                except StopIteration:
                    return False
                future = executor.submit(generate, index, generation)
                in_flight[future] = index
                return True

            for _ in range(max(1, concurrency)):
                if not submit_next():
                    break

            try:
                while in_flight:
                    completed, _pending = wait(
                        in_flight,
                        return_when=FIRST_COMPLETED,
                    )
                    completed.update(future for future in in_flight if future.done())
                    for future in sorted(
                        completed,
                        key=in_flight.__getitem__,
                    ):
                        index, chunks = future.result()
                        ordered_results[index] = chunks
                    for future in completed:
                        del in_flight[future]
                    for _ in completed:
                        if not submit_next():
                            break
            except BaseException:
                for future in in_flight:
                    future.cancel()
                raise

    return [chunk for result in ordered_results if result is not None for chunk in result]


def _unique_strings(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def _consolidated_sections(
    chunks: list[GeneratedChunkPayload],
) -> list[dict[str, str]]:
    summaries_by_title: dict[str, list[str]] = {}
    for chunk in chunks:
        sections = (
            chunk.payload.get("sections") if isinstance(chunk.payload.get("sections"), list) else []
        )
        for section in sections:
            if not isinstance(section, dict):
                continue
            title = section.get("title")
            summary = section.get("summary")
            if not isinstance(title, str) or not isinstance(summary, str):
                continue
            values = summaries_by_title.setdefault(title, [])
            full_summary = summary.strip()
            if full_summary and full_summary not in values:
                values.append(full_summary)
    return [
        {
            "summary": " / ".join(summaries),
            "title": title,
        }
        for title, summaries in summaries_by_title.items()
        if summaries
    ]


def _prepare_project_batch_memory(
    *,
    batch: BatchAttemptSnapshot,
    chunks: list[GeneratedChunkPayload],
) -> PreparedBatchMemory:
    changed_files = dedupe_files(
        [
            file
            for chunk in chunks
            for file in (
                chunk.payload.get("changed_files")
                if isinstance(chunk.payload.get("changed_files"), list)
                else []
            )
            if isinstance(file, dict) and isinstance(file.get("path"), str)
        ]
    )
    summaries = _unique_strings([chunk.payload.get("summary") for chunk in chunks])
    outcomes = _unique_strings([chunk.payload.get("outcome") for chunk in chunks])
    prompt_event_ids = _unique_strings(
        [
            event_id
            for chunk in chunks
            for event_id in (
                chunk.payload.get("prompt_event_ids")
                if isinstance(chunk.payload.get("prompt_event_ids"), list)
                else []
            )
        ]
    )
    source_draft_ids = _unique_strings(
        [draft_id for chunk in chunks for draft_id in chunk.source_draft_ids]
    )
    source_draft_version_ids = _unique_strings(
        [version_id for chunk in chunks for version_id in chunk.source_draft_version_ids]
    )
    first_event_at = min(
        (chunk.first_event_at for chunk in chunks if chunk.first_event_at),
        default=None,
    )
    last_event_at = max(
        (chunk.last_event_at for chunk in chunks if chunk.last_event_at),
        default=None,
    )
    first_event_ids = _unique_strings([chunk.payload.get("first_event_id") for chunk in chunks])
    last_event_ids = _unique_strings([chunk.payload.get("last_event_id") for chunk in chunks])
    tools = _unique_strings([chunk.payload.get("tool") for chunk in chunks])
    models = _unique_strings([chunk.payload.get("model") for chunk in chunks])
    commit_shas = _unique_strings([chunk.payload.get("commit_sha") for chunk in chunks])
    tags = _unique_strings(
        [
            tag
            for chunk in chunks
            for tag in (
                chunk.payload.get("tags") if isinstance(chunk.payload.get("tags"), list) else []
            )
        ]
    )
    technologies = _unique_strings(
        [
            technology
            for chunk in chunks
            for technology in (
                chunk.payload.get("technologies")
                if isinstance(chunk.payload.get("technologies"), list)
                else []
            )
        ]
    )
    payload = {
        "changed_files": changed_files[:100],
        "commit_sha": commit_shas[-1] if commit_shas else None,
        "event_count": sum(
            value
            for chunk in chunks
            if isinstance((value := chunk.payload.get("event_count")), int)
        ),
        "first_event_id": first_event_ids[0] if first_event_ids else None,
        "generator": PROJECT_MEMORY_BATCH_GENERATOR,
        "last_event_id": last_event_ids[-1] if last_event_ids else None,
        "model": models[0] if len(models) == 1 else "multiple" if models else None,
        "outcome": " ".join(outcomes),
        "prompt_event_ids": prompt_event_ids,
        "reason": "Created from the captured work in this project update.",
        "sections": _consolidated_sections(chunks),
        "summary": " ".join(summaries),
        "tags": _unique_strings([*tags, "project-update"])[:12],
        "technologies": technologies[:20],
        "title": f"{batch.project_name} memory update",
        "tool": tools[0] if len(tools) == 1 else "multiple" if tools else "promty",
    }
    batch_source_draft_ids = _unique_strings(
        [item.get("draft_id") for item in batch.snapshot_manifest or [] if isinstance(item, dict)]
    )
    timestamp = utc_now()
    return PreparedBatchMemory(
        artifact_id=uuid5(
            NAMESPACE_URL,
            f"promty:project-memory-batch:{batch.batch_id}:memory",
        ),
        event_id=_event_id(payload["last_event_id"]),
        extra_metadata={
            "artifact_stage": "generated_memory",
            "batch_source_draft_ids": batch_source_draft_ids,
            "draft_generator": PROJECT_MEMORY_BATCH_GENERATOR,
            "draft_type": "work_log",
            "first_event_at": first_event_at,
            "grouping_mode": batch.grouping_mode,
            "internal_chunk_count": len(chunks),
            "last_event_at": last_event_at,
            "memory_batch_id": str(batch.batch_id),
            "memory_scope": "generated",
            "prompt_count": len(prompt_event_ids),
            "review_state": REVIEW_STATE_GENERATED,
            "source_chunk_ids": source_draft_ids,
            "source_draft_ids": source_draft_ids,
            "source_draft_version_ids": source_draft_version_ids,
            "source_session_ids": list(batch.source_session_ids or []),
            "summary_level": 2,
            "trigger_reason": PROJECT_MEMORY_BATCH_TRIGGER,
        },
        project_id=batch.project_id,
        payload=payload,
        storage_key=f"memory/project/{batch.project_id}/batch/{batch.batch_id}/memory",
        timestamp=timestamp,
    )


def _prepared_batch_memory_context(memory: PreparedBatchMemory) -> dict[str, Any]:
    metadata = memory.extra_metadata
    payload = memory.payload
    return {
        "changed_file_count": len(payload.get("changed_files") or []),
        "created_at": memory.timestamp.isoformat(),
        "draft_details": metadata.get("draft_details"),
        "draft_type": metadata.get("draft_type"),
        "first_event_at": metadata.get("first_event_at"),
        "id": str(memory.artifact_id),
        "last_event_at": metadata.get("last_event_at"),
        "memory_batch_id": metadata.get("memory_batch_id"),
        "memory_scope": metadata.get("memory_scope"),
        "outcome": payload.get("outcome"),
        "reason": payload.get("reason"),
        "sections": payload.get("sections") or [],
        "session_id": None,
        "source_draft_id": metadata.get("source_draft_id"),
        "source_draft_ids": metadata.get("source_draft_ids") or [],
        "source_draft_version_ids": metadata.get("source_draft_version_ids") or [],
        "source_session_ids": metadata.get("source_session_ids") or [],
        "summary": payload.get("summary"),
        "tags": payload.get("tags") or [],
        "technologies": payload.get("technologies") or [],
        "title": payload.get("title"),
        "updated_at": memory.timestamp.isoformat(),
    }


def _write_project_batch_memory(
    db: DBSession,
    memory: PreparedBatchMemory,
) -> Artifact:
    return write_memory_artifact_payload(
        db,
        artifact_id=memory.artifact_id,
        artifact_type=MEMORY_ARTIFACT_TYPE,
        event_id=memory.event_id,
        extra_metadata=memory.extra_metadata,
        payload=memory.payload,
        project_id=memory.project_id,
        session_id=None,
        storage_key=memory.storage_key,
    )


def _consume_batch_snapshots(
    db: DBSession,
    *,
    batch: ProjectMemoryBatch,
    generated_chunks: list[GeneratedChunkPayload],
    memory: Artifact | None,
    rows: list[tuple[ProjectMemoryBatchItem, Artifact, PendingDraftSnapshot, Session]],
) -> None:
    contributed_draft_ids = {
        UUID(draft_id) for chunk in generated_chunks for draft_id in chunk.source_draft_ids
    }
    generated_ids = [str(memory.id)] if memory is not None else []
    consumed_at = utc_now()
    for _item, draft, _snapshot, _session in rows:
        metadata = draft.metadata_ if isinstance(draft.metadata_, dict) else {}
        draft_generated_ids = generated_ids if draft.id in contributed_draft_ids else []
        draft.metadata_ = {
            **metadata,
            "generated_memory_ids": draft_generated_ids,
            "memory_batch_id": str(batch.id),
            "review_state": (
                REVIEW_STATE_GENERATED if draft_generated_ids else REVIEW_STATE_IGNORED
            ),
            "sent_to_ai_at": consumed_at.isoformat(),
        }
        draft.updated_at = consumed_at
    db.flush()


def _finalize_batch_attempt(
    db: DBSession,
    *,
    expected_attempt_count: int,
    generated_chunks: list[GeneratedChunkPayload],
    prepared: BatchAttemptSnapshot,
    prepared_memory: PreparedBatchMemory | None,
    prepared_project_memory: Any | None,
) -> ProjectMemoryBatch:
    lock_project_memory(db, prepared.project_id)
    batch = db.execute(
        select(ProjectMemoryBatch)
        .where(ProjectMemoryBatch.id == prepared.batch_id)
        .with_for_update()
    ).scalar_one()
    now = utc_now()
    if (
        batch.status != "running"
        or batch.attempt_count != expected_attempt_count
        or batch.lease_expires_at is None
        or batch.lease_expires_at <= now
    ):
        db.commit()
        return batch
    if (
        batch.project_id != prepared.project_id
        or list(batch.snapshot_manifest or []) != prepared.snapshot_manifest
    ):
        raise ProjectMemoryBatchInvariantError("Batch snapshot changed during generation")

    rows = _load_batch_snapshots(db, batch, lock_drafts=True)
    if prepared_project_memory is not None and (
        project_memory_compilation_guard(db, prepared.project_id)
        != prepared_project_memory.base_guard
    ):
        raise ProjectMemoryBatchGenerationError(
            "Project Memory changed during generation; retry the same captured work"
        )

    memory = (
        _write_project_batch_memory(db, prepared_memory) if prepared_memory is not None else None
    )
    _consume_batch_snapshots(
        db,
        batch=batch,
        generated_chunks=generated_chunks,
        memory=memory,
        rows=rows,
    )
    project_memory = (
        write_project_memory_compilation(db, prepared_project_memory)
        if prepared_project_memory is not None
        else None
    )

    batch.status = "succeeded"
    batch.result_status = "memory_generated" if memory is not None else "no_memory"
    batch.generated_artifact_ids = [str(memory.id)] if memory is not None else []
    batch.project_memory_artifact_id = project_memory.id if project_memory else None
    batch.chunk_results = {}
    batch.error_code = None
    batch.error_message = None
    batch.lease_expires_at = None
    batch.completed_at = now
    batch.updated_at = now
    db.flush()
    db.commit()
    return batch


def _record_batch_failure(
    db: DBSession,
    *,
    batch_id: UUID,
    error: Exception,
    expected_attempt_count: int,
) -> ProjectMemoryBatch:
    batch = db.execute(
        select(ProjectMemoryBatch).where(ProjectMemoryBatch.id == batch_id).with_for_update()
    ).scalar_one()
    if batch.status != "running" or batch.attempt_count != expected_attempt_count:
        db.commit()
        return batch
    now = utc_now()
    is_invariant_failure = isinstance(error, ProjectMemoryBatchInvariantError)
    batch.status = "superseded" if is_invariant_failure else "failed"
    batch.result_status = "generation_failed"
    batch.error_code = _batch_failure_code(error)
    batch.error_message = _safe_batch_failure_message(error)
    batch.lease_expires_at = None
    batch.completed_at = now
    batch.updated_at = now
    batch.chunk_results = {}
    if is_invariant_failure:
        db.execute(
            delete(ProjectMemoryBatchItem).where(ProjectMemoryBatchItem.batch_id == batch.id)
        )
    else:
        draft_ids = select(ProjectMemoryBatchItem.draft_id).where(
            ProjectMemoryBatchItem.batch_id == batch.id
        )
        db.execute(
            update(Artifact)
            .where(Artifact.id.in_(draft_ids))
            .values(
                metadata_=Artifact.metadata_.op("||")(
                    cast(
                        {
                            "review_state": REVIEW_STATE_DRAFT,
                            "sent_to_ai_at": None,
                        },
                        JSONB,
                    )
                ),
                updated_at=now,
            )
        )
        db.execute(
            delete(ProjectMemoryBatchItem).where(
                ProjectMemoryBatchItem.batch_id == batch.id
            )
        )
    db.flush()
    db.commit()
    return batch


def _batch_failure_code(error: Exception) -> str:
    return error.code if isinstance(error, ProjectMemoryBatchError) else "generation_failed"


def _safe_batch_failure_message(error: Exception) -> str:
    if isinstance(error, ProjectMemoryBatchProviderUnavailableError):
        return "AI generation is not configured. Captured work is safe; try again shortly."
    if isinstance(error, ProjectMemoryBatchLeaseLostError):
        return "Project Memory batch lease was lost."
    if isinstance(error, ProjectMemoryBatchInvariantError):
        return "Project Memory batch snapshot is invalid."
    status_match = HTTP_STATUS_PATTERN.search(str(error))
    if status_match is not None:
        return f"Memory provider request failed with HTTP status {status_match.group(1)}."
    return "Project Memory generation failed."


def _execute_claimed_batch(
    db: DBSession,
    *,
    batch_id: UUID,
    attempt_count: int,
) -> ProjectMemoryBatch:
    try:
        prepared = _prepare_batch_attempt(
            db,
            batch_id=batch_id,
            expected_attempt_count=attempt_count,
        )
        if prepared is None:
            current = db.get(ProjectMemoryBatch, batch_id)
            if current is None:
                raise ProjectMemoryBatchInvariantError("Project Memory batch not found")
            return current

        generated_chunks = _generate_batch_chunks(
            prepared,
            expected_attempt_count=attempt_count,
        )
        prepared_memory = (
            _prepare_project_batch_memory(batch=prepared, chunks=generated_chunks)
            if generated_chunks
            else None
        )
        prepared_project_memory = None
        if prepared_memory is not None:
            compilation_input = prepare_project_memory_compilation(
                db,
                project_id=prepared.project_id,
                required_source_memory_contexts=[_prepared_batch_memory_context(prepared_memory)],
            )
            db.commit()
            prepared_project_memory = generate_project_memory_compilation(compilation_input)
        batch = _finalize_batch_attempt(
            db,
            expected_attempt_count=attempt_count,
            generated_chunks=generated_chunks,
            prepared=prepared,
            prepared_memory=prepared_memory,
            prepared_project_memory=prepared_project_memory,
        )
    except Exception as exc:
        db.rollback()
        logger.error(
            "Project Memory batch %s failed error_code=%s error_type=%s",
            batch_id,
            _batch_failure_code(exc),
            type(exc).__name__,
        )
        batch = _record_batch_failure(
            db,
            batch_id=batch_id,
            error=exc,
            expected_attempt_count=attempt_count,
        )
    return batch


def run_project_memory_batch(
    db: DBSession,
    *,
    batch_id: UUID,
    replayed: bool = False,
) -> dict[str, Any]:
    batch, claimed, attempt_count = _claim_batch_run(db, batch_id)
    if not claimed:
        return serialize_project_memory_batch(db, batch, replayed=True)
    with _batch_lease_heartbeat(batch_id, attempt_count):
        batch = _execute_claimed_batch(
            db,
            batch_id=batch_id,
            attempt_count=attempt_count,
        )
    return serialize_project_memory_batch(db, batch, replayed=replayed)


def next_project_memory_batch_id(db: DBSession) -> UUID | None:
    now = utc_now()
    expired_batch_ids = select(ProjectMemoryBatch.id).where(
        ProjectMemoryBatch.status == "running",
        ProjectMemoryBatch.lease_expires_at.is_not(None),
        ProjectMemoryBatch.lease_expires_at <= now,
    )
    expired_draft_ids = (
        select(ProjectMemoryBatchItem.draft_id)
        .join(
            ProjectMemoryBatch,
            ProjectMemoryBatch.id == ProjectMemoryBatchItem.batch_id,
        )
        .where(ProjectMemoryBatch.id.in_(expired_batch_ids))
    )
    db.execute(
        update(Artifact)
        .where(Artifact.id.in_(expired_draft_ids))
        .values(
            metadata_=Artifact.metadata_.op("||")(
                cast(
                    {
                        "review_state": REVIEW_STATE_GENERATION_FAILED,
                        "sent_to_ai_at": now.isoformat(),
                    },
                    JSONB,
                )
            ),
            updated_at=now,
        )
    )
    db.execute(
        update(ProjectMemoryBatch)
        .where(ProjectMemoryBatch.id.in_(expired_batch_ids))
        .values(
            chunk_results={},
            completed_at=now,
            error_code="generation_interrupted",
            error_message="Project Memory generation was interrupted and will not be retried.",
            lease_expires_at=None,
            result_status="generation_failed",
            status="failed",
            updated_at=now,
        )
    )
    return db.scalar(
        select(ProjectMemoryBatch.id)
        .where(ProjectMemoryBatch.status == "pending")
        .order_by(ProjectMemoryBatch.created_at, ProjectMemoryBatch.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def run_next_project_memory_batch(db: DBSession) -> bool:
    batch_id = next_project_memory_batch_id(db)
    db.commit()
    if batch_id is None:
        return False
    run_project_memory_batch(db, batch_id=batch_id)
    return True


def _queued_batch_response(
    db: DBSession,
    batch: ProjectMemoryBatch,
    *,
    replayed: bool,
) -> dict[str, Any]:
    response = serialize_project_memory_batch(db, batch, replayed=replayed)
    db.commit()
    return response


def generate_project_memory_batch(
    db: DBSession,
    *,
    idempotency_key: str,
    project_id: UUID,
    user_id: UUID,
    excluded_prompt_event_ids: list[UUID] | None = None,
    review_token: str = "",
) -> dict[str, Any]:
    in_progress = _in_progress_batch_by_idempotency_key(
        db,
        idempotency_key=idempotency_key,
        project_id=project_id,
    )
    if in_progress is not None:
        in_progress = _attach_idempotency_key(
            db,
            in_progress,
            idempotency_key,
            update_legacy_keys=False,
        )
        return _queued_batch_response(db, in_progress, replayed=True)

    visible_active = _visible_active_batch(db, project_id=project_id)
    if visible_active is not None:
        mapped = _attach_idempotency_key(
            db,
            visible_active,
            idempotency_key,
            update_legacy_keys=False,
        )
        return _queued_batch_response(db, mapped, replayed=True)

    lock_project_memory(db, project_id)
    existing = _batch_by_idempotency_key(
        db,
        idempotency_key=idempotency_key,
        project_id=project_id,
    )
    if existing is not None:
        existing = _attach_idempotency_key(db, existing, idempotency_key)
        return _queued_batch_response(db, existing, replayed=True)

    active = _active_batch(db, project_id=project_id)
    if active is not None:
        active = _attach_idempotency_key(db, active, idempotency_key)
        return _queued_batch_response(db, active, replayed=True)

    batch = _prepare_batch(
        db,
        idempotency_key=idempotency_key,
        project_id=project_id,
        user_id=user_id,
        excluded_prompt_event_ids=excluded_prompt_event_ids,
        review_token=review_token,
    )
    if batch.status == "succeeded":
        return serialize_project_memory_batch(db, batch, replayed=False)
    return serialize_project_memory_batch(db, batch, replayed=False)
