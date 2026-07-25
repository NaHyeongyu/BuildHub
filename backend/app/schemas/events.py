from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SupportedTool = Literal["claude-code", "codex-cli", "cursor", "gemini-cli"]
EventType = Literal[
    "SessionStarted",
    "PromptSubmitted",
    "ResponseReceived",
    "FilesChanged",
    "CommitCreated",
    "SessionEnded",
]

EVENT_FILES_MAX_ITEMS = 2_000
EVENT_CHANGES_MAX_ITEMS = 2_000
EVENT_RESOURCE_ENTRIES_MAX_PER_BATCH = 2_000
EVENT_BATCH_MAX_ITEMS = 100
EVENT_FILE_PATH_MAX_CHARS = 2_048
EVENT_CHANGE_PATCH_MAX_CHARS = 524_288


class PayloadModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionStartedPayload(PayloadModel):
    cwd: str | None = None
    branch: str | None = None
    git_remote: str | None = None
    github_url: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    session_id: str | None = None


class PromptSubmittedPayload(PayloadModel):
    prompt: str
    prompt_truncated: bool = False
    prompt_original_length: int | None = None
    prompt_storage_limit: int | None = None
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


class ResponseReceivedPayload(PayloadModel):
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


class FilesChangedPayload(PayloadModel):
    files: list[str] = Field(default_factory=list, max_length=EVENT_FILES_MAX_ITEMS)
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
    summary: dict | None = None
    changes: list[dict] = Field(default_factory=list, max_length=EVENT_CHANGES_MAX_ITEMS)
    change_detection_complete: bool | None = None
    no_changes: bool | None = None

    @field_validator("files")
    @classmethod
    def limit_file_path_lengths(cls, values: list[str]) -> list[str]:
        if any(len(value) > EVENT_FILE_PATH_MAX_CHARS for value in values):
            raise ValueError(f"file paths must be at most {EVENT_FILE_PATH_MAX_CHARS} characters")
        return values

    @field_validator("changes")
    @classmethod
    def limit_change_content(cls, values: list[dict]) -> list[dict]:
        for change in values:
            for key in ("path", "old_path", "before_path"):
                path = change.get(key)
                if isinstance(path, str) and len(path) > EVENT_FILE_PATH_MAX_CHARS:
                    raise ValueError(
                        f"change paths must be at most {EVENT_FILE_PATH_MAX_CHARS} characters"
                    )
            patch = change.get("patch")
            if isinstance(patch, str) and len(patch) > EVENT_CHANGE_PATCH_MAX_CHARS:
                raise ValueError(
                    f"change patches must be at most {EVENT_CHANGE_PATCH_MAX_CHARS} characters"
                )
        return values


class CommitCreatedPayload(PayloadModel):
    hash: str | None = None
    message: str | None = None
    branch: str | None = None
    git_remote: str | None = None
    github_url: str | None = None
    cwd: str | None = None
    session_id: str | None = None


class SessionEndedPayload(PayloadModel):
    reason: str | None = None
    duration: int | None = None
    session_id: str | None = None


EventPayload = Union[
    SessionStartedPayload,
    PromptSubmittedPayload,
    ResponseReceivedPayload,
    FilesChangedPayload,
    CommitCreatedPayload,
    SessionEndedPayload,
]
LEGACY_USAGE_KEYS = frozenset(("tokens", "total_tokens"))
PAYLOAD_BY_EVENT_TYPE: dict[str, type[PayloadModel]] = {
    "SessionStarted": SessionStartedPayload,
    "PromptSubmitted": PromptSubmittedPayload,
    "ResponseReceived": ResponseReceivedPayload,
    "FilesChanged": FilesChangedPayload,
    "CommitCreated": CommitCreatedPayload,
    "SessionEnded": SessionEndedPayload,
}


class EventCreate(BaseModel):
    id: UUID
    schema_version: int = Field(default=1, ge=1)
    project_id: UUID
    session_id: UUID
    sequence: int = Field(gt=0)
    tool: SupportedTool
    event_type: EventType
    timestamp: datetime
    payload: EventPayload

    @model_validator(mode="before")
    @classmethod
    def coerce_typed_payload(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        event_type = values.get("event_type")
        payload_model = PAYLOAD_BY_EVENT_TYPE.get(event_type)
        if payload_model is not None and isinstance(values.get("payload"), dict):
            payload = {
                key: value
                for key, value in values["payload"].items()
                if key not in LEGACY_USAGE_KEYS
            }
            values["payload"] = payload_model(**payload)
        return values

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    model_config = ConfigDict(extra="forbid")


class EventRead(EventCreate):
    pass


class EventBatchCreate(BaseModel):
    events: list[EventCreate] = Field(..., min_length=1, max_length=EVENT_BATCH_MAX_ITEMS)

    @model_validator(mode="after")
    def limit_resource_entries(self) -> EventBatchCreate:
        resource_entries = 0
        for event in self.events:
            if event.event_type != "FilesChanged" or not isinstance(
                event.payload,
                FilesChangedPayload,
            ):
                continue
            resource_entries += len(event.payload.changes or event.payload.files)
            if resource_entries > EVENT_RESOURCE_ENTRIES_MAX_PER_BATCH:
                raise ValueError(
                    "event batch contains more than "
                    f"{EVENT_RESOURCE_ENTRIES_MAX_PER_BATCH} file changes"
                )
        return self


class EventBatchResponse(BaseModel):
    accepted: int
    event_ids: list[str]
