from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

from sqlalchemy import Integer, and_, case, cast, desc, func, or_, select, tuple_
from sqlalchemy.orm import Session as DBSession

from app.models.artifacts import Artifact
from app.models.events import Event
from app.models.projects import Project
from app.models.sessions import Session
from app.models.users import User
from app.schemas.events import EventCreate, EventRead
from app.services.event_payload_security import (
    apply_event_storage_policy,
    decrypt_event_payload,
    encrypt_event_payload,
)
from app.services.memory.artifacts import generate_due_memory_artifacts_for_session
from app.services.memory.constants import (
    MEMORY_DRAFT_ARTIFACT_TYPE,
    MEMORY_WINDOW_STRATEGY,
    PENDING_DRAFT_STAGE,
)
from app.services.memory.windows import memory_slice_prompt_target
from app.services.projects.search import upsert_prompt_search_documents
from app.services.projects.resources import sync_project_resources_from_events
from app.services.projects.stats import increment_project_stats, project_stats_delta

SYSTEM_USER_ID = uuid5(NAMESPACE_DNS, "prompthub.system_user")


class EventIngestConflict(ValueError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class PreparedEvent:
    event: EventCreate
    payload: dict[str, Any]


def _dump_model(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value.dict()


def _payload_value(event: EventCreate, key: str) -> str | None:
    payload = _dump_model(event.payload)
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _project_name(event: EventCreate) -> str:
    cwd = _payload_value(event, "cwd")
    if cwd:
        return Path(cwd).name or str(event.project_id)
    return f"Project {str(event.project_id)[:8]}"


def _project_git_remote(event: EventCreate) -> str | None:
    return _payload_value(event, "github_url") or _payload_value(event, "git_remote")


def _ensure_system_user(db: DBSession) -> User:
    user = db.get(User, SYSTEM_USER_ID)
    if user is not None:
        return user

    user = User(
        id=SYSTEM_USER_ID,
        github_id="system",
        email="system@promty.local",
        username="system",
        avatar_url=None,
    )
    db.add(user)
    return user


def _ensure_project_owner(project: Project, owner: User | None) -> None:
    if owner is None:
        return
    if project.owner_id == owner.id:
        return
    if project.owner_id == SYSTEM_USER_ID:
        project.owner_id = owner.id
        return
    raise EventIngestConflict("Event project_id belongs to a different user")


def _new_project(event: EventCreate, owner: User) -> Project:
    branch = _payload_value(event, "branch") or "main"
    return Project(
        id=event.project_id,
        owner_id=owner.id,
        name=_project_name(event),
        slug=f"project-{str(event.project_id)[:8]}",
        description=None,
        visibility="private",
        git_remote=_project_git_remote(event),
        local_path_hash=None,
        default_branch=branch,
    )


def _new_session(event: EventCreate, project: Project) -> Session:
    session = Session(
        id=event.session_id,
        project_id=project.id,
        device_id=None,
        tool=event.tool,
        tool_version=None,
        model=_payload_value(event, "model"),
        cwd=_payload_value(event, "cwd"),
        branch=_payload_value(event, "branch"),
        started_at=event.timestamp,
        last_activity_at=event.timestamp,
        ended_at=None,
    )
    _apply_session_metadata(session, event)
    return session


def _apply_session_metadata(session: Session, event: EventCreate) -> None:
    model = _payload_value(event, "model")
    cwd = _payload_value(event, "cwd")
    branch = _payload_value(event, "branch")

    if model and not session.model:
        session.model = model
    if cwd and not session.cwd:
        session.cwd = cwd
    if branch and not session.branch:
        session.branch = branch
    last_activity_at = getattr(session, "last_activity_at", None)
    if last_activity_at is None or event.timestamp > last_activity_at:
        session.last_activity_at = event.timestamp
    if event.event_type == "SessionEnded":
        session.ended_at = event.timestamp
    elif session.ended_at is not None and event.timestamp > session.ended_at:
        # Idle completion is provisional. If the collector reports later work
        # for the same session, reopen it; an eventual SessionEnded event will
        # close it again.
        session.ended_at = None


def _to_read_model(event: Event) -> EventRead:
    return EventRead(
        id=event.id,
        schema_version=event.schema_version,
        project_id=event.project_id,
        session_id=event.session_id,
        sequence=event.sequence,
        tool=event.tool,
        event_type=event.event_type,
        timestamp=event.created_at,
        payload=decrypt_event_payload(event.event_type, event.payload),
    )


def _event_values(event: EventCreate, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": event.project_id,
        "session_id": event.session_id,
        "sequence": event.sequence,
        "schema_version": event.schema_version,
        "tool": event.tool,
        "event_type": event.event_type,
        "payload": payload,
        "created_at": event.timestamp,
    }


def _stored_event_values(event: Event) -> dict[str, Any]:
    payload = apply_event_storage_policy(
        event.event_type,
        decrypt_event_payload(event.event_type, event.payload),
    )
    return {
        "project_id": event.project_id,
        "session_id": event.session_id,
        "sequence": event.sequence,
        "schema_version": event.schema_version,
        "tool": event.tool,
        "event_type": event.event_type,
        "payload": payload,
        "created_at": event.created_at,
    }


def _prepare_events(events: list[EventCreate]) -> list[PreparedEvent]:
    return [
        PreparedEvent(
            event=event,
            payload=apply_event_storage_policy(
                event.event_type,
                _dump_model(event.payload),
            ),
        )
        for event in events
    ]


def _existing_events_by_id(
    db: DBSession,
    prepared_events: list[PreparedEvent],
) -> dict[UUID, Event]:
    event_ids = {prepared.event.id for prepared in prepared_events}
    if not event_ids:
        return {}
    return {
        event.id: event for event in db.scalars(select(Event).where(Event.id.in_(list(event_ids))))
    }


def _existing_sequence_owners(
    db: DBSession,
    prepared_events: list[PreparedEvent],
) -> dict[tuple[UUID, UUID, int], UUID]:
    sequence_keys = {
        (
            prepared.event.project_id,
            prepared.event.session_id,
            prepared.event.sequence,
        )
        for prepared in prepared_events
    }
    if not sequence_keys:
        return {}
    return {
        (project_id, session_id, sequence): event_id
        for event_id, project_id, session_id, sequence in db.execute(
            select(Event.id, Event.project_id, Event.session_id, Event.sequence).where(
                tuple_(Event.project_id, Event.session_id, Event.sequence).in_(list(sequence_keys))
            )
        )
    }


def _partition_events(
    prepared_events: list[PreparedEvent],
    *,
    existing_by_id: dict[UUID, Event],
    sequence_owners: dict[tuple[UUID, UUID, int], UUID],
) -> tuple[list[PreparedEvent], list[PreparedEvent]]:
    first_occurrences: list[PreparedEvent] = []
    first_occurrence_ids: set[UUID] = set()
    new_events: list[PreparedEvent] = []
    seen_new_by_id: dict[UUID, PreparedEvent] = {}
    stored_values_by_id: dict[UUID, dict[str, Any]] = {}

    for prepared in prepared_events:
        incoming = prepared.event
        existing = existing_by_id.get(incoming.id)
        if existing is not None:
            stored_values = stored_values_by_id.setdefault(
                incoming.id,
                _stored_event_values(existing),
            )
            if stored_values != _event_values(incoming, prepared.payload):
                raise EventIngestConflict("Event id already exists with different content")
            if incoming.id not in first_occurrence_ids:
                first_occurrences.append(prepared)
                first_occurrence_ids.add(incoming.id)
            continue

        prior = seen_new_by_id.get(incoming.id)
        if prior is not None:
            if _event_values(prior.event, prior.payload) != _event_values(
                incoming,
                prepared.payload,
            ):
                raise EventIngestConflict("Event id already exists with different content")
            continue

        sequence_key = (incoming.project_id, incoming.session_id, incoming.sequence)
        if sequence_key in sequence_owners:
            raise EventIngestConflict(
                "Event sequence already exists for this project_id/session_id"
            )

        seen_new_by_id[incoming.id] = prepared
        sequence_owners[sequence_key] = incoming.id
        first_occurrences.append(prepared)
        first_occurrence_ids.add(incoming.id)
        new_events.append(prepared)

    return first_occurrences, new_events


def _prefetch_projects_and_sessions(
    db: DBSession,
    new_events: list[PreparedEvent],
) -> tuple[dict[UUID, Project], dict[UUID, Session]]:
    project_ids = {prepared.event.project_id for prepared in new_events}
    session_ids = {prepared.event.session_id for prepared in new_events}
    projects = (
        {
            project.id: project
            for project in db.scalars(select(Project).where(Project.id.in_(project_ids)))
        }
        if project_ids
        else {}
    )
    sessions = (
        {
            session.id: session
            for session in db.scalars(
                select(Session)
                .where(Session.id.in_(session_ids))
                .order_by(Session.id)
                .with_for_update()
            )
        }
        if session_ids
        else {}
    )
    return projects, sessions


def _stage_new_events(
    db: DBSession,
    new_events: list[PreparedEvent],
    *,
    owner: User | None,
) -> tuple[dict[UUID, Event], dict[UUID, Session], set[UUID]]:
    projects_by_id, sessions_by_id = _prefetch_projects_and_sessions(db, new_events)
    event_rows_by_id: dict[UUID, Event] = {}
    touched_sessions: dict[UUID, Session] = {}
    new_session_ids: set[UUID] = set()
    system_user: User | None = None

    for prepared in new_events:
        event = prepared.event
        session = sessions_by_id.get(event.session_id)
        if session is not None:
            if session.project_id != event.project_id:
                raise EventIngestConflict("Event session_id belongs to a different project_id")
            project = projects_by_id.get(event.project_id)
            if project is None:
                raise EventIngestConflict("Event project_id does not exist")
            _ensure_project_owner(project, owner)
            _apply_session_metadata(session, event)
        else:
            project = projects_by_id.get(event.project_id)
            if project is None:
                if owner is None:
                    system_user = system_user or _ensure_system_user(db)
                project = _new_project(event, owner or system_user)
                db.add(project)
                projects_by_id[project.id] = project
            else:
                _ensure_project_owner(project, owner)
                git_remote = _project_git_remote(event)
                if git_remote and not project.git_remote:
                    project.git_remote = git_remote

            session = _new_session(event, project)
            db.add(session)
            sessions_by_id[session.id] = session
            new_session_ids.add(session.id)

        event_row = Event(
            id=event.id,
            project_id=event.project_id,
            session_id=event.session_id,
            sequence=event.sequence,
            schema_version=event.schema_version,
            tool=event.tool,
            event_type=event.event_type,
            payload=encrypt_event_payload(event.event_type, prepared.payload),
            created_at=event.timestamp,
        )
        db.add(event_row)
        event_rows_by_id[event_row.id] = event_row
        touched_sessions[session.id] = session

    return event_rows_by_id, touched_sessions, new_session_ids


def _due_memory_session_ids(
    db: DBSession,
    *,
    new_events: list[PreparedEvent],
    touched_sessions: dict[UUID, Session],
) -> set[UUID]:
    """Return only touched sessions that can produce at least one memory slice.

    Event ingest used to call the memory window reader once for every touched
    session, even when a batch only contained the first prompt for hundreds of
    sessions. This query evaluates the first uncovered window for every
    response/file/finalization-triggered session together. Per-session work is
    then reserved for sessions that have a prompt followed by an AI response
    inside that window. File evidence is optional so reasoning-only work is not
    discarded.
    """

    # A prompt normally arrives before its response/file markers, but the
    # collector queue can replay a missing lower sequence after later events
    # have already been stored. In that case the prompt itself completes the
    # first uncovered window, so it must participate in the same bulk check.
    trigger_types = {
        "FilesChanged",
        "PromptSubmitted",
        "ResponseReceived",
        "SessionEnded",
    }
    triggered_ids = {
        prepared.event.session_id
        for prepared in new_events
        if prepared.event.event_type in trigger_types
    }
    candidate_ids = triggered_ids.intersection(touched_sessions)
    if not candidate_ids:
        return set()

    finalized_ids = {
        session_id
        for session_id in candidate_ids
        if touched_sessions[session_id].ended_at is not None
    }
    prompt_target = memory_slice_prompt_target()
    latest_slices = (
        select(
            Artifact.session_id.label("session_id"),
            func.max(cast(Artifact.metadata_["end_sequence"].astext, Integer)).label(
                "end_sequence"
            ),
        )
        .where(
            Artifact.session_id.in_(candidate_ids),
            Artifact.type == MEMORY_DRAFT_ARTIFACT_TYPE,
            Artifact.metadata_["artifact_stage"].astext == PENDING_DRAFT_STAGE,
            Artifact.metadata_["memory_strategy"].astext == MEMORY_WINDOW_STRATEGY,
        )
        .group_by(Artifact.session_id)
        .cte("latest_memory_slices")
    )
    uncovered_prompts = (
        select(
            Event.session_id.label("session_id"),
            Event.sequence.label("sequence"),
            func.row_number()
            .over(
                partition_by=Event.session_id,
                order_by=(Event.sequence, Event.created_at, Event.id),
            )
            .label("ordinal"),
        )
        .outerjoin(
            latest_slices,
            latest_slices.c.session_id == Event.session_id,
        )
        .where(
            Event.session_id.in_(candidate_ids),
            Event.event_type == "PromptSubmitted",
            Event.sequence > func.coalesce(latest_slices.c.end_sequence, -1),
        )
        .cte("uncovered_memory_prompts")
    )
    prompt_bounds = (
        select(
            uncovered_prompts.c.session_id,
            func.count().label("prompt_count"),
            func.max(uncovered_prompts.c.sequence).label("last_prompt_sequence"),
            func.max(
                case(
                    (
                        uncovered_prompts.c.ordinal == prompt_target,
                        uncovered_prompts.c.sequence,
                    ),
                )
            ).label("target_prompt_sequence"),
            func.max(
                case(
                    (
                        uncovered_prompts.c.ordinal == prompt_target + 1,
                        uncovered_prompts.c.sequence,
                    ),
                )
            ).label("next_prompt_sequence"),
        )
        .group_by(uncovered_prompts.c.session_id)
        .cte("memory_prompt_bounds")
    )
    anchor_sequence = case(
        (
            prompt_bounds.c.prompt_count >= prompt_target,
            prompt_bounds.c.target_prompt_sequence,
        ),
        else_=prompt_bounds.c.last_prompt_sequence,
    )
    due_condition = prompt_bounds.c.prompt_count >= prompt_target
    if finalized_ids:
        due_condition = or_(
            due_condition,
            and_(
                prompt_bounds.c.session_id.in_(finalized_ids),
                prompt_bounds.c.prompt_count > 0,
            ),
        )
    statement = (
        select(prompt_bounds.c.session_id)
        .join(Event, Event.session_id == prompt_bounds.c.session_id)
        .where(
            due_condition,
            Event.sequence > anchor_sequence,
            or_(
                prompt_bounds.c.next_prompt_sequence.is_(None),
                Event.sequence < prompt_bounds.c.next_prompt_sequence,
            ),
        )
        .group_by(prompt_bounds.c.session_id)
        .having(
            func.count(Event.id).filter(Event.event_type == "ResponseReceived") > 0,
        )
    )
    return set(db.scalars(statement))


def add_events(
    db: DBSession,
    events: list[EventCreate],
    *,
    owner: User | None = None,
) -> list[str]:
    prepared_events = _prepare_events(events)
    existing_by_id = _existing_events_by_id(db, prepared_events)
    first_occurrences, new_events = _partition_events(
        prepared_events,
        existing_by_id=existing_by_id,
        sequence_owners=_existing_sequence_owners(db, prepared_events),
    )
    new_rows_by_id, touched_sessions, new_session_ids = _stage_new_events(
        db,
        new_events,
        owner=owner,
    )
    if new_events:
        # Prompt search/resource rows use UUID foreign keys without ORM relationships.
        # Persist their Project -> Session -> Event dependencies as one bounded phase.
        db.flush()

    rows_by_id = {**existing_by_id, **new_rows_by_id}
    unique_event_payloads = [
        (rows_by_id[prepared.event.id], prepared.payload) for prepared in first_occurrences
    ]
    upsert_prompt_search_documents(db, unique_event_payloads)
    tracked_file_deltas = sync_project_resources_from_events(
        db,
        [(new_rows_by_id[prepared.event.id], prepared.payload) for prepared in new_events],
    )
    stats_by_project: dict[UUID, dict[str, Any]] = {}
    for prepared in new_events:
        event = prepared.event
        stats = stats_by_project.setdefault(
            event.project_id,
            project_stats_delta(project_id=event.project_id),
        )
        stats["event_count"] += 1
        if event.event_type == "PromptSubmitted":
            stats["prompt_count"] += 1
        latest_event_at = stats.get("latest_event_at")
        if latest_event_at is None or event.timestamp > latest_event_at:
            stats["latest_event_at"] = event.timestamp
    for session_id in new_session_ids:
        session = touched_sessions[session_id]
        stats = stats_by_project.setdefault(
            session.project_id,
            project_stats_delta(project_id=session.project_id),
        )
        stats["session_count"] += 1
    for project_id, tracked_file_delta in tracked_file_deltas.items():
        stats = stats_by_project.setdefault(
            project_id,
            project_stats_delta(project_id=project_id),
        )
        stats["tracked_files"] += tracked_file_delta
    increment_project_stats(db, stats_by_project.values())
    due_session_ids = _due_memory_session_ids(
        db,
        new_events=new_events,
        touched_sessions=touched_sessions,
    )
    for session_id in sorted(due_session_ids, key=str):
        session = touched_sessions[session_id]
        generate_due_memory_artifacts_for_session(
            db,
            session,
            finalize=session.ended_at is not None,
        )

    return [str(event.id) for event in events]


def list_recent_events(
    db: DBSession,
    *,
    owner: User,
    project_id: UUID | None = None,
    session_id: UUID | None = None,
    event_type: str | None = None,
    limit: int = 100,
) -> list[EventRead]:
    query = (
        select(Event)
        .join(Project, Event.project_id == Project.id)
        .where(Project.owner_id == owner.id)
    )
    if project_id is not None:
        query = query.where(Event.project_id == project_id)
    if session_id is not None:
        query = query.where(Event.session_id == session_id)
    if event_type is not None:
        query = query.where(Event.event_type == event_type)

    rows = db.execute(
        query.order_by(desc(Event.created_at), desc(Event.sequence)).limit(limit)
    ).scalars()
    return [_to_read_model(event) for event in rows]
