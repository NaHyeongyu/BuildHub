from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

SupportedTool = Literal["claude-code", "codex-cli", "cursor", "gemini-cli"]
EventType = Literal[
    "SessionStarted",
    "PromptSubmitted",
    "ResponseReceived",
    "FilesChanged",
    "CommitCreated",
    "SessionEnded",
]

SUPPORTED_TOOLS: tuple[SupportedTool, ...] = (
    "claude-code",
    "codex-cli",
    "cursor",
    "gemini-cli",
)
SUPPORTED_EVENT_TYPES: tuple[EventType, ...] = (
    "SessionStarted",
    "PromptSubmitted",
    "ResponseReceived",
    "FilesChanged",
    "CommitCreated",
    "SessionEnded",
)
EVENT_TYPE_ALIASES: dict[str, EventType] = {
    "SessionStart": "SessionStarted",
    "SessionEnd": "SessionEnded",
    "Stop": "ResponseReceived",
    "SESSION_STARTED": "SessionStarted",
    "PROMPT_SENT": "PromptSubmitted",
    "PROMPT_RESPONSE": "ResponseReceived",
    "FILES_CHANGED": "FilesChanged",
    "COMMIT_CREATED": "CommitCreated",
    "SESSION_ENDED": "SessionEnded",
    "session_started": "SessionStarted",
    "prompt_sent": "PromptSubmitted",
    "prompt_submitted": "PromptSubmitted",
    "response_received": "ResponseReceived",
    "prompt_response": "ResponseReceived",
    "files_changed": "FilesChanged",
    "commit_created": "CommitCreated",
    "session_ended": "SessionEnded",
}

TOOL_ALIASES: dict[str, SupportedTool] = {
    "claude": "claude-code",
    "claude-code": "claude-code",
    "codex": "codex-cli",
    "codex-cli": "codex-cli",
    "cursor": "cursor",
    "gemini": "gemini-cli",
    "gemini-cli": "gemini-cli",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class PayloadBase:
    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(slots=True)
class SessionStartedPayload(PayloadBase):
    cwd: str | None = None
    branch: str | None = None
    git_remote: str | None = None
    github_url: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    session_id: str | None = None


@dataclass(slots=True)
class PromptSubmittedPayload(PayloadBase):
    prompt: str
    cwd: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    transcript_path: str | None = None
    turn_id: str | int | None = None
    session_id: str | None = None
    branch: str | None = None
    git_remote: str | None = None
    github_url: str | None = None
    hook_event_name: str | None = None
    approval_policy: str | None = None
    sandbox_mode: str | None = None
    submission_context: Literal["idle", "during_output"] | None = None
    delivery_mode: Literal["unknown", "queued", "interrupt"] | None = None
    continuation_of: str | None = None
    root_prompt_event_id: str | None = None


@dataclass(slots=True)
class ResponseReceivedPayload(PayloadBase):
    response: str | None = None
    response_truncated: bool = False
    response_original_length: int | None = None
    response_storage_limit: int | None = None
    response_source: str | None = None
    transcript_path: str | None = None
    turn_id: str | int | None = None
    duration_ms: int | None = None
    success: bool | None = None
    model: str | None = None
    session_id: str | None = None


@dataclass(slots=True)
class FilesChangedPayload(PayloadBase):
    files: list[str] = field(default_factory=list)
    cwd: str | None = None
    session_id: str | None = None
    prompt_event_id: str | None = None
    turn_id: str | int | None = None
    git_root: str | None = None
    branch: str | None = None
    git_remote: str | None = None
    github_url: str | None = None
    base_commit: str | None = None
    head_commit: str | None = None
    baseline_captured_at: str | None = None
    detected_at: str | None = None
    source: str | None = None
    summary: dict[str, Any] | None = None
    changes: list[dict[str, Any]] = field(default_factory=list)
    change_detection_complete: bool | None = None
    no_changes: bool | None = None


@dataclass(slots=True)
class CommitCreatedPayload(PayloadBase):
    hash: str | None = None
    message: str | None = None
    branch: str | None = None
    git_remote: str | None = None
    github_url: str | None = None
    cwd: str | None = None
    session_id: str | None = None


@dataclass(slots=True)
class SessionEndedPayload(PayloadBase):
    reason: str | None = None
    duration: int | None = None
    session_id: str | None = None


EventPayload = (
    SessionStartedPayload
    | PromptSubmittedPayload
    | ResponseReceivedPayload
    | FilesChangedPayload
    | CommitCreatedPayload
    | SessionEndedPayload
)


@dataclass(slots=True)
class BaseEvent:
    tool: SupportedTool
    event_type: EventType
    payload: EventPayload
    project_id: str
    session_id: str
    sequence: int
    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=utc_now_iso)
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "tool": self.tool,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "payload": self.payload.to_dict(),
        }


def normalize_tool(tool: str) -> SupportedTool:
    normalized = TOOL_ALIASES.get(tool)
    if normalized is None:
        raise ValueError(f"Unsupported tool: {tool}")
    return normalized


def normalize_event_type(event_type: str) -> EventType:
    if event_type not in SUPPORTED_EVENT_TYPES:
        alias = EVENT_TYPE_ALIASES.get(event_type)
        if alias:
            return alias
        raise ValueError(f"Unsupported event type: {event_type}")
    return event_type  # type: ignore[return-value]


def coerce_uuid(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return str(UUID(str(value)))
    except ValueError:
        return None


def stable_uuid(namespace_name: str, value: Any) -> str:
    namespace = uuid5(NAMESPACE_DNS, namespace_name)
    return str(uuid5(namespace, str(value)))


def find_project_root(path_value: Any) -> str:
    path = Path(str(path_value)).expanduser()
    if not path.exists():
        return str(path_value)
    try:
        candidate = path if path.is_dir() else path.parent
        candidate = candidate.resolve()
    except OSError:
        return str(path_value)

    for current in (candidate, *candidate.parents):
        if (current / ".git").exists():
            return str(current)
    return str(candidate)


def resolve_project_id(raw_payload: dict[str, Any], event_payload: dict[str, Any]) -> str:
    explicit_project_id = (
        coerce_uuid(raw_payload.get("project_id"))
        or coerce_uuid(event_payload.get("project_id"))
        or coerce_uuid(os.environ.get("PROMTY_PROJECT_ID"))
    )
    if explicit_project_id:
        return explicit_project_id

    project_seed = (
        event_payload.get("cwd")
        or raw_payload.get("cwd")
        or raw_payload.get("workspace")
        or os.getcwd()
    )
    return stable_uuid("prompthub.project", find_project_root(project_seed))


def resolve_session_id(
    tool: SupportedTool,
    project_id: str,
    raw_payload: dict[str, Any],
    event_payload: dict[str, Any],
) -> str:
    explicit_session_id = (
        coerce_uuid(raw_payload.get("session_id"))
        or coerce_uuid(event_payload.get("session_id"))
        or coerce_uuid(os.environ.get("PROMTY_SESSION_ID"))
    )
    if explicit_session_id:
        return explicit_session_id

    session_seed = (
        raw_payload.get("session_id")
        or raw_payload.get("conversation_id")
        or raw_payload.get("thread_id")
        or event_payload.get("session_id")
        or f"{project_id}:{tool}"
    )
    return stable_uuid("prompthub.session", f"{project_id}:{tool}:{session_seed}")


def resolve_sequence(raw_payload: dict[str, Any], event_payload: dict[str, Any]) -> int:
    for key in ("sequence", "seq", "turn_id", "turn", "message_index"):
        value = raw_payload.get(key)
        if value is None:
            value = event_payload.get(key)
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 0


def build_event(
    *,
    tool: SupportedTool,
    event_type: EventType,
    payload: EventPayload,
    raw_payload: dict[str, Any],
) -> BaseEvent:
    event_payload = payload.to_dict()
    project_id = resolve_project_id(raw_payload, event_payload)
    session_id = resolve_session_id(tool, project_id, raw_payload, event_payload)
    sequence = resolve_sequence(raw_payload, event_payload)

    return BaseEvent(
        project_id=project_id,
        session_id=session_id,
        sequence=sequence,
        tool=tool,
        event_type=event_type,
        payload=payload,
    )
