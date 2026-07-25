from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Date, and_, case, cast, desc, func, literal, or_, select, true, union_all
from sqlalchemy.orm import Session

from app.core.encoding import base64_urldecode, base64_urlencode
from app.core.security import is_admin_user
from app.models.artifacts import Artifact
from app.models.code_change_patches import CodeChangePatch
from app.models.events import Event
from app.models.project_files import ProjectFile
from app.models.projects import Project
from app.models.prompt_search_documents import PromptSearchDocument
from app.models.sessions import Session as PromptSession
from app.models.users import User
from app.services.event_payload_security import (
    decrypt_event_payload,
)
from app.services.memory.artifacts import (
    get_latest_project_memory,
    list_project_memory_artifacts,
)
from app.services.memory.constants import (
    MEMORY_ARTIFACT_TYPE,
    REVIEW_STATE_GENERATED,
    REVIEW_STATE_VERIFIED,
)
from app.services.memory.serializers import (
    serialize_memory_artifact_summary,
)
from app.services.projects.activity import (
    file_changes_from_files_changed,
    files_changed_by_prompt_from_events,
    format_response_summary,
    iso,
    model_name,
    patch_file_changes_by_prompt,
    payload_model,
    payload_prompt,
    response_payloads_by_prompt,
    tool_label,
)
from app.services.projects.search import prompt_search_hashes_for_query

RECENT_ACTIVITY_LIMIT = 50
PROJECT_METRIC_HISTORY_DAYS = 14
PROMPT_RELATED_EVENT_LIMIT = 1000
PROJECT_FILE_TREE_LIMIT = 2000
PROMPT_RELATED_EVENT_SEQUENCE_TRAILING = 40

PromptActivityCursor = tuple[datetime, int, UUID]


def _project_metric_history(
    db: Session,
    *,
    project_id: UUID,
    now: datetime,
) -> list[dict[str, Any]]:
    today = now.astimezone(timezone.utc).date()
    first_day = today - timedelta(days=PROJECT_METRIC_HISTORY_DAYS - 1)
    first_day_at = datetime.combine(first_day, datetime.min.time(), tzinfo=timezone.utc)
    history = {
        (first_day + timedelta(days=index)).isoformat(): {
            "date": (first_day + timedelta(days=index)).isoformat(),
            "files_changed": 0,
            "memories": 0,
            "prompts": 0,
            "sessions": 0,
        }
        for index in range(PROJECT_METRIC_HISTORY_DAYS)
    }

    session_day = cast(func.timezone("UTC", PromptSession.started_at), Date)
    event_day = cast(func.timezone("UTC", Event.created_at), Date)
    artifact_day = cast(func.timezone("UTC", Artifact.created_at), Date)
    patch_day = cast(func.timezone("UTC", CodeChangePatch.created_at), Date)
    grouped_metrics = union_all(
        select(
            session_day.label("day"),
            literal("sessions").label("metric"),
            func.count(PromptSession.id).label("value"),
        )
        .where(
            PromptSession.project_id == project_id,
            PromptSession.started_at >= first_day_at,
        )
        .group_by(session_day),
        select(
            event_day.label("day"),
            literal("prompts").label("metric"),
            func.count(Event.id).label("value"),
        )
        .where(
            Event.project_id == project_id,
            Event.event_type == "PromptSubmitted",
            Event.created_at >= first_day_at,
        )
        .group_by(event_day),
        select(
            artifact_day.label("day"),
            literal("memories").label("metric"),
            func.count(Artifact.id).label("value"),
        )
        .where(
            Artifact.project_id == project_id,
            Artifact.type == MEMORY_ARTIFACT_TYPE,
            Artifact.metadata_["review_state"].astext.in_(
                [REVIEW_STATE_GENERATED, REVIEW_STATE_VERIFIED]
            ),
            Artifact.metadata_["artifact_stage"].astext.in_(
                ["generated_memory", "verified_memory"]
            ),
            Artifact.created_at >= first_day_at,
        )
        .group_by(artifact_day),
        select(
            patch_day.label("day"),
            literal("files_changed").label("metric"),
            func.count(func.distinct(CodeChangePatch.path)).label("value"),
        )
        .where(
            CodeChangePatch.project_id == project_id,
            CodeChangePatch.created_at >= first_day_at,
        )
        .group_by(patch_day),
    )
    for day, metric, value in db.execute(grouped_metrics).all():
        day_key = day.isoformat()
        if day_key in history and metric in history[day_key]:
            history[day_key][metric] = int(value or 0)

    return list(history.values())


def normalize_github_url(remote_url: str | None) -> str | None:
    if not remote_url:
        return None

    value = remote_url.strip()
    patterns = (
        r"^git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, value)
        if match:
            return f"https://github.com/{match.group('owner')}/{match.group('repo')}"
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 2:
        return None
    owner, repository = path_parts
    repository = re.sub(r"\.git$", "", repository, flags=re.IGNORECASE)
    if not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in (owner, repository)):
        return None
    return f"https://github.com/{owner}/{repository}"


def build_file_tree(paths: list[str]) -> list[dict[str, Any]]:
    root: dict[str, dict[str, Any]] = {}

    for path in paths:
        parts = [part for part in path.split("/") if part]
        if not parts:
            continue
        current = root
        for index, part in enumerate(parts[:-1]):
            node = current.setdefault(
                part,
                {
                    "name": part,
                    "path": "/".join(parts[: index + 1]),
                    "type": "folder",
                    "children": {},
                },
            )
            if node.get("type") != "folder":
                node["type"] = "folder"
                node["children"] = {}
            if not isinstance(node.get("children"), dict):
                node["children"] = {}
            current = node["children"]
        current[parts[-1]] = {"name": parts[-1], "path": path, "type": "file"}

    def serialize(nodes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for node in sorted(nodes.values(), key=lambda item: (item["type"] == "file", item["name"])):
            if node["type"] == "folder":
                serialized.append(
                    {
                        "name": node["name"],
                        "path": node["path"],
                        "type": "folder",
                        "children": serialize(node["children"]),
                    }
                )
            else:
                serialized.append(node)
        return serialized

    return serialize(root)


def project_for_user(
    db: Session,
    project_id: UUID,
    current_user: User,
    *,
    allow_admin: bool = False,
    allow_public: bool = False,
) -> Project:
    project = db.get(Project, project_id)
    if project is None or (
        project.owner_id != current_user.id
        and not (allow_admin and is_admin_user(current_user))
        and not (allow_public and project.visibility == "public")
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return project


def _project_detail_stats(
    db: Session,
    *,
    project_id: UUID,
    since: datetime,
) -> dict[str, Any]:
    visible_memory = and_(
        Artifact.type == MEMORY_ARTIFACT_TYPE,
        Artifact.metadata_["review_state"].astext.in_(
            [REVIEW_STATE_GENERATED, REVIEW_STATE_VERIFIED]
        ),
        Artifact.metadata_["artifact_stage"].astext.in_(["generated_memory", "verified_memory"]),
    )
    session_stats = (
        select(
            func.count(PromptSession.id).label("session_count"),
            func.count(PromptSession.id)
            .filter(PromptSession.started_at >= since)
            .label("recent_session_count"),
            func.array_agg(func.distinct(PromptSession.model))
            .filter(PromptSession.model.is_not(None))
            .label("models"),
            func.array_agg(func.distinct(PromptSession.tool))
            .filter(PromptSession.tool.is_not(None))
            .label("tools"),
        )
        .where(PromptSession.project_id == project_id)
        .subquery()
    )
    event_stats = (
        select(
            func.count(Event.id).label("event_count"),
            func.count(Event.id)
            .filter(Event.event_type == "PromptSubmitted")
            .label("prompt_count"),
            func.count(Event.id)
            .filter(
                Event.event_type == "PromptSubmitted",
                Event.created_at >= since,
            )
            .label("recent_prompt_count"),
            func.max(Event.created_at).label("latest_activity_at"),
        )
        .where(Event.project_id == project_id)
        .subquery()
    )
    artifact_stats = (
        select(
            func.count(Artifact.id).filter(visible_memory).label("memory_count"),
            func.count(Artifact.id)
            .filter(visible_memory, Artifact.created_at >= since)
            .label("recent_memory_count"),
        )
        .where(Artifact.project_id == project_id)
        .subquery()
    )
    file_stats = (
        select(
            func.count(ProjectFile.id).label("tracked_file_count"),
            func.count(ProjectFile.id)
            .filter(ProjectFile.changed_at >= since)
            .label("recent_file_count"),
            func.max(ProjectFile.changed_at).label("last_modified_at"),
        )
        .where(
            ProjectFile.project_id == project_id,
            ProjectFile.status != "deleted",
        )
        .subquery()
    )
    row = (
        db.execute(
            select(
                session_stats.c.session_count,
                session_stats.c.recent_session_count,
                session_stats.c.models,
                session_stats.c.tools,
                event_stats.c.event_count,
                event_stats.c.prompt_count,
                event_stats.c.recent_prompt_count,
                event_stats.c.latest_activity_at,
                artifact_stats.c.memory_count,
                artifact_stats.c.recent_memory_count,
                file_stats.c.tracked_file_count,
                file_stats.c.recent_file_count,
                file_stats.c.last_modified_at,
            ).select_from(
                session_stats.join(event_stats, true())
                .join(artifact_stats, true())
                .join(file_stats, true())
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


def read_project_files_response(
    project_id: UUID,
    current_user: User,
    db: Session,
    *,
    limit: int = PROJECT_FILE_TREE_LIMIT,
) -> dict[str, Any]:
    project = project_for_user(db, project_id, current_user, allow_admin=True)
    total = int(
        db.scalar(
            select(func.count(ProjectFile.id)).where(
                ProjectFile.project_id == project.id,
                ProjectFile.status != "deleted",
            )
        )
        or 0
    )
    file_paths = list(
        db.execute(
            select(ProjectFile.path)
            .where(
                ProjectFile.project_id == project.id,
                ProjectFile.status != "deleted",
            )
            .order_by(ProjectFile.path)
            .limit(limit + 1)
        ).scalars()
    )
    page_paths = file_paths[:limit]
    return {
        "files": build_file_tree(page_paths),
        "limit": limit,
        "total": total,
        "truncated": len(file_paths) > limit,
    }


def _activity_summaries(db: Session, project_id: UUID) -> tuple[list[dict[str, Any]], set[str]]:
    event_stats = (
        select(
            Event.session_id.label("session_id"),
            func.count(Event.id).label("event_count"),
            func.count(case((Event.event_type == "PromptSubmitted", 1))).label("prompt_count"),
            func.count(case((Event.event_type == "ResponseReceived", 1))).label("response_count"),
            func.count(case((Event.event_type == "FilesChanged", 1))).label(
                "file_change_event_count"
            ),
            func.min(Event.created_at).label("first_event_at"),
            func.max(Event.created_at).label("last_activity_at"),
            func.max(Event.tool).label("fallback_tool"),
        )
        .where(Event.project_id == project_id)
        .group_by(Event.session_id)
        .order_by(desc(func.max(Event.created_at)))
        .limit(RECENT_ACTIVITY_LIMIT)
        .cte("recent_project_activity")
    )
    changed_file_stats = (
        select(
            CodeChangePatch.session_id.label("session_id"),
            func.count(func.distinct(CodeChangePatch.path)).label("files_changed"),
        )
        .where(
            CodeChangePatch.project_id == project_id,
            CodeChangePatch.session_id.in_(select(event_stats.c.session_id)),
        )
        .group_by(CodeChangePatch.session_id)
        .cte("recent_project_file_changes")
    )
    rows = db.execute(
        select(
            event_stats.c.session_id,
            event_stats.c.event_count,
            event_stats.c.prompt_count,
            event_stats.c.response_count,
            event_stats.c.file_change_event_count,
            event_stats.c.first_event_at,
            event_stats.c.last_activity_at,
            event_stats.c.fallback_tool,
            PromptSession.model,
            PromptSession.tool,
            PromptSession.started_at,
            func.coalesce(changed_file_stats.c.files_changed, 0),
        )
        .outerjoin(PromptSession, PromptSession.id == event_stats.c.session_id)
        .outerjoin(
            changed_file_stats,
            changed_file_stats.c.session_id == event_stats.c.session_id,
        )
        .order_by(desc(event_stats.c.last_activity_at))
    ).all()
    legacy_session_ids = [
        session_id
        for (
            session_id,
            _event_count,
            _prompt_count,
            _response_count,
            file_change_event_count,
            _first_event_at,
            _last_activity_at,
            _fallback_tool,
            _session_model,
            _session_tool,
            _session_started_at,
            files_changed,
        ) in rows
        if file_change_event_count and not files_changed
    ]
    changed_paths_by_session: dict[UUID, set[str]] = {}
    if legacy_session_ids:
        for session_id, payload in db.execute(
            select(Event.session_id, Event.payload).where(
                Event.project_id == project_id,
                Event.session_id.in_(legacy_session_ids),
                Event.event_type == "FilesChanged",
            )
        ).all():
            if not isinstance(payload, dict):
                continue
            changed_paths = changed_paths_by_session.setdefault(session_id, set())
            changed_paths.update(
                change["path"]
                for change in file_changes_from_files_changed(payload)
                if isinstance(change.get("path"), str)
            )
    tools: set[str] = set()
    activities: list[dict[str, Any]] = []
    for (
        session_id,
        event_count,
        prompt_count,
        response_count,
        _file_change_event_count,
        first_event_at,
        last_activity_at,
        fallback_tool,
        raw_session_model,
        raw_session_tool,
        session_started_at,
        files_changed,
    ) in rows:
        session_tool = raw_session_tool or fallback_tool
        if session_tool:
            tools.add(tool_label(session_tool))
        started_at = session_started_at or first_event_at
        if first_event_at is not None and started_at is not None and first_event_at < started_at:
            started_at = first_event_at
        session_model = model_name(raw_session_model)
        activities.append(
            {
                "events": int(event_count or 0),
                "files_changed": int(files_changed or 0)
                or len(changed_paths_by_session.get(session_id, set())),
                "id": str(session_id),
                "last_activity_at": iso(last_activity_at),
                "model": session_model or tool_label(session_tool or "unknown"),
                "prompts": int(prompt_count or 0),
                "responses": int(response_count or 0),
                "started_at": iso(started_at),
            }
        )
    return activities, tools


def _prompt_file_changes(
    db: Session,
    *,
    project_id: UUID,
    prompt_events: list[Event],
) -> dict[str, list[dict[str, Any]]]:
    prompt_event_ids = {event.id for event in prompt_events}
    prompt_event_id_values = list(prompt_event_ids)
    prompt_event_id_strings = {str(prompt_event_id) for prompt_event_id in prompt_event_ids}

    prompt_changes: dict[str, list[dict[str, Any]]] = {}
    prompt_changes = patch_file_changes_by_prompt(
        db,
        descending=True,
        project_id=project_id,
        prompt_event_ids=prompt_event_id_values,
    )

    if not prompt_events:
        return prompt_changes
    if prompt_changes:
        return prompt_changes

    prompt_session_ids = {event.session_id for event in prompt_events}
    earliest_prompt_at = min(event.created_at for event in prompt_events)
    sequence_filters = _prompt_related_sequence_filters(
        prompt_events,
        trailing=PROMPT_RELATED_EVENT_SEQUENCE_TRAILING,
    )
    fallback_events = list(
        db.execute(
            select(Event)
            .where(
                Event.project_id == project_id,
                Event.event_type == "FilesChanged",
                Event.session_id.in_(prompt_session_ids),
                Event.created_at >= earliest_prompt_at,
                or_(*sequence_filters),
            )
            .order_by(desc(Event.created_at), desc(Event.sequence))
            .limit(PROMPT_RELATED_EVENT_LIMIT)
        ).scalars()
    )
    fallback_payloads = {
        event.id: decrypt_event_payload(event.event_type, event.payload)
        for event in fallback_events
    }
    for prompt_event_id, changes in files_changed_by_prompt_from_events(
        fallback_events,
        fallback_payloads,
        existing_prompt_ids=set(prompt_changes),
        prompt_event_ids=prompt_event_id_strings,
    ).items():
        prompt_changes.setdefault(prompt_event_id, []).extend(changes)

    return prompt_changes


def _prompt_related_sequence_filters(
    prompt_events: list[Event],
    *,
    trailing: int,
) -> list[Any]:
    windows: dict[UUID, tuple[int, int]] = {}
    for event in prompt_events:
        minimum, maximum = windows.get(event.session_id, (event.sequence, event.sequence))
        windows[event.session_id] = (
            min(minimum, event.sequence),
            max(maximum, event.sequence),
        )
    return [
        and_(
            Event.session_id == session_id,
            Event.sequence >= minimum,
            Event.sequence <= maximum + trailing,
        )
        for session_id, (minimum, maximum) in windows.items()
    ]


def _prompt_responses(
    db: Session,
    *,
    project_id: UUID,
    prompt_events: list[Event],
    prompt_payloads: dict[UUID, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not prompt_events:
        return {}

    prompt_session_ids = {event.session_id for event in prompt_events}
    earliest_prompt_at = min(event.created_at for event in prompt_events)
    sequence_filters = _prompt_related_sequence_filters(
        prompt_events,
        trailing=PROMPT_RELATED_EVENT_SEQUENCE_TRAILING,
    )
    response_events = list(
        db.execute(
            select(Event)
            .where(
                Event.project_id == project_id,
                Event.event_type == "ResponseReceived",
                Event.session_id.in_(prompt_session_ids),
                Event.created_at >= earliest_prompt_at,
                or_(*sequence_filters),
            )
            .order_by(desc(Event.created_at), desc(Event.sequence))
            .limit(PROMPT_RELATED_EVENT_LIMIT)
        ).scalars()
    )

    response_payloads = {
        event.id: decrypt_event_payload(event.event_type, event.payload)
        for event in response_events
    }
    return {
        prompt_event_id: format_response_summary(event, payload)
        for prompt_event_id, (event, payload) in response_payloads_by_prompt(
            [*prompt_events, *response_events],
            {**prompt_payloads, **response_payloads},
        ).items()
    }


def _prompt_activity_items(
    db: Session,
    *,
    project_id: UUID,
    prompt_events: list[Event],
    prompt_payloads: dict[UUID, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not prompt_events:
        return []

    payloads = prompt_payloads or {
        event.id: decrypt_event_payload(event.event_type, event.payload) for event in prompt_events
    }
    prompt_changes = _prompt_file_changes(
        db,
        project_id=project_id,
        prompt_events=prompt_events,
    )
    prompt_responses = _prompt_responses(
        db,
        project_id=project_id,
        prompt_events=prompt_events,
        prompt_payloads=payloads,
    )

    return [
        {
            "continuation_of": payloads[event.id].get("continuation_of")
            if isinstance(payloads[event.id].get("continuation_of"), str)
            else None,
            "delivery_mode": payloads[event.id].get("delivery_mode")
            if payloads[event.id].get("delivery_mode") in {"unknown", "queued", "interrupt"}
            else None,
            "file_changes": prompt_changes.get(str(event.id), []),
            "files_changed": len(
                {change["path"] for change in prompt_changes.get(str(event.id), [])}
            ),
            "id": str(event.id),
            "model": payload_model(payloads[event.id], event.tool),
            "prompt": payload_prompt(payloads[event.id]),
            "prompt_original_length": payloads[event.id].get("prompt_original_length")
            if isinstance(payloads[event.id].get("prompt_original_length"), int)
            else None,
            "prompt_storage_limit": payloads[event.id].get("prompt_storage_limit")
            if isinstance(payloads[event.id].get("prompt_storage_limit"), int)
            else None,
            "prompt_truncated": payloads[event.id].get("prompt_truncated") is True,
            **prompt_responses.get(str(event.id), {}),
            "root_prompt_event_id": payloads[event.id].get("root_prompt_event_id")
            if isinstance(payloads[event.id].get("root_prompt_event_id"), str)
            else None,
            "sequence": event.sequence,
            "session_id": str(event.session_id) if event.session_id is not None else None,
            "submission_context": payloads[event.id].get("submission_context")
            if payloads[event.id].get("submission_context") in {"idle", "during_output"}
            else None,
            "submitted_at": iso(event.created_at),
        }
        for event in prompt_events
    ]


def _encode_prompt_activity_cursor(event: Event) -> str:
    payload = {
        "created_at": event.created_at.isoformat(),
        "id": str(event.id),
        "sequence": event.sequence,
    }
    return base64_urlencode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )


def _decode_prompt_activity_cursor(value: str | None) -> PromptActivityCursor | None:
    if not value:
        return None

    try:
        payload = json.loads(base64_urldecode(value))
        created_at_value = payload["created_at"]
        sequence = payload["sequence"]
        event_id = UUID(payload["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid prompt activity cursor",
        ) from exc

    if not isinstance(created_at_value, str) or not isinstance(sequence, int):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid prompt activity cursor",
        )

    try:
        created_at = datetime.fromisoformat(created_at_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid prompt activity cursor",
        ) from exc
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at, sequence, event_id


def _prompt_activity_order_by() -> tuple[Any, Any, Any]:
    return desc(Event.created_at), desc(Event.sequence), desc(Event.id)


def _apply_prompt_activity_cursor(statement: Any, cursor: PromptActivityCursor | None) -> Any:
    if cursor is None:
        return statement

    created_at, sequence, event_id = cursor
    return statement.where(
        or_(
            Event.created_at < created_at,
            and_(Event.created_at == created_at, Event.sequence < sequence),
            and_(
                Event.created_at == created_at,
                Event.sequence == sequence,
                Event.id < event_id,
            ),
        )
    )


def read_project_prompt_activities_response(
    project_id: UUID,
    current_user: User,
    db: Session,
    *,
    limit: int,
    cursor: str | None = None,
    query: str | None = None,
    session_id: UUID | None = None,
) -> dict[str, Any]:
    project = project_for_user(db, project_id, current_user, allow_admin=True)
    normalized_query = re.sub(r"\s+", " ", query.strip().lower()) if query else ""
    decoded_cursor = _decode_prompt_activity_cursor(cursor)
    base_query = select(Event).where(
        Event.project_id == project.id,
        Event.event_type == "PromptSubmitted",
    )
    total_query = select(func.count(Event.id)).where(
        Event.project_id == project.id,
        Event.event_type == "PromptSubmitted",
    )
    if session_id is not None:
        base_query = base_query.where(Event.session_id == session_id)
        total_query = total_query.where(Event.session_id == session_id)

    if not normalized_query:
        total = int(db.scalar(total_query) or 0)
        prompt_events = list(
            db.execute(
                _apply_prompt_activity_cursor(base_query, decoded_cursor)
                .order_by(*_prompt_activity_order_by())
                .limit(limit + 1)
            ).scalars()
        )
        page_events = prompt_events[:limit]
        has_more = len(prompt_events) > limit
        return {
            "cursor": cursor,
            "has_more": has_more,
            "items": _prompt_activity_items(
                db,
                project_id=project.id,
                prompt_events=page_events,
            ),
            "limit": limit,
            "next_cursor": _encode_prompt_activity_cursor(page_events[-1])
            if has_more and page_events
            else None,
            "query": None,
            "scanned": len(page_events),
            "session_id": str(session_id) if session_id else None,
            "total": total,
        }

    query_hashes = prompt_search_hashes_for_query(normalized_query)
    if not query_hashes:
        return {
            "cursor": cursor,
            "has_more": False,
            "items": [],
            "limit": limit,
            "next_cursor": None,
            "query": normalized_query,
            "scanned": 0,
            "session_id": str(session_id) if session_id else None,
            "total": None,
        }

    search_query = (
        select(Event)
        .join(
            PromptSearchDocument,
            PromptSearchDocument.prompt_event_id == Event.id,
        )
        .where(
            Event.project_id == project.id,
            Event.event_type == "PromptSubmitted",
            PromptSearchDocument.project_id == project.id,
            PromptSearchDocument.token_hashes.contains(query_hashes),
        )
    )
    if session_id is not None:
        search_query = search_query.where(
            Event.session_id == session_id,
            PromptSearchDocument.session_id == session_id,
        )

    matched_events = list(
        db.execute(
            _apply_prompt_activity_cursor(search_query, decoded_cursor)
            .order_by(*_prompt_activity_order_by())
            .limit(limit + 1)
        ).scalars()
    )
    page_events = matched_events[:limit]
    has_more = len(matched_events) > limit
    return {
        "cursor": cursor,
        "has_more": has_more,
        "items": _prompt_activity_items(
            db,
            project_id=project.id,
            prompt_events=page_events,
        ),
        "limit": limit,
        "next_cursor": _encode_prompt_activity_cursor(page_events[-1])
        if has_more and page_events
        else None,
        "query": normalized_query,
        "scanned": len(page_events),
        "session_id": str(session_id) if session_id else None,
        "total": None,
    }


def read_project_detail_response(
    project_id: UUID,
    current_user: User,
    db: Session,
    *,
    allow_public: bool = False,
) -> dict[str, Any]:
    project = project_for_user(
        db,
        project_id,
        current_user,
        allow_admin=True,
        allow_public=allow_public,
    )
    repository_url = normalize_github_url(project.git_remote)
    now = datetime.now(timezone.utc)
    since_yesterday_at = now - timedelta(days=1)
    stats = _project_detail_stats(
        db,
        project_id=project.id,
        since=since_yesterday_at,
    )
    models = {
        model for raw_model in stats["models"] or [] if (model := model_name(raw_model)) is not None
    }
    tools = {tool_label(raw_tool) for raw_tool in stats["tools"] or [] if raw_tool}
    activities, activity_tools = _activity_summaries(db, project.id)
    tools.update(activity_tools)
    memory_artifacts = list_project_memory_artifacts(
        db,
        limit=10,
        project_id=project.id,
    )
    project_memory_artifact = get_latest_project_memory(db, project_id=project.id)
    visible_memory_artifacts = [
        *([project_memory_artifact] if project_memory_artifact is not None else []),
        *memory_artifacts,
    ]
    latest_memory_artifact_at = max(
        (artifact.updated_at for artifact in visible_memory_artifacts),
        default=None,
    )
    activity_history = _project_metric_history(
        db,
        project_id=project.id,
        now=now,
    )

    return {
        "project": {
            "id": str(project.id),
            "slug": project.slug,
            "name": project.name,
            "project_url": project.project_url,
            "description": project.description,
            "created_at": iso(project.created_at),
            "is_bookmarked": bool(project.is_bookmarked),
            "memory_grouping_mode": project.memory_grouping_mode or "session",
            "tags": project.tags or [],
            "visibility": project.visibility,
            "repository_status": "Repository connected"
            if repository_url
            else "Repository not connected",
            "repository_url": repository_url,
            "default_branch": project.default_branch,
            "updated_at": iso(project.updated_at),
        },
        "metrics": {
            "activity_history": activity_history,
            "connected_models": sorted(models),
            "connected_tools": sorted(tools),
            "files_changed_since_yesterday": int(stats["recent_file_count"] or 0),
            "latest_activity_at": iso(stats["latest_activity_at"]),
            "last_modified_at": iso(stats["last_modified_at"] or project.updated_at),
            "memory_artifacts_since_yesterday": int(stats["recent_memory_count"] or 0),
            "prompts_since_yesterday": int(stats["recent_prompt_count"] or 0),
            "repository_connected": repository_url is not None,
            "sessions_since_yesterday": int(stats["recent_session_count"] or 0),
            "tracked_files": int(stats["tracked_file_count"] or 0),
            "total_events": int(stats["event_count"] or 0),
            "total_prompts": int(stats["prompt_count"] or 0),
            "total_sessions": int(stats["session_count"] or 0),
        },
        "memory": {
            "latest_artifact_at": iso(latest_memory_artifact_at),
            "recent_artifacts": [
                serialize_memory_artifact_summary(artifact) for artifact in visible_memory_artifacts
            ],
            "total_artifacts": int(stats["memory_count"] or 0),
        },
        "activities": activities,
        "prompt_activities": [],
        "files": [],
    }
