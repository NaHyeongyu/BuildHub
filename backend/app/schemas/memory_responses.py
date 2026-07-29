from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.project_responses import (
    MemoryArtifactSummaryResponse,
    ProjectDetailMemoryResponse,
    ProjectDetailResponse,
)
from app.schemas.projects import ProjectSummaryResponse


class StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PendingMemoryRangeResponse(StrictResponse):
    can_checkpoint: bool
    changed_file_count: int
    draft_id: str
    end_sequence: int | None
    event_count: int
    file_change_event_count: int
    first_event_at: str | None
    last_event_at: str | None
    prompt_count: int
    response_count: int
    session_id: str
    start_sequence: int | None
    tool: str


class MemoryReviewQueueErrorResponse(StrictResponse):
    message: str
    project_id: str


class MemoryReviewQueueProjectResponse(StrictResponse):
    pending_count: int
    project_id: str
    ranges: list[PendingMemoryRangeResponse]


class MemoryReviewQueueResponse(StrictResponse):
    errors: list[MemoryReviewQueueErrorResponse]
    project_summaries: list[ProjectSummaryResponse]
    projects: list[MemoryReviewQueueProjectResponse]
    total_pending_count: int


class MemoryGeneratorStagesResponse(StrictResponse):
    draft: str
    pending_draft: str
    project: str


class MemoryGeneratorTimeoutsResponse(StrictResponse):
    gemini: float
    openai: float


class MemoryGeneratorStatusResponse(StrictResponse):
    fallback_generator: str
    gemini_configured: bool
    gemini_max_retries: int
    gemini_model: str
    openai_configured: bool
    openai_max_retries: int
    openai_model: str
    generators: MemoryGeneratorStagesResponse
    requested_generators: MemoryGeneratorStagesResponse
    timeout_seconds: MemoryGeneratorTimeoutsResponse


class SessionCompletionDetailResponse(StrictResponse):
    completed: bool
    completed_at: str | None
    reason: str


class SessionCompletionResponse(StrictResponse):
    artifact: None = None
    completion: SessionCompletionDetailResponse
    message: str | None = None
    pending_range: PendingMemoryRangeResponse | None
    status: Literal["pending_memory", "session_open"]


class MemoryBatchErrorResponse(StrictResponse):
    code: str
    message: str
    retryable: bool


class MemoryBatchResponse(StrictResponse):
    batch_id: str
    batch_status: str
    completed_at: str | None
    error: MemoryBatchErrorResponse | None
    generated_artifact_ids: list[str]
    message: str
    project_memory_artifact_id: str | None
    remaining_pending_count: int
    replayed: bool
    retryable: bool
    snapshot_at: str
    source_draft_count: int
    source_session_ids: list[str]
    status: str | None


class ProjectWorkspaceMemoryResponse(ProjectDetailMemoryResponse):
    drafts: list[MemoryArtifactSummaryResponse]
    latest_batch: MemoryBatchResponse | None
    pending_ranges: list[PendingMemoryRangeResponse]


class ProjectWorkspaceResponse(ProjectDetailResponse):
    memory: ProjectWorkspaceMemoryResponse


class MemoryProviderEstimateResponse(StrictResponse):
    calls: int
    configured: bool
    estimated_cost_microusd: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    model: str
    provider: str
    requested_calls: int
    stage: str


class MemoryGenerationPreviewResponse(StrictResponse):
    can_generate: bool
    currency: Literal["USD"]
    draft_count: int
    estimated_cost_microusd: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_provider_calls: int
    event_count: int
    file_change_event_count: int
    one_time_generation: Literal[True]
    overflow_draft_count: int
    prompt_count: int
    providers: list[MemoryProviderEstimateResponse]
    ranges: list[PendingMemoryRangeResponse]
    retryable: Literal[False]
    session_count: int


class MemoryReviewPromptResponse(StrictResponse):
    created_at: str
    event_id: str
    prompt_truncated: bool
    response_preview: str | None
    response_truncated: bool
    sequence: int
    session_id: str
    text: str
    tool: str


class MemoryGenerationReviewResponse(StrictResponse):
    changed_file_count: int
    commit_count: int
    draft_count: int
    prompt_count: int
    prompts: list[MemoryReviewPromptResponse]
    providers: list[Literal["gemini", "openai"]]
    response_count: int
    review_token: str
    source_code_included: Literal[False]


class MemoryArtifactResponse(MemoryArtifactSummaryResponse):
    prompt_event_ids: list[str]


class ProjectMemorySnapshotResponse(StrictResponse):
    artifact: MemoryArtifactResponse | None
    snapshot: dict[str, Any] | None
