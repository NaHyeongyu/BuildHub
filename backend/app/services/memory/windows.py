from __future__ import annotations

from typing import Any

from sqlalchemy import Integer, and_, cast, desc, func, or_, select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import settings
from app.models.artifacts import Artifact
from app.models.events import Event
from app.models.sessions import Session
from app.services.memory.constants import (
    MEMORY_DRAFT_ARTIFACT_TYPE,
    MEMORY_WINDOW_STRATEGY,
    PENDING_DRAFT_STAGE,
)
from app.services.memory.context import payload as event_payload
from app.services.memory.context import string_or_none


MEMORY_SLICE_KIND_IMPLEMENTATION = "implementation"
MEMORY_SLICE_KIND_REASONING = "reasoning"


def memory_slice_event_max_rows() -> int:
    return max(settings.memory_slice_event_max_rows, 2)


def memory_slice_prompt_target() -> int:
    # The prompt decision query and the event range share the same hard row
    # ceiling. Keep one row available for the look-ahead prompt that closes a
    # prompt-count group.
    return min(max(settings.memory_slice_prompt_count, 1), memory_slice_event_max_rows() - 1)


def response_has_content_condition() -> ColumnElement[bool]:
    response_original_length = cast(
        Event.payload["response_original_length"].astext,
        Integer,
    )
    response_text = Event.payload["response"].astext
    return and_(
        Event.event_type == "ResponseReceived",
        or_(
            response_original_length > 0,
            and_(
                Event.payload["response_original_length"].astext.is_(None),
                response_text.is_not(None),
                func.length(response_text) > 0,
            ),
        ),
    )


def slice_metadata(artifact: Artifact) -> dict[str, Any]:
    metadata = artifact.metadata_ if isinstance(artifact.metadata_, dict) else {}
    if metadata.get("memory_strategy") != MEMORY_WINDOW_STRATEGY:
        return {}
    return metadata


def memory_slice_artifacts(db: DBSession, session: Session) -> list[Artifact]:
    return list(
        db.execute(
            select(Artifact)
            .where(
                Artifact.project_id == session.project_id,
                Artifact.session_id == session.id,
                Artifact.type == MEMORY_DRAFT_ARTIFACT_TYPE,
                Artifact.metadata_["artifact_stage"].astext == PENDING_DRAFT_STAGE,
                Artifact.metadata_["memory_strategy"].astext == MEMORY_WINDOW_STRATEGY,
            )
            .order_by(Artifact.created_at, Artifact.updated_at)
        ).scalars()
    )


def memory_slice_state(
    db: DBSession,
    session: Session,
) -> tuple[int | None, int]:
    end_sequence_column = cast(Artifact.metadata_["end_sequence"].astext, Integer)
    row = db.execute(
        _latest_memory_slice_values_statement(
            session,
            end_sequence_column,
            cast(Artifact.metadata_["slice_index"].astext, Integer),
        )
    ).one_or_none()
    end_sequence, slice_index = row if row is not None else (None, None)
    return (
        end_sequence if isinstance(end_sequence, int) else None,
        (slice_index if isinstance(slice_index, int) else 0) + 1,
    )


def memory_slice_materialization_state(
    db: DBSession,
    session: Session,
) -> tuple[int | None, int, int | None]:
    """Return the coverage cursor and an unfinished logical-window boundary.

    A prompt-count window can contain more rows than one materialization slice.
    Every slice persists the same ``materialization_end_sequence`` so a later
    transaction can resume the group without skipping the rows between the
    last persisted slice and the next prompt.
    """
    end_sequence_column = cast(Artifact.metadata_["end_sequence"].astext, Integer)
    row = db.execute(
        _latest_memory_slice_values_statement(
            session,
            end_sequence_column,
            cast(Artifact.metadata_["slice_index"].astext, Integer),
            cast(
                Artifact.metadata_["materialization_end_sequence"].astext,
                Integer,
            ),
        )
    ).one_or_none()
    end_sequence, slice_index, materialization_end_sequence = (
        row if row is not None else (None, None, None)
    )
    covered_end = end_sequence if isinstance(end_sequence, int) else None
    group_end = (
        materialization_end_sequence
        if isinstance(materialization_end_sequence, int)
        and (covered_end is None or materialization_end_sequence > covered_end)
        else None
    )
    return (
        covered_end,
        (slice_index if isinstance(slice_index, int) else 0) + 1,
        group_end,
    )


def memory_slice_runtime_state(
    db: DBSession,
    session: Session,
) -> tuple[int | None, int, int | None, bool]:
    end_sequence_column = cast(Artifact.metadata_["end_sequence"].astext, Integer)
    row = db.execute(
        _latest_memory_slice_values_statement(
            session,
            end_sequence_column,
            cast(Artifact.metadata_["slice_index"].astext, Integer),
            cast(
                Artifact.metadata_["materialization_end_sequence"].astext,
                Integer,
            ),
            Artifact.metadata_["memory_resume_required"].astext == "true",
        )
    ).one_or_none()
    end_sequence, slice_index, materialization_end_sequence, resume_required = (
        row if row is not None else (None, None, None, False)
    )
    covered_end = end_sequence if isinstance(end_sequence, int) else None
    group_end = (
        materialization_end_sequence
        if isinstance(materialization_end_sequence, int)
        and (covered_end is None or materialization_end_sequence > covered_end)
        else None
    )
    return (
        covered_end,
        (slice_index if isinstance(slice_index, int) else 0) + 1,
        group_end,
        resume_required is True,
    )


def _latest_memory_slice_values_statement(
    session: Session,
    *columns: Any,
):
    """Project state from the one slice holding the monotonic coverage cursor."""

    end_sequence_column = cast(Artifact.metadata_["end_sequence"].astext, Integer)
    return (
        select(*columns)
        .where(
            Artifact.project_id == session.project_id,
            Artifact.session_id == session.id,
            Artifact.type == MEMORY_DRAFT_ARTIFACT_TYPE,
            Artifact.metadata_["artifact_stage"].astext == PENDING_DRAFT_STAGE,
            Artifact.metadata_["memory_strategy"].astext == MEMORY_WINDOW_STRATEGY,
            end_sequence_column.is_not(None),
        )
        .order_by(desc(end_sequence_column))
        .limit(1)
    )


def latest_memory_slice_end_sequence(db: DBSession, session: Session) -> int | None:
    end_sequence, _next_slice_index = memory_slice_state(db, session)
    return end_sequence


def next_memory_slice_index(db: DBSession, session: Session) -> int:
    _end_sequence, next_slice_index = memory_slice_state(db, session)
    return next_slice_index


def latest_session_event(db: DBSession, session: Session) -> Event | None:
    return db.execute(
        select(Event)
        .where(Event.project_id == session.project_id, Event.session_id == session.id)
        .order_by(desc(Event.sequence), desc(Event.created_at))
        .limit(1)
    ).scalar_one_or_none()


def _event_type_value(event: Any) -> str | None:
    if isinstance(event, dict):
        value = event.get("event_type")
    else:
        value = getattr(event, "event_type", None)
    return value if isinstance(value, str) else None


def _event_sequence_value(event: Any) -> int | None:
    if isinstance(event, dict):
        value = event.get("sequence")
    else:
        value = getattr(event, "sequence", None)
    return value if isinstance(value, int) else None


def _event_payload_value(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        payload = event.get("payload")
        return payload if isinstance(payload, dict) else {}
    if isinstance(event, Event):
        return event_payload(event)
    payload = getattr(event, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _event_has_response_text(event: Any) -> bool:
    if _event_type_value(event) != "ResponseReceived":
        return False
    return string_or_none(_event_payload_value(event).get("response")) is not None


def generation_input_kind(events: list[Any]) -> str | None:
    prompt_sequences = [
        sequence
        for event in events
        if _event_type_value(event) == "PromptSubmitted"
        and isinstance((sequence := _event_sequence_value(event)), int)
    ]
    if not prompt_sequences:
        return None

    latest_prompt_sequence = max(prompt_sequences)
    events_after_prompt = [
        event
        for event in events
        if isinstance((sequence := _event_sequence_value(event)), int)
        and sequence > latest_prompt_sequence
    ]
    if not any(_event_has_response_text(event) for event in events_after_prompt):
        return None
    if any(_event_type_value(event) == "FilesChanged" for event in events_after_prompt):
        return MEMORY_SLICE_KIND_IMPLEMENTATION
    return MEMORY_SLICE_KIND_REASONING


def events_have_generation_inputs(events: list[Any]) -> bool:
    return generation_input_kind(events) is not None


def _events_for_memory_window(
    db: DBSession,
    session: Session,
    *,
    start_sequence: int,
    through_sequence: int,
) -> list[Event]:
    return list(
        db.execute(
            select(Event)
            .where(
                Event.project_id == session.project_id,
                Event.session_id == session.id,
                Event.sequence >= start_sequence,
                Event.sequence <= through_sequence,
            )
            .order_by(Event.sequence, Event.created_at)
            .limit(memory_slice_event_max_rows())
        ).scalars()
    )


def _event_after_sequence(
    db: DBSession,
    session: Session,
    *,
    after_sequence: int,
    through_sequence: int | None = None,
) -> Event | None:
    query = select(Event).where(
        Event.project_id == session.project_id,
        Event.session_id == session.id,
        Event.sequence > after_sequence,
    )
    if through_sequence is not None:
        query = query.where(Event.sequence <= through_sequence)
    return db.execute(
        query.order_by(Event.sequence, Event.created_at).limit(1)
    ).scalar_one_or_none()


def _bounded_memory_window_events(
    db: DBSession,
    session: Session,
    *,
    start_sequence: int,
    through_sequence: int,
) -> tuple[list[Event], int]:
    events = _events_for_memory_window(
        db,
        session,
        start_sequence=start_sequence,
        through_sequence=through_sequence,
    )
    if not events:
        return [], through_sequence

    last_sequence = events[-1].sequence
    if len(events) < memory_slice_event_max_rows():
        return events, through_sequence
    has_more = (
        _event_after_sequence(
            db,
            session,
            after_sequence=last_sequence,
            through_sequence=through_sequence,
        )
        is not None
    )
    return events, last_sequence if has_more else through_sequence


def _window_generation_input_kind(
    db: DBSession,
    session: Session,
    *,
    latest_prompt_sequence: int,
    through_sequence: int,
) -> str | None:
    response_query = select(Event.id).where(
        Event.project_id == session.project_id,
        Event.session_id == session.id,
        Event.sequence > latest_prompt_sequence,
        Event.sequence <= through_sequence,
        response_has_content_condition(),
    )
    files_query = select(Event.id).where(
        Event.project_id == session.project_id,
        Event.session_id == session.id,
        Event.event_type == "FilesChanged",
        Event.sequence > latest_prompt_sequence,
        Event.sequence <= through_sequence,
    )
    has_response, has_files = db.execute(
        select(response_query.exists(), files_query.exists())
    ).one()
    if not has_response:
        return None
    return MEMORY_SLICE_KIND_IMPLEMENTATION if has_files else MEMORY_SLICE_KIND_REASONING


def _prompt_events_after_sequence(
    db: DBSession,
    session: Session,
    *,
    after_sequence: int | None,
    limit: int,
) -> list[Event]:
    query = select(Event).where(
        Event.project_id == session.project_id,
        Event.session_id == session.id,
        Event.event_type == "PromptSubmitted",
    )
    if after_sequence is not None:
        query = query.where(Event.sequence > after_sequence)
    return list(db.execute(query.order_by(Event.sequence, Event.created_at).limit(limit)).scalars())


def _latest_prompt_through_sequence(
    db: DBSession,
    session: Session,
    *,
    through_sequence: int,
) -> Event | None:
    return db.execute(
        select(Event)
        .where(
            Event.project_id == session.project_id,
            Event.session_id == session.id,
            Event.event_type == "PromptSubmitted",
            Event.sequence <= through_sequence,
        )
        .order_by(desc(Event.sequence), desc(Event.created_at))
        .limit(1)
    ).scalar_one_or_none()


def _window_result(
    db: DBSession,
    session: Session,
    *,
    context_prompt: Event | None,
    materialization_end_sequence: int,
    memory_slice_kind: str,
    reason: str,
    start_sequence: int,
) -> dict[str, Any] | None:
    window_events, covered_end_sequence = _bounded_memory_window_events(
        db,
        session,
        start_sequence=start_sequence,
        through_sequence=materialization_end_sequence,
    )
    if not window_events:
        return None
    selected_prompts = [
        event for event in window_events if _event_type_value(event) == "PromptSubmitted"
    ]
    return {
        "context_prompt": (
            context_prompt
            if context_prompt is not None
            and not selected_prompts
            and all(event.id != context_prompt.id for event in window_events)
            else None
        ),
        "end_sequence": covered_end_sequence,
        "event_row_limit": memory_slice_event_max_rows(),
        "events": window_events,
        "materialization_end_sequence": materialization_end_sequence,
        "memory_slice_kind": memory_slice_kind,
        "reason": reason,
        "selected_prompts": selected_prompts,
        "start_sequence": start_sequence,
        "window_truncated": covered_end_sequence < materialization_end_sequence,
    }


def due_memory_window(
    db: DBSession,
    session: Session,
    *,
    after_sequence: int | None,
    continuation_end_sequence: int | None = None,
    finalize: bool,
) -> dict[str, Any] | None:
    if (
        after_sequence is not None
        and continuation_end_sequence is not None
        and continuation_end_sequence > after_sequence
    ):
        context_prompt = _latest_prompt_through_sequence(
            db,
            session,
            through_sequence=after_sequence,
        )
        return _window_result(
            db,
            session,
            context_prompt=context_prompt,
            materialization_end_sequence=continuation_end_sequence,
            memory_slice_kind="continuation",
            reason="event_count_continuation",
            start_sequence=after_sequence + 1,
        )

    prompt_target = memory_slice_prompt_target()
    prompts = _prompt_events_after_sequence(
        db,
        session,
        after_sequence=after_sequence,
        limit=prompt_target + 1,
    )

    # Backfill uncovered trailing events created after an older materialized
    # prompt window. This also makes rolling upgrades safe for artifacts that
    # predate ``materialization_end_sequence`` metadata.
    if after_sequence is not None:
        first_uncovered_event = _event_after_sequence(
            db,
            session,
            after_sequence=after_sequence,
        )
        if first_uncovered_event is not None and (
            not prompts or first_uncovered_event.sequence < prompts[0].sequence
        ):
            if prompts:
                inferred_end_sequence = prompts[0].sequence - 1
            else:
                latest_event = latest_session_event(db, session)
                if latest_event is None:
                    return None
                inferred_end_sequence = latest_event.sequence
            return _window_result(
                db,
                session,
                context_prompt=_latest_prompt_through_sequence(
                    db,
                    session,
                    through_sequence=after_sequence,
                ),
                materialization_end_sequence=inferred_end_sequence,
                memory_slice_kind="continuation",
                reason="event_count_continuation",
                start_sequence=after_sequence + 1,
            )

    if not prompts:
        return None

    if len(prompts) >= prompt_target:
        selected_prompts = prompts[:prompt_target]
        next_prompt = prompts[prompt_target] if len(prompts) > prompt_target else None
        if next_prompt is not None:
            end_sequence = next_prompt.sequence - 1
        else:
            latest_event = latest_session_event(db, session)
            if latest_event is None or latest_event.sequence <= selected_prompts[-1].sequence:
                return None
            end_sequence = latest_event.sequence
        memory_slice_kind = _window_generation_input_kind(
            db,
            session,
            latest_prompt_sequence=selected_prompts[-1].sequence,
            through_sequence=end_sequence,
        )
        if memory_slice_kind is None:
            return None
        return _window_result(
            db,
            session,
            context_prompt=selected_prompts[-1],
            materialization_end_sequence=end_sequence,
            memory_slice_kind=memory_slice_kind,
            reason="prompt_count",
            start_sequence=selected_prompts[0].sequence,
        )

    if finalize:
        latest_event = latest_session_event(db, session)
        if latest_event is None:
            return None
        memory_slice_kind = _window_generation_input_kind(
            db,
            session,
            latest_prompt_sequence=prompts[-1].sequence,
            through_sequence=latest_event.sequence,
        )
        if memory_slice_kind is None:
            return None
        return _window_result(
            db,
            session,
            context_prompt=prompts[-1],
            materialization_end_sequence=latest_event.sequence,
            memory_slice_kind=memory_slice_kind,
            reason="session_finalized",
            start_sequence=prompts[0].sequence,
        )

    return None


def latest_memory_slice(db: DBSession, session: Session) -> Artifact | None:
    end_sequence_column = cast(Artifact.metadata_["end_sequence"].astext, Integer)
    return db.execute(
        select(Artifact)
        .where(
            Artifact.project_id == session.project_id,
            Artifact.session_id == session.id,
            Artifact.type == MEMORY_DRAFT_ARTIFACT_TYPE,
            Artifact.metadata_["artifact_stage"].astext == PENDING_DRAFT_STAGE,
            Artifact.metadata_["memory_strategy"].astext == MEMORY_WINDOW_STRATEGY,
            end_sequence_column.is_not(None),
        )
        .order_by(desc(end_sequence_column))
        .limit(1)
    ).scalar_one_or_none()
