from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.core.config import settings
from app.models.events import Event
from app.models.projects import Project
from app.models.sessions import Session
from app.services.event_payload_security import decrypt_event_payload
from app.services.memory.constants import (
    LONG_TEXT_AI_PREVIEW_AFTER,
    LONG_TEXT_AI_PREVIEW_EDGE,
    PENDING_MEMORY_DRAFT_GENERATOR,
)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def string_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def truncate(value: Any, limit: int = 220) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = " ".join(value.split())
    return cleaned if len(cleaned) <= limit else f"{cleaned[: limit - 3].rstrip()}..."


def is_generic_local_memory_summary(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    return "prompts and" in value.lower() and "ai responses were captured" in value.lower()


def _long_text_ai_preview(value: str, *, label: str) -> str:
    head = value[:LONG_TEXT_AI_PREVIEW_EDGE]
    tail = value[-LONG_TEXT_AI_PREVIEW_EDGE:]
    return f"[Long {label} preview: original_size={len(value)} chars]\nHead: {head}\nTail: {tail}"


def _ai_text_preview(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    if len(value) <= LONG_TEXT_AI_PREVIEW_AFTER:
        return value
    return _long_text_ai_preview(value, label=label)


def _ai_text_metadata(value: str | None, *, prefix: str) -> dict[str, Any]:
    if value is None or len(value) <= LONG_TEXT_AI_PREVIEW_AFTER:
        return {
            f"{prefix}_ai_preview_truncated": False,
            f"{prefix}_original_size": len(value) if value is not None else None,
        }
    return {
        f"{prefix}_ai_preview_policy": "head_300_tail_300_size",
        f"{prefix}_ai_preview_truncated": True,
        f"{prefix}_original_size": len(value),
    }


def _prompt_ai_text(value: str | None) -> str | None:
    return _ai_text_preview(value, label="prompt")


def _prompt_ai_metadata(value: str | None) -> dict[str, Any]:
    return _ai_text_metadata(value, prefix="prompt")


def _response_ai_text(value: str | None) -> str | None:
    return _ai_text_preview(value, label="AI output")


def _response_ai_metadata(value: str | None) -> dict[str, Any]:
    return _ai_text_metadata(value, prefix="response")


def payload(event: Event) -> dict[str, Any]:
    return decrypt_event_payload(event.event_type, event.payload)


def event_model(event: Event, payload: dict[str, Any]) -> str | None:
    model = string_or_none(payload.get("model"))
    return model if model and model.lower() not in {event.tool, "codex", "cursor"} else None


def changed_files_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    changes = payload.get("changes")
    if isinstance(changes, list):
        for change in changes:
            if not isinstance(change, dict):
                continue
            path = string_or_none(change.get("path"))
            if not path:
                continue
            files.append(
                {
                    "additions": change.get("additions")
                    if isinstance(change.get("additions"), int)
                    else change.get("insertions_delta")
                    if isinstance(change.get("insertions_delta"), int)
                    else None,
                    "deletions": change.get("deletions")
                    if isinstance(change.get("deletions"), int)
                    else change.get("deletions_delta")
                    if isinstance(change.get("deletions_delta"), int)
                    else None,
                    "binary": change.get("binary") is True,
                    "old_path": string_or_none(change.get("old_path")),
                    "patch": change.get("patch") if isinstance(change.get("patch"), str) else None,
                    "patch_omitted_reason": string_or_none(change.get("patch_omitted_reason")),
                    "patch_truncated": change.get("patch_truncated") is True,
                    "path": path,
                    "status": string_or_none(change.get("status")) or "changed",
                }
            )
        return files

    raw_files = payload.get("files")
    if isinstance(raw_files, list):
        return [
            {"additions": None, "deletions": None, "path": path, "status": "changed"}
            for path in raw_files
            if isinstance(path, str) and path
        ]
    return []


def event_context_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event_type == "PromptSubmitted":
        prompt = string_or_none(payload.get("prompt"))
        return {
            "prompt": truncate(_prompt_ai_text(prompt) or "", 600),
            "turn_id": payload.get("turn_id"),
            **_prompt_ai_metadata(prompt),
        }
    if event_type == "ResponseReceived":
        response = string_or_none(payload.get("response"))
        return {
            "response": truncate(_response_ai_text(response) or "", 500),
            "success": payload.get("success"),
            "turn_id": payload.get("turn_id"),
            **_response_ai_metadata(response),
        }
    if event_type == "FilesChanged":
        return {
            "change_detection_complete": payload.get("change_detection_complete") is True,
            "files": [
                truncate(file["path"], 240) for file in changed_files_from_payload(payload)[:8]
            ],
            "no_changes": payload.get("no_changes") is True,
        }
    if event_type == "CommitCreated":
        return {
            "hash": string_or_none(payload.get("hash")),
            "message": truncate(string_or_none(payload.get("message")) or "", 240),
        }
    return {}


def dedupe_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for file in files:
        path = file["path"]
        current = merged.setdefault(
            path,
            {
                "additions": 0,
                "binary": file.get("binary") is True,
                "deletions": 0,
                "old_path": file.get("old_path"),
                "patch": file.get("patch"),
                "patch_omitted_reason": file.get("patch_omitted_reason"),
                "patch_truncated": file.get("patch_truncated") is True,
                "path": path,
                "status": file.get("status") or "changed",
            },
        )
        current["status"] = file.get("status") or current["status"]
        for key in ("old_path", "patch", "patch_omitted_reason"):
            if current.get(key) is None and file.get(key) is not None:
                current[key] = file.get(key)
        current["binary"] = current.get("binary") is True or file.get("binary") is True
        current["patch_truncated"] = (
            current.get("patch_truncated") is True or file.get("patch_truncated") is True
        )
        if isinstance(file.get("additions"), int):
            current["additions"] += file["additions"]
        if isinstance(file.get("deletions"), int):
            current["deletions"] += file["deletions"]

    return [
        {
            **file,
            "additions": file["additions"] if file["additions"] > 0 else None,
            "deletions": file["deletions"] if file["deletions"] > 0 else None,
        }
        for file in sorted(merged.values(), key=lambda item: item["path"])
    ]


def tags_for_session(
    *,
    changed_files: list[dict[str, Any]],
    model: str | None,
    tool: str,
) -> list[str]:
    tags = {"memory", tool}
    if model:
        tags.add(model.lower().replace(" ", "-"))
    for file in changed_files[:20]:
        path = file["path"]
        if "." in path:
            tags.add(path.rsplit(".", 1)[1].lower())
    return sorted(tags)[:12]


def technologies_for_session(changed_files: list[dict[str, Any]]) -> list[str]:
    technologies: set[str] = set()
    extension_map = {
        "css": "CSS",
        "html": "HTML",
        "js": "JavaScript",
        "json": "JSON",
        "jsx": "React",
        "md": "Markdown",
        "py": "Python",
        "sql": "SQL",
        "ts": "TypeScript",
        "tsx": "React",
        "yml": "YAML",
        "yaml": "YAML",
    }
    path_markers = {
        "alembic/": "Alembic",
        "app/api/": "FastAPI",
        "app/models/": "SQLAlchemy",
        "backend/": "FastAPI",
        "frontend/": "React",
    }

    for file in changed_files:
        path = file["path"]
        normalized_path = path.lower()
        for marker, technology in path_markers.items():
            if marker in normalized_path:
                technologies.add(technology)
        if "." in normalized_path:
            extension = normalized_path.rsplit(".", 1)[1]
            technology = extension_map.get(extension)
            if technology:
                technologies.add(technology)

    return sorted(technologies)[:12]


def title_from_session(
    *,
    prompts: list[dict[str, Any]],
    project_name: str,
) -> str:
    for prompt in prompts:
        prompt_text = string_or_none(prompt.get("prompt"))
        if prompt_text:
            return truncate(prompt_text.splitlines()[0], 120)
    return f"{project_name} development session"


def build_session_memory_context(
    db: DBSession,
    session: Session,
    *,
    context_event_rows: list[Event] | None = None,
    end_sequence: int | None = None,
    event_rows: list[Event] | None = None,
    slice_metadata: dict[str, Any] | None = None,
    start_sequence: int | None = None,
) -> dict[str, Any]:
    project = session.project or db.get(Project, session.project_id)
    if event_rows is None:
        query = select(Event).where(
            Event.project_id == session.project_id,
            Event.session_id == session.id,
        )
        if start_sequence is not None:
            query = query.where(Event.sequence >= start_sequence)
        if end_sequence is not None:
            query = query.where(Event.sequence <= end_sequence)
        events = list(db.execute(query.order_by(Event.sequence, Event.created_at)).scalars())
    else:
        events = event_rows
    coverage_event_ids = {event.id for event in events}
    context_events = [
        event for event in (context_event_rows or []) if event.id not in coverage_event_ids
    ]
    context_event_ids = {event.id for event in context_events}
    payloads = [(event, payload(event)) for event in [*events, *context_events]]
    coverage_payloads = payloads[: len(events)]
    prompt_events = [
        {
            "context_only": event.id in context_event_ids,
            "id": str(event.id),
            "prompt": _prompt_ai_text(string_or_none(event_payload.get("prompt"))),
            "prompt_original": string_or_none(event_payload.get("prompt")),
            **_prompt_ai_metadata(string_or_none(event_payload.get("prompt"))),
            "sequence": event.sequence,
            "turn_id": event_payload.get("turn_id"),
        }
        for event, event_payload in payloads
        if event.event_type == "PromptSubmitted"
    ]
    responses = [
        {
            "context_only": event.id in context_event_ids,
            "id": str(event.id),
            "response": _response_ai_text(string_or_none(event_payload.get("response"))),
            "response_original": string_or_none(event_payload.get("response")),
            **_response_ai_metadata(string_or_none(event_payload.get("response"))),
            "sequence": event.sequence,
            "turn_id": event_payload.get("turn_id"),
        }
        for event, event_payload in payloads
        if event.event_type == "ResponseReceived"
    ]
    response_count = sum(1 for event in events if event.event_type == "ResponseReceived")
    commits = [
        {
            "hash": string_or_none(event_payload.get("hash")),
            "message": string_or_none(event_payload.get("message")),
        }
        for event, event_payload in payloads
        if event.event_type == "CommitCreated"
    ]
    changed_files = dedupe_files(
        [
            file
            for event, event_payload in payloads
            if event.event_type == "FilesChanged"
            for file in changed_files_from_payload(event_payload)
        ]
    )
    model = next(
        (
            model
            for event, event_payload in payloads
            if (model := event_model(event, event_payload)) is not None
        ),
        session.model,
    )

    return {
        "changed_files": changed_files,
        "commits": commits,
        "ended_at": iso(session.ended_at),
        "event_count": len(events),
        "events": [
            {
                "event_type": event.event_type,
                "payload": event_context_payload(event.event_type, event_payload),
                "sequence": event.sequence,
                "timestamp": iso(event.created_at),
            }
            for event, event_payload in coverage_payloads
        ],
        "first_event_id": str(events[0].id) if events else None,
        "last_event_id": str(events[-1].id) if events else None,
        "model": model,
        "project_id": str(session.project_id),
        "project_name": project.name,
        "prompt_events": prompt_events,
        "response_count": response_count,
        "responses": responses,
        "slice": slice_metadata or None,
        "session_id": str(session.id),
        "started_at": iso(session.started_at),
        "tool": session.tool,
    }


def _ai_ready_text(value: str | None) -> dict[str, Any]:
    if not value:
        return {
            "original_length": 0,
            "text": "",
            "truncated_for_ai": False,
        }
    if len(value) <= LONG_TEXT_AI_PREVIEW_AFTER:
        return {
            "original_length": len(value),
            "text": value,
            "truncated_for_ai": False,
        }
    return {
        "head_chars": LONG_TEXT_AI_PREVIEW_EDGE,
        "original_length": len(value),
        "tail_chars": LONG_TEXT_AI_PREVIEW_EDGE,
        "text": (
            f"{value[:LONG_TEXT_AI_PREVIEW_EDGE]}\n"
            "[...prompt clipped for AI memory generation...]\n"
            f"{value[-LONG_TEXT_AI_PREVIEW_EDGE:]}"
        ),
        "truncated_for_ai": True,
    }


def _edge_sample(values: list[Any], limit: int) -> list[Any]:
    if limit <= 0:
        return []
    if len(values) <= limit:
        return list(values)
    first_count = max(1, limit // 3)
    return [*values[:first_count], *values[-(limit - first_count) :]]


def _compact_changed_file_metadata(file: dict[str, Any]) -> dict[str, Any]:
    return {
        "additions": file.get("additions"),
        "binary": file.get("binary") is True,
        "deletions": file.get("deletions"),
        "old_path": truncate(file.get("old_path"), 240) or None,
        "patch_omitted_reason": truncate(file.get("patch_omitted_reason"), 120) or None,
        "patch_truncated": file.get("patch_truncated") is True,
        "path": truncate(file.get("path"), 240),
        "status": truncate(file.get("status"), 32) or "changed",
    }


def _compact_commit_metadata(commit: dict[str, Any]) -> dict[str, Any]:
    return {
        "hash": truncate(commit.get("hash"), 64) or None,
        "message": truncate(commit.get("message"), 240) or None,
    }


def _evidence_size(value: dict[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _bounded_pending_evidence(
    evidence: dict[str, Any],
    *,
    original_counts: dict[str, int],
) -> dict[str, Any]:
    byte_limit = max(settings.memory_draft_evidence_max_bytes, 4_096)

    def candidate(limits: dict[str, int]) -> dict[str, Any]:
        bounded = {
            **evidence,
            **{
                key: _edge_sample(evidence[key], limits[key])
                for key in ("changed_files", "commits", "events", "prompts", "responses")
            },
        }
        bounded["omitted"] = {
            key: max(original_counts[key] - len(bounded[key]), 0) for key in original_counts
        }
        return bounded

    for limits in (
        {"changed_files": 100, "commits": 20, "events": 80, "prompts": 40, "responses": 40},
        {"changed_files": 60, "commits": 12, "events": 40, "prompts": 20, "responses": 20},
        {"changed_files": 30, "commits": 8, "events": 20, "prompts": 10, "responses": 10},
        {"changed_files": 12, "commits": 4, "events": 8, "prompts": 4, "responses": 4},
    ):
        bounded = candidate(limits)
        if _evidence_size(bounded) <= byte_limit:
            return bounded

    minimal = candidate(
        {"changed_files": 4, "commits": 2, "events": 0, "prompts": 2, "responses": 2}
    )
    for prompt in minimal["prompts"]:
        ai_input = prompt.get("ai_input") if isinstance(prompt.get("ai_input"), dict) else {}
        prompt["ai_input"] = {
            **ai_input,
            "text": truncate(ai_input.get("text"), 160),
        }
    for response in minimal["responses"]:
        response["output_preview"] = truncate(response.get("output_preview"), 160)
    if _evidence_size(minimal) <= byte_limit:
        return minimal

    counts_only = candidate(
        {"changed_files": 0, "commits": 0, "events": 0, "prompts": 0, "responses": 0}
    )
    return counts_only


def pending_draft_evidence_from_context(context: dict[str, Any]) -> dict[str, Any]:
    prompt_events = context["prompt_events"]
    responses = context["responses"]
    changed_files = context["changed_files"]
    response_by_turn = {
        str(response.get("turn_id")): response
        for response in responses
        if response.get("turn_id") is not None
    }
    response_by_sequence = list(responses)

    prompts: list[dict[str, Any]] = []
    for prompt in prompt_events:
        raw_prompt = string_or_none(prompt.get("prompt_original")) or string_or_none(
            prompt.get("prompt")
        )
        paired_response = None
        if prompt.get("turn_id") is not None:
            paired_response = response_by_turn.get(str(prompt.get("turn_id")))
        if paired_response is None:
            paired_response = next(
                (
                    response
                    for response in response_by_sequence
                    if isinstance(response.get("sequence"), int)
                    and isinstance(prompt.get("sequence"), int)
                    and response["sequence"] > prompt["sequence"]
                ),
                None,
            )
        ai_input = _ai_ready_text(raw_prompt)
        ai_input["text"] = truncate(ai_input.get("text"), 700)
        ai_input["truncated_for_ai"] = ai_input.get("truncated_for_ai") is True or (
            raw_prompt is not None and len(raw_prompt) > len(ai_input["text"])
        )
        prompts.append(
            {
                "ai_input": ai_input,
                "context_only": prompt.get("context_only") is True,
                "event_id": prompt.get("id"),
                "original_length": prompt.get("prompt_original_size")
                or (len(raw_prompt) if raw_prompt else 0),
                "paired_response_event_id": paired_response.get("id")
                if isinstance(paired_response, dict)
                else None,
                "sequence": prompt.get("sequence"),
                "storage_truncated": (
                    prompt.get("prompt_ai_preview_truncated") is True
                    or ai_input.get("truncated_for_ai") is True
                ),
                "turn_id": prompt.get("turn_id"),
            }
        )

    evidence = {
        "changed_files": [
            _compact_changed_file_metadata(file)
            for file in changed_files
            if isinstance(file, dict) and file.get("path")
        ],
        "commits": [
            _compact_commit_metadata(commit)
            for commit in context["commits"]
            if isinstance(commit, dict)
        ],
        "events": context["events"],
        "prompts": prompts,
        "responses": [
            {
                "context_only": response.get("context_only") is True,
                "event_id": response.get("id"),
                "original_length": response.get("response_original_size")
                or len(response.get("response_original") or response.get("response") or ""),
                "output_preview": truncate(
                    string_or_none(response.get("response_original"))
                    or string_or_none(response.get("response")),
                    500,
                ),
                "sequence": response.get("sequence"),
                "storage_truncated": (
                    response.get("response_ai_preview_truncated") is True
                    or len(response.get("response_original") or response.get("response") or "")
                    > 500
                ),
                "turn_id": response.get("turn_id"),
            }
            for response in responses
        ],
        "schema_version": 2,
        "session": {
            "ended_at": context.get("ended_at"),
            "id": context.get("session_id"),
            "model": context.get("model"),
            "project_id": context.get("project_id"),
            "project_name": context.get("project_name"),
            "started_at": context.get("started_at"),
            "tool": context.get("tool"),
        },
    }
    return _bounded_pending_evidence(
        evidence,
        original_counts={
            key: len(evidence[key])
            for key in ("changed_files", "commits", "events", "prompts", "responses")
        },
    )


def build_pending_memory_draft_payload(
    context: dict[str, Any],
    *,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if evidence is None:
        evidence = pending_draft_evidence_from_context(context)
    omitted = evidence.get("omitted") if isinstance(evidence.get("omitted"), dict) else {}

    def evidence_count(key: str) -> int:
        omitted_count = omitted.get(key)
        return len(evidence[key]) + (omitted_count if isinstance(omitted_count, int) else 0)

    prompt_count = evidence_count("prompts")
    response_count = evidence_count("responses")
    file_count = evidence_count("changed_files")
    slice_metadata = context.get("slice") if isinstance(context.get("slice"), dict) else {}
    is_continuation = (
        slice_metadata.get("window_reason") == "event_count_continuation"
        or slice_metadata.get("window_truncated") is True
    )
    memory_slice_kind = slice_metadata.get("memory_slice_kind")
    title = title_from_session(
        prompts=context["prompt_events"],
        project_name=context["project_name"],
    )
    summary = (
        f"{prompt_count} prompts are ready with {response_count} AI responses "
        f"and {file_count} changed files for one-time memory generation."
    )
    sections = [
        {
            "summary": f"{prompt_count} original user prompt inputs captured.",
            "title": "User inputs",
        },
        {
            "summary": f"{response_count} original AI outputs captured.",
            "title": "AI outputs",
        },
        {
            "summary": f"{file_count} changed files captured.",
            "title": "File changes",
        },
    ]
    return {
        "changed_files": evidence["changed_files"][:100],
        "commit_sha": evidence["commits"][-1]["hash"] if evidence["commits"] else None,
        "event_count": context["event_count"],
        "first_event_id": context["first_event_id"],
        "generator": PENDING_MEMORY_DRAFT_GENERATOR,
        "last_event_id": context["last_event_id"],
        "model": context["model"],
        "outcome": "Pending AI memory generation.",
        "prompt_event_ids": [
            prompt["id"]
            for prompt in context["prompt_events"]
            if prompt.get("context_only") is not True
        ],
        "reason": (
            "Bounded continuation of an eligible prompt window; the prompt is context-only "
            "and event coverage remains unique to this slice."
            if is_continuation
            else (
                "Prompt input and AI output are available for reasoning memory generation; "
                "no file change was required."
                if memory_slice_kind == "reasoning"
                else "Prompt input, AI output, and file-change detection are all available."
            )
        ),
        "sections": sections,
        "summary": summary,
        "tags": sorted(
            set(
                [
                    *tags_for_session(
                        changed_files=context["changed_files"],
                        model=context["model"],
                        tool=context["tool"],
                    ),
                    memory_slice_kind
                    if memory_slice_kind in {"implementation", "reasoning"}
                    else "continuation",
                    "pending-draft",
                ]
            )
        )[:12],
        "technologies": technologies_for_session(context["changed_files"]),
        "title": f"Pending memory draft: {title}",
        "tool": context["tool"],
    }
