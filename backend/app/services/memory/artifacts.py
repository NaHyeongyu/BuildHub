from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Integer, cast, desc, func, or_, select
from sqlalchemy.orm import Session as DBSession

from app.core.config import settings
from app.core.time import utc_now
from app.models.artifact_generation_jobs import ArtifactGenerationJob
from app.models.artifacts import Artifact
from app.models.projects import Project
from app.models.sessions import Session
from app.services.memory.context import (
    build_pending_memory_draft_payload as _build_pending_memory_draft_payload,
    build_session_memory_context as _build_session_memory_context,
    dedupe_files as _dedupe_files,
    is_generic_local_memory_summary as _is_generic_local_memory_summary,
    iso as _iso,
    pending_draft_evidence_from_context as _pending_draft_evidence_from_context,
)
from app.services.memory.draft_payloads import (
    build_memory_draft_payloads_from_context as _build_memory_draft_payloads_from_context,
)
from app.services.memory.providers import (
    generator_for_provider as _generator_for_provider,
    provider_name as _provider_name,
)
from app.services.memory.session_completion import complete_session_if_ready
from app.services.memory.project_memory import (
    approve_project_memory_snapshot as approve_project_memory_snapshot,
    compile_project_memory as compile_project_memory,
    count_project_memory_artifacts as count_project_memory_artifacts,
    count_project_memory_history_artifacts as count_project_memory_history_artifacts,
    get_latest_project_memory as get_latest_project_memory,
    list_project_memory_history_artifacts as list_project_memory_history_artifacts,
    list_project_memory_artifacts as list_project_memory_artifacts,
    update_project_memory_snapshot as update_project_memory_snapshot,
)
from app.services.memory.windows import (
    due_memory_window as _due_memory_window,
    latest_session_event as _latest_session_event,
    memory_slice_prompt_target as _memory_slice_prompt_target,
    memory_slice_runtime_state as _memory_slice_runtime_state,
)
from app.services.memory.repository import (
    payload_from_artifact as _payload_from_artifact,
    write_memory_artifact_payload as _write_memory_artifact_payload,
)
from app.services.memory.constants import (
    MEMORY_ARTIFACT_TYPE,
    MEMORY_DRAFT_ARTIFACT_TYPE,
    MEMORY_WINDOW_STRATEGY,
    PENDING_DRAFT_STAGE,
    REVIEW_STATE_DRAFT,
    REVIEW_STATE_GENERATED,
    REVIEW_STATE_IGNORED,
    REVIEW_STATE_SAVED,
    REVIEW_STATE_VERIFIED,
    SESSION_IDLE_COMPLETE_AFTER,
)
from app.services.memory.serializers import (
    serialize_artifact_version as serialize_artifact_version,
    serialize_generation_job as serialize_generation_job,
    serialize_memory_artifact as serialize_memory_artifact,
    serialize_memory_artifact_summary as serialize_memory_artifact_summary,
)


def _artifact_is_current_for_context(artifact: Artifact, context: dict[str, Any]) -> bool:
    if not context["last_event_id"] or not artifact.summary:
        return False
    if not isinstance(artifact.sections, list) or not artifact.sections:
        return False
    metadata = artifact.metadata_ if isinstance(artifact.metadata_, dict) else {}
    return (
        metadata.get("last_event_id") == context["last_event_id"]
        and metadata.get("event_count") == context["event_count"]
    )


def create_artifact_generation_job(
    db: DBSession,
    *,
    project_id: UUID,
    reason: str,
    session_id: UUID,
) -> ArtifactGenerationJob:
    job = ArtifactGenerationJob(
        project_id=project_id,
        session_id=session_id,
        reason=reason,
        status="pending",
        generator=_generator_for_provider(
            _provider_name(settings.memory_draft_generator),
            stage="draft",
        ),
    )
    db.add(job)
    db.flush()
    return job


def run_artifact_generation_job(
    db: DBSession,
    job: ArtifactGenerationJob,
) -> ArtifactGenerationJob:
    job.status = "running"
    job.updated_at = utc_now()
    db.flush()

    try:
        # The job outcome intentionally survives a generation failure, but all
        # memory writes must remain atomic. In particular, rolling back this
        # savepoint restores a cleared resume marker and removes partial slices.
        with db.begin_nested():
            session = db.get(Session, job.session_id)
            if session is None:
                raise ValueError("Session not found")
            generate_due_memory_artifacts_for_session(
                db,
                session,
                finalize=True,
            )
            artifacts = generate_context_memories_for_session(
                db,
                session,
                trigger_reason=job.reason,
            )
            if not artifacts:
                result_artifact_id = None
                result_generator = None
                result_metadata = {
                    "reason": "No pending memory draft was generated.",
                    "status": "no_memory",
                }
            else:
                artifact = artifacts[0]
                result_artifact_id = artifact.id
                result_generator = artifact.generator
                result_metadata = {
                    "generated_memory_ids": [str(item.id) for item in artifacts],
                    **(artifact.metadata_ if isinstance(artifact.metadata_, dict) else {}),
                }

        job.artifact_id = result_artifact_id
        job.generator = result_generator or job.generator
        job.metadata_ = result_metadata
        job.status = "succeeded"
        job.completed_at = utc_now()
        job.error = None
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.completed_at = utc_now()
    finally:
        job.updated_at = utc_now()
        db.flush()

    return job


def _generate_pending_draft_for_context(
    db: DBSession,
    *,
    context: dict[str, Any],
    session: Session,
    storage_key: str,
) -> Artifact:
    artifact = db.execute(
        select(Artifact).where(
            Artifact.project_id == session.project_id,
            Artifact.session_id == session.id,
            Artifact.type == MEMORY_DRAFT_ARTIFACT_TYPE,
            Artifact.storage_key == storage_key,
        )
    ).scalar_one_or_none()
    if artifact is not None and _artifact_is_current_for_context(artifact, context):
        return artifact

    evidence = _pending_draft_evidence_from_context(context)
    payload = _build_pending_memory_draft_payload(context, evidence=evidence)
    slice_metadata = context.get("slice") if isinstance(context.get("slice"), dict) else {}
    return _write_memory_artifact_payload(
        db,
        artifact_type=MEMORY_DRAFT_ARTIFACT_TYPE,
        event_id=UUID(payload["last_event_id"]) if payload["last_event_id"] else None,
        extra_metadata={
            "artifact_stage": PENDING_DRAFT_STAGE,
            "commit_metadata": evidence.get("commits", []),
            "draft_evidence": evidence,
            "event_count": payload["event_count"],
            "first_event_id": payload["first_event_id"],
            "last_event_id": payload["last_event_id"],
            "memory_scope": "pending_draft",
            "review_state": REVIEW_STATE_DRAFT,
            "summary_level": 1,
            **slice_metadata,
        },
        payload=payload,
        project_id=session.project_id,
        session_id=session.id,
        storage_key=storage_key,
    )


def _clear_memory_resume_marker(db: DBSession, session: Session) -> None:
    # Normal materialization keeps at most one marker per session: the prior
    # marker is cleared before work and only the newest capped artifact is
    # marked again in the same transaction.
    artifact = db.execute(
        select(Artifact)
        .where(
            Artifact.project_id == session.project_id,
            Artifact.session_id == session.id,
            Artifact.type == MEMORY_DRAFT_ARTIFACT_TYPE,
            Artifact.metadata_["artifact_stage"].astext == PENDING_DRAFT_STAGE,
            Artifact.metadata_["memory_strategy"].astext == MEMORY_WINDOW_STRATEGY,
            Artifact.metadata_["memory_resume_required"].astext == "true",
        )
        .order_by(
            desc(cast(Artifact.metadata_["end_sequence"].astext, Integer)),
            desc(Artifact.updated_at),
        )
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    if artifact is None:
        return
    metadata = artifact.metadata_ if isinstance(artifact.metadata_, dict) else {}
    updated_metadata = {**metadata}
    updated_metadata.pop("memory_resume_required", None)
    artifact.metadata_ = updated_metadata
    artifact.updated_at = utc_now()


def _lock_memory_materialization_session(db: DBSession, session: Session) -> None:
    """Serialize every writer of the monotonic per-session slice cursor."""

    locked_session_id = db.execute(
        select(Session.id)
        .where(
            Session.id == session.id,
            Session.project_id == session.project_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if locked_session_id is None:
        raise ValueError("Session not found")


def generate_due_memory_artifacts_for_session(
    db: DBSession,
    session: Session,
    *,
    finalize: bool = False,
) -> list[Artifact]:
    _lock_memory_materialization_session(db, session)
    generated_artifacts: list[Artifact] = []
    (
        after_sequence,
        next_slice_index,
        continuation_end_sequence,
        resume_required,
    ) = _memory_slice_runtime_state(db, session)
    if resume_required:
        _clear_memory_resume_marker(db, session)

    slice_limit = max(settings.memory_slice_max_slices_per_call, 1)
    while len(generated_artifacts) < slice_limit:
        window = _due_memory_window(
            db,
            session,
            after_sequence=after_sequence,
            continuation_end_sequence=continuation_end_sequence,
            finalize=finalize,
        )
        if window is None:
            break

        selected_prompts = window["selected_prompts"]
        start_prompt_sequence = selected_prompts[0].sequence if selected_prompts else None
        end_prompt_sequence = selected_prompts[-1].sequence if selected_prompts else None

        slice_metadata = {
            "context_prompt_sequence": (
                window["context_prompt"].sequence if window["context_prompt"] is not None else None
            ),
            "end_prompt_sequence": end_prompt_sequence,
            "end_sequence": window["end_sequence"],
            "event_row_limit": window["event_row_limit"],
            "materialization_end_sequence": window["materialization_end_sequence"],
            "memory_slice_kind": window.get("memory_slice_kind") or "continuation",
            "memory_strategy": MEMORY_WINDOW_STRATEGY,
            "prompt_count": len(selected_prompts),
            "slice_index": next_slice_index,
            "start_prompt_sequence": start_prompt_sequence,
            "start_sequence": window["start_sequence"],
            "target_prompt_count": _memory_slice_prompt_target(),
            "window_reason": window["reason"],
            "window_truncated": window["window_truncated"],
        }
        artifact = _generate_pending_draft_for_context(
            db,
            context=_build_session_memory_context(
                db,
                session,
                context_event_rows=(
                    [window["context_prompt"]] if window["context_prompt"] is not None else None
                ),
                end_sequence=window["end_sequence"],
                event_rows=window["events"],
                slice_metadata=slice_metadata,
                start_sequence=window["start_sequence"],
            ),
            session=session,
            storage_key=(
                f"memory/session/{session.id}/pending/"
                f"{window['start_sequence']}-{window['end_sequence']}"
            ),
        )
        generated_artifacts.append(artifact)
        after_sequence = window["end_sequence"]
        continuation_end_sequence = (
            window["materialization_end_sequence"]
            if after_sequence < window["materialization_end_sequence"]
            else None
        )
        next_slice_index += 1

    if len(generated_artifacts) == slice_limit:
        last_artifact = generated_artifacts[-1]
        metadata = last_artifact.metadata_ if isinstance(last_artifact.metadata_, dict) else {}
        last_artifact.metadata_ = {
            **metadata,
            "memory_resume_required": True,
        }
        last_artifact.updated_at = utc_now()

    return generated_artifacts


def _pending_memory_drafts_for_session(
    db: DBSession,
    session: Session,
    *,
    end_sequence: int | None = None,
    start_sequence: int | None = None,
) -> list[Artifact]:
    artifacts = list(
        db.execute(
            select(Artifact)
            .where(
                Artifact.project_id == session.project_id,
                Artifact.session_id == session.id,
                Artifact.type == MEMORY_DRAFT_ARTIFACT_TYPE,
                Artifact.metadata_["artifact_stage"].astext == PENDING_DRAFT_STAGE,
                Artifact.metadata_["review_state"].astext == REVIEW_STATE_DRAFT,
                Artifact.metadata_["sent_to_ai_at"].astext.is_(None),
            )
            .order_by(Artifact.created_at, Artifact.updated_at)
        ).scalars()
    )
    pending: list[Artifact] = []
    for artifact in artifacts:
        metadata = artifact.metadata_ if isinstance(artifact.metadata_, dict) else {}
        draft_start = metadata.get("start_sequence")
        draft_end = metadata.get("end_sequence")
        if start_sequence is not None and isinstance(draft_end, int) and draft_end < start_sequence:
            continue
        if end_sequence is not None and isinstance(draft_start, int) and draft_start > end_sequence:
            continue
        pending.append(artifact)
    return pending


def _evidence_event_range(evidence: dict[str, Any]) -> tuple[str | None, str | None]:
    events = evidence.get("events") if isinstance(evidence.get("events"), list) else []
    timestamps = sorted(
        timestamp
        for event in events
        if isinstance(event, dict)
        and isinstance((timestamp := event.get("timestamp")), str)
        and timestamp
    )
    if timestamps:
        return timestamps[0], timestamps[-1]
    return None, None


def _pending_draft_range(artifact: Artifact) -> dict[str, Any]:
    metadata = artifact.metadata_ if isinstance(artifact.metadata_, dict) else {}
    evidence = (
        metadata.get("draft_evidence") if isinstance(metadata.get("draft_evidence"), dict) else {}
    )
    prompts = evidence.get("prompts") if isinstance(evidence.get("prompts"), list) else []
    responses = evidence.get("responses") if isinstance(evidence.get("responses"), list) else []
    changed_files = (
        evidence.get("changed_files") if isinstance(evidence.get("changed_files"), list) else []
    )
    events = evidence.get("events") if isinstance(evidence.get("events"), list) else []
    file_change_event_count = sum(
        1
        for event in events
        if isinstance(event, dict) and event.get("event_type") == "FilesChanged"
    )
    if not file_change_event_count and changed_files:
        file_change_event_count = 1
    first_event_at, last_event_at = _evidence_event_range(evidence)
    return {
        "can_checkpoint": True,
        "draft_id": str(artifact.id),
        "end_sequence": metadata.get("end_sequence"),
        "event_count": metadata.get("event_count") or 0,
        "file_change_event_count": file_change_event_count,
        "first_event_at": first_event_at or _iso(artifact.created_at),
        "last_event_at": last_event_at or _iso(artifact.updated_at),
        "prompt_count": len(prompts) or len(artifact.prompt_event_ids or []),
        "response_count": len(responses),
        "session_id": str(artifact.session_id),
        "start_sequence": metadata.get("start_sequence"),
        "tool": metadata.get("tool") or "unknown",
        "changed_file_count": len(changed_files),
    }


def _latest_event_sequence(db: DBSession, session: Session) -> int | None:
    latest_event = _latest_session_event(db, session)
    return latest_event.sequence if latest_event is not None else None


def _pending_draft_generation_context(
    db: DBSession,
    session: Session,
    drafts: list[Artifact],
) -> dict[str, Any]:
    start_sequences = []
    end_sequences = []
    pending_drafts: list[dict[str, Any]] = []
    changed_files: list[dict[str, Any]] = []
    commits: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    prompt_events: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    first_event_id = None
    last_event_id = None

    for draft in drafts:
        metadata = draft.metadata_ if isinstance(draft.metadata_, dict) else {}
        evidence = (
            metadata.get("draft_evidence")
            if isinstance(metadata.get("draft_evidence"), dict)
            else {}
        )
        if isinstance(metadata.get("start_sequence"), int):
            start_sequences.append(metadata["start_sequence"])
        if isinstance(metadata.get("end_sequence"), int):
            end_sequences.append(metadata["end_sequence"])
        if isinstance(metadata.get("first_event_id"), str) and first_event_id is None:
            first_event_id = metadata["first_event_id"]
        if isinstance(metadata.get("last_event_id"), str):
            last_event_id = metadata["last_event_id"]
        draft_changed_files = (
            evidence.get("changed_files") if isinstance(evidence.get("changed_files"), list) else []
        )
        draft_commits = evidence.get("commits") if isinstance(evidence.get("commits"), list) else []
        draft_events = evidence.get("events") if isinstance(evidence.get("events"), list) else []
        draft_prompts = evidence.get("prompts") if isinstance(evidence.get("prompts"), list) else []
        draft_responses = (
            evidence.get("responses") if isinstance(evidence.get("responses"), list) else []
        )
        changed_files.extend(file for file in draft_changed_files if isinstance(file, dict))
        commits.extend(commit for commit in draft_commits if isinstance(commit, dict))
        events.extend(event for event in draft_events if isinstance(event, dict))
        prompt_events.extend(
            {
                "context_only": prompt.get("context_only") is True,
                "id": prompt.get("event_id"),
                "prompt": (
                    (prompt.get("ai_input") or {}).get("text")
                    if isinstance(prompt.get("ai_input"), dict)
                    else prompt.get("original_input")
                ),
                "prompt_original": (
                    prompt.get("original_input")
                    or (
                        (prompt.get("ai_input") or {}).get("text")
                        if isinstance(prompt.get("ai_input"), dict)
                        else None
                    )
                ),
                "prompt_original_size": prompt.get("original_length"),
                "prompt_ai_preview_truncated": (
                    (prompt.get("ai_input") or {}).get("truncated_for_ai") is True
                    if isinstance(prompt.get("ai_input"), dict)
                    else False
                ),
                "sequence": prompt.get("sequence"),
                "turn_id": prompt.get("turn_id"),
            }
            for prompt in draft_prompts
            if isinstance(prompt, dict)
        )
        responses.extend(
            {
                "context_only": response.get("context_only") is True,
                "id": response.get("event_id"),
                "response": response.get("output_preview") or response.get("original_output"),
                "response_original": response.get("output_preview")
                or response.get("original_output"),
                "response_original_size": response.get("original_length"),
                "response_ai_preview_truncated": response.get("storage_truncated") is True,
                "sequence": response.get("sequence"),
                "turn_id": response.get("turn_id"),
            }
            for response in draft_responses
            if isinstance(response, dict)
        )
        pending_drafts.append(
            {
                "changed_files": draft_changed_files,
                "evidence": evidence,
                "id": str(draft.id),
                "memory_slice_kind": metadata.get("memory_slice_kind"),
                "summary": draft.summary,
                "title": draft.title,
            }
        )

    project = session.project or db.get(Project, session.project_id)
    output_locale = (
        project.owner.preferred_locale
        if project is not None and project.owner is not None
        else "en"
    )
    return {
        "changed_files": _dedupe_files(changed_files),
        "commits": commits,
        "ended_at": _iso(session.ended_at),
        "event_count": len(events),
        "events": events,
        "first_event_id": first_event_id,
        "last_event_id": last_event_id,
        "model": session.model,
        "output_locale": output_locale,
        "pending_drafts": pending_drafts,
        "project_id": str(session.project_id),
        "project_name": project.name if project else str(session.project_id),
        "prompt_events": prompt_events,
        "response_count": len(responses),
        "responses": responses,
        "session_id": str(session.id),
        "started_at": _iso(session.started_at),
        "tool": session.tool,
        "window": {
            "end_sequence": max(end_sequences) if end_sequences else None,
            "start_sequence": min(start_sequences) if start_sequences else None,
        },
    }


def materialize_project_memory_drafts(
    db: DBSession,
    *,
    limit: int | None = None,
    project_id: UUID,
) -> None:
    idle_before = utc_now() - SESSION_IDLE_COMPLETE_AFTER
    session_query = (
        select(Session)
        .where(
            Session.project_id == project_id,
            or_(
                Session.ended_at.is_not(None),
                Session.last_activity_at <= idle_before,
            ),
        )
        .order_by(desc(Session.started_at))
    )
    if limit is not None:
        session_query = session_query.limit(limit)
    sessions = list(db.execute(session_query).scalars())
    for session in sessions:
        completion = (
            {"completed": True}
            if session.ended_at is not None
            else complete_session_if_ready(db, session, force=False)
        )
        generate_due_memory_artifacts_for_session(
            db,
            session,
            finalize=completion["completed"],
        )


def materialize_next_idle_memory_session(db: DBSession) -> bool:
    """Resume one bounded window group or finalize one idle open session.

    The logical-end aggregate covers partial windows. The operational marker
    covers calls that stop exactly at a completed-window boundary. Both are
    persisted transactionally, so open and ended sessions resume after a
    crash without an external queue flag.
    """
    slice_progress = (
        select(
            Artifact.session_id.label("session_id"),
            func.max(cast(Artifact.metadata_["end_sequence"].astext, Integer)).label(
                "covered_end_sequence"
            ),
            func.max(
                cast(
                    Artifact.metadata_["materialization_end_sequence"].astext,
                    Integer,
                )
            ).label("materialization_end_sequence"),
        )
        .where(
            Artifact.session_id.is_not(None),
            Artifact.type == MEMORY_DRAFT_ARTIFACT_TYPE,
            Artifact.metadata_["artifact_stage"].astext == PENDING_DRAFT_STAGE,
            Artifact.metadata_["memory_strategy"].astext == MEMORY_WINDOW_STRATEGY,
        )
        .group_by(Artifact.session_id)
        .having(
            or_(
                func.max(
                    cast(
                        Artifact.metadata_["materialization_end_sequence"].astext,
                        Integer,
                    )
                )
                > func.max(cast(Artifact.metadata_["end_sequence"].astext, Integer)),
                func.bool_or(Artifact.metadata_["memory_resume_required"].astext == "true"),
            )
        )
        .subquery()
    )
    session = db.execute(
        select(Session)
        .join(slice_progress, slice_progress.c.session_id == Session.id)
        .order_by(Session.started_at, Session.id)
        .limit(1)
        .with_for_update(of=Session, skip_locked=True)
    ).scalar_one_or_none()
    if session is not None:
        # The latest-row runtime state is sufficient for normal serialized
        # writers. Clear defensively here as well so an older marker from
        # manually repaired or pre-invariant data cannot select this session
        # forever after a newer completed slice has been stored.
        _clear_memory_resume_marker(db, session)
        generate_due_memory_artifacts_for_session(
            db,
            session,
            finalize=session.ended_at is not None,
        )
        return True

    idle_before = utc_now() - SESSION_IDLE_COMPLETE_AFTER
    session = db.execute(
        select(Session)
        .where(
            Session.ended_at.is_(None),
            Session.last_activity_at.is_not(None),
            Session.last_activity_at <= idle_before,
        )
        .order_by(
            Session.last_activity_at,
            Session.started_at,
            Session.id,
        )
        .limit(1)
        .with_for_update(of=Session, skip_locked=True)
    ).scalar_one_or_none()
    if session is None:
        return False

    completion = complete_session_if_ready(db, session, force=False)
    if not completion["completed"]:
        return False
    generate_due_memory_artifacts_for_session(
        db,
        session,
        finalize=True,
    )
    return True


def list_project_memory_pending_ranges(
    db: DBSession,
    *,
    limit: int = 20,
    project_id: UUID,
) -> list[dict[str, Any]]:
    pending_drafts = list(
        db.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.type == MEMORY_DRAFT_ARTIFACT_TYPE,
                Artifact.metadata_["artifact_stage"].astext == PENDING_DRAFT_STAGE,
                Artifact.metadata_["review_state"].astext == REVIEW_STATE_DRAFT,
                Artifact.metadata_["sent_to_ai_at"].astext.is_(None),
            )
            .order_by(desc(Artifact.updated_at), desc(Artifact.created_at))
            .limit(limit)
        ).scalars()
    )
    return [_pending_draft_range(draft) for draft in pending_drafts]


def generate_context_memories_for_session(
    db: DBSession,
    session: Session,
    *,
    end_sequence: int | None = None,
    start_sequence: int | None = None,
    trigger_reason: str,
) -> list[Artifact]:
    pending_drafts = _pending_memory_drafts_for_session(
        db,
        session,
        end_sequence=end_sequence,
        start_sequence=start_sequence,
    )
    if not pending_drafts:
        generate_due_memory_artifacts_for_session(
            db,
            session,
            finalize=True,
        )
        pending_drafts = _pending_memory_drafts_for_session(
            db,
            session,
            end_sequence=end_sequence,
            start_sequence=start_sequence,
        )
    if not pending_drafts:
        return []

    context = _pending_draft_generation_context(db, session, pending_drafts)
    covered_start_sequence = context["window"]["start_sequence"]
    covered_end_sequence = context["window"]["end_sequence"]
    latest_sequence = (
        covered_end_sequence
        if isinstance(covered_end_sequence, int)
        else _latest_event_sequence(db, session)
    )
    source_draft_ids = [str(draft.id) for draft in pending_drafts]
    sent_to_ai_at = _iso(utc_now())
    for draft in pending_drafts:
        metadata = draft.metadata_ if isinstance(draft.metadata_, dict) else {}
        draft.metadata_ = {
            **metadata,
            "sent_to_ai_at": metadata.get("sent_to_ai_at") or sent_to_ai_at,
        }
        draft.updated_at = utc_now()
    db.flush()
    payloads, generation_metadata = _build_memory_draft_payloads_from_context(
        context,
        trigger_reason=trigger_reason,
    )
    if not payloads:
        return []
    memories: list[Artifact] = []
    event_id = UUID(context["last_event_id"]) if context["last_event_id"] else None
    for index, (payload, draft_metadata) in enumerate(payloads, start=1):
        storage_key = (
            f"memory/session/{session.id}/generated/{latest_sequence or 0}/{trigger_reason}/{index}"
        )
        existing_memory = db.execute(
            select(Artifact).where(
                Artifact.project_id == session.project_id,
                Artifact.type == MEMORY_ARTIFACT_TYPE,
                Artifact.storage_key == storage_key,
            )
        ).scalar_one_or_none()
        if existing_memory is not None:
            memories.append(existing_memory)
            continue
        memories.append(
            _write_memory_artifact_payload(
                db,
                artifact_type=MEMORY_ARTIFACT_TYPE,
                event_id=event_id,
                extra_metadata={
                    **draft_metadata,
                    **generation_metadata,
                    "artifact_stage": "generated_memory",
                    "commit_metadata": context["commits"],
                    "memory_scope": "generated",
                    "review_state": REVIEW_STATE_GENERATED,
                    "summary_level": 2,
                    "trigger_reason": trigger_reason,
                    "source_draft_ids": source_draft_ids,
                    "source_chunk_ids": source_draft_ids,
                    "start_sequence": covered_start_sequence,
                    "end_sequence": covered_end_sequence,
                },
                payload=payload,
                project_id=session.project_id,
                session_id=session.id,
                storage_key=storage_key,
            )
        )

    generated_ids = [str(memory.id) for memory in memories]
    for draft in pending_drafts:
        metadata = draft.metadata_ if isinstance(draft.metadata_, dict) else {}
        draft.metadata_ = {
            **metadata,
            "generated_memory_ids": generated_ids,
            "review_state": REVIEW_STATE_GENERATED,
            "sent_to_ai_at": _iso(utc_now()),
        }
        draft.updated_at = utc_now()
        db.flush()

    return memories


def update_memory_draft(
    db: DBSession,
    draft: Artifact,
    *,
    updates: dict[str, Any],
) -> Artifact:
    metadata = draft.metadata_ if isinstance(draft.metadata_, dict) else {}
    payload = _payload_from_artifact(draft, overrides=updates)
    return _write_memory_artifact_payload(
        db,
        artifact_type=MEMORY_DRAFT_ARTIFACT_TYPE,
        event_id=draft.event_id,
        extra_metadata={
            **metadata,
            "artifact_stage": "memory_draft",
            "review_state": REVIEW_STATE_DRAFT,
            "summary_level": 2,
            "user_edited": True,
        },
        payload=payload,
        project_id=draft.project_id,
        session_id=draft.session_id,
        storage_key=draft.storage_key,
    )


def save_memory_draft_as_verified(
    db: DBSession,
    draft: Artifact,
    *,
    updates: dict[str, Any] | None = None,
) -> Artifact:
    metadata = draft.metadata_ if isinstance(draft.metadata_, dict) else {}
    if updates:
        draft = update_memory_draft(db, draft, updates=updates)
        metadata = draft.metadata_ if isinstance(draft.metadata_, dict) else {}
    payload = _payload_from_artifact(draft)
    verified = _write_memory_artifact_payload(
        db,
        artifact_type=MEMORY_ARTIFACT_TYPE,
        event_id=draft.event_id,
        extra_metadata={
            **metadata,
            "artifact_stage": "verified_memory",
            "memory_scope": "verified",
            "review_state": REVIEW_STATE_VERIFIED,
            "source_draft_id": str(draft.id),
        },
        payload=payload,
        project_id=draft.project_id,
        session_id=draft.session_id,
        storage_key=f"memory/session/{draft.session_id}/verified/{draft.id}",
    )
    draft.metadata_ = {
        **metadata,
        "review_state": REVIEW_STATE_SAVED,
        "verified_artifact_id": str(verified.id),
    }
    draft.updated_at = utc_now()
    db.flush()
    return verified


def ignore_memory_draft(db: DBSession, draft: Artifact) -> Artifact:
    metadata = draft.metadata_ if isinstance(draft.metadata_, dict) else {}
    draft.metadata_ = {
        **metadata,
        "review_state": REVIEW_STATE_IGNORED,
    }
    draft.updated_at = utc_now()
    db.flush()
    return draft


def list_project_memory_drafts(
    db: DBSession,
    *,
    limit: int = 20,
    project_id: UUID,
) -> list[Artifact]:
    artifacts = list(
        db.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.type == MEMORY_DRAFT_ARTIFACT_TYPE,
                Artifact.metadata_["artifact_stage"].astext == "memory_draft",
                Artifact.metadata_["review_state"].astext == REVIEW_STATE_DRAFT,
                Artifact.metadata_["summary_level"].astext == "2",
                Artifact.metadata_["fallback_reason"].astext.is_(None),
            )
            .order_by(desc(Artifact.updated_at), desc(Artifact.created_at))
            .limit(limit * 5)
        ).scalars()
    )
    filtered = [
        artifact for artifact in artifacts if not _is_generic_local_memory_summary(artifact.summary)
    ]
    return filtered[:limit]


def create_and_run_session_memory_job(
    db: DBSession,
    *,
    project_id: UUID,
    reason: str,
    session_id: UUID,
) -> ArtifactGenerationJob:
    job = create_artifact_generation_job(
        db,
        project_id=project_id,
        reason=reason,
        session_id=session_id,
    )
    return run_artifact_generation_job(db, job)
