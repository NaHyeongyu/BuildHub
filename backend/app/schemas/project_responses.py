from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileTreeNodeResponse(StrictResponse):
    name: str
    path: str
    type: Literal["file", "folder"]
    children: list[FileTreeNodeResponse] | None = None


class ProjectActivitySummaryResponse(StrictResponse):
    events: int
    files_changed: int
    id: str
    last_activity_at: str | None
    model: str
    prompts: int
    responses: int
    started_at: str | None


class ProjectMetricHistoryResponse(StrictResponse):
    date: str
    files_changed: int
    memories: int
    prompts: int
    sessions: int


class ProjectDetailMetricsResponse(StrictResponse):
    activity_history: list[ProjectMetricHistoryResponse]
    connected_models: list[str]
    connected_tools: list[str]
    files_changed_since_yesterday: int
    latest_activity_at: str | None
    last_modified_at: str | None
    memory_artifacts_since_yesterday: int
    prompts_since_yesterday: int
    repository_connected: bool
    sessions_since_yesterday: int
    tracked_files: int
    total_events: int
    total_prompts: int
    total_sessions: int


class ProjectDetailProjectResponse(StrictResponse):
    created_at: str | None
    default_branch: str
    description: str | None
    id: str
    is_bookmarked: bool
    memory_grouping_mode: Literal["session", "chronological"]
    name: str
    project_url: str | None
    repository_status: str
    repository_url: str | None
    slug: str
    tags: list[str]
    updated_at: str | None
    visibility: Literal["private", "public"]


class MemoryArtifactSummaryResponse(StrictResponse):
    artifact_stage: str | None
    changed_file_count: int
    changed_files: list[dict[str, Any]] = Field(default_factory=list)
    commit_sha: str | None
    created_at: str | None
    draft_confidence: float | None
    draft_generator: str | None
    draft_type: str | None
    end_sequence: int | None
    fallback_reason: str | None
    first_event_at: str | None
    generator: str | None
    id: str
    last_event_at: str | None
    memory_batch_id: str | None
    memory_batch_ids: list[str]
    memory_scope: str | None
    model: str | None
    needs_user_verification: bool | None
    outcome: str | None
    prompt_count: int | None
    reason: str | None
    requested_generator: str | None
    review_state: str | None
    sections: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str | None
    slice_index: int | None
    source_draft_ids: list[str]
    source_session_ids: list[str]
    start_sequence: int | None
    summary: str | None
    summary_level: int | None
    suggested_user_action: str | None
    tags: list[str]
    technologies: list[str]
    title: str
    trigger_reason: str | None
    type: str
    updated_at: str | None
    why_it_matters: str | None
    window_reason: str | None


class ProjectDetailMemoryResponse(StrictResponse):
    latest_artifact_at: str | None
    recent_artifacts: list[MemoryArtifactSummaryResponse]
    total_artifacts: int


class ProjectDetailResponse(StrictResponse):
    activities: list[ProjectActivitySummaryResponse]
    files: list[FileTreeNodeResponse]
    memory: ProjectDetailMemoryResponse
    metrics: ProjectDetailMetricsResponse
    project: ProjectDetailProjectResponse
    prompt_activities: list[dict[str, Any]]


class PublicProjectOwnerResponse(StrictResponse):
    avatar_url: str | None
    id: str
    username: str


class PublicProjectSummaryResponse(StrictResponse):
    connected_models: list[str]
    created_at: str
    default_branch: str
    description: str | None
    events: int
    github_url: str | None
    id: str
    is_owner: bool
    is_saved: bool
    latest_event_at: str | None
    latest_memory_at: str | None
    memory_count: int
    name: str
    owner: PublicProjectOwnerResponse
    project_url: str | None
    prompts: int
    sessions: int
    slug: str
    tags: list[str]
    tracked_files: int
    updated_at: str
    view_count: int
    weekly_popularity_score: float
    weekly_saves: int
    weekly_unique_viewers: int
    weekly_views: int
    visibility: Literal["public"]


class PublicProjectListResponse(StrictResponse):
    items: list[PublicProjectSummaryResponse]
    limit: int
    offset: int
    total: int


class PublicProfileResponse(PublicProjectListResponse):
    profile: PublicProjectOwnerResponse


class PublicProjectViewHistoryResponse(StrictResponse):
    date: str
    views: int


class PublicMemoryArtifactResponse(StrictResponse):
    artifact_stage: str | None
    changed_file_count: int
    created_at: str | None
    first_event_at: str | None
    generator: str | None
    id: str
    last_event_at: str | None
    memory_scope: str | None
    model: str | None
    outcome: str | None
    prompt_count: int | None
    reason: str | None
    review_state: Literal["edited", "verified"]
    sections: list[dict[str, Any]] = Field(default_factory=list)
    summary: str | None
    tags: list[str]
    technologies: list[str]
    title: str
    type: str
    updated_at: str | None
    why_it_matters: str | None


class PublicProjectMemoryResponse(StrictResponse):
    latest_artifact_at: str | None
    recent_artifacts: list[PublicMemoryArtifactResponse]
    total_artifacts: int


class PublicProjectDetailResponse(StrictResponse):
    activities: list[ProjectActivitySummaryResponse]
    files: list[FileTreeNodeResponse]
    is_owner: bool
    is_saved: bool
    memory: PublicProjectMemoryResponse
    metrics: ProjectDetailMetricsResponse
    owner: PublicProjectOwnerResponse
    project: ProjectDetailProjectResponse
    prompt_activities: list[dict[str, Any]]
    unique_viewers: int
    view_count: int
    view_history: list[PublicProjectViewHistoryResponse]
    views_7d: int


class PublicProjectViewResponse(StrictResponse):
    project_id: str
    recorded: bool
    unique_viewers: int
    view_count: int
    view_history: list[PublicProjectViewHistoryResponse]
    views_7d: int


class PublicProjectSaveResponse(StrictResponse):
    is_saved: bool
    project_id: str


class PromptFileChangeResponse(StrictResponse):
    additions: int | None
    binary: bool = False
    deletions: int | None
    event_id: str | None = None
    old_path: str | None = None
    patch: str | None = None
    patch_omitted_reason: str | None = None
    patch_truncated: bool = False
    path: str
    status: str


class ProjectPromptActivityResponse(StrictResponse):
    continuation_of: str | None = None
    delivery_mode: Literal["unknown", "queued", "interrupt"] | None = None
    file_changes: list[PromptFileChangeResponse]
    files_changed: int
    id: str
    model: str
    prompt: str
    prompt_original_length: int | None = None
    prompt_storage_limit: int | None = None
    prompt_truncated: bool = False
    response: str | None = None
    response_original_length: int | None = None
    response_received_at: str | None = None
    response_source: str | None = None
    response_storage_limit: int | None = None
    response_truncated: bool = False
    root_prompt_event_id: str | None = None
    sequence: int
    session_id: str | None
    submission_context: Literal["idle", "during_output"] | None = None
    submitted_at: str | None


class ProjectPromptActivitiesResponse(StrictResponse):
    cursor: str | None
    has_more: bool
    items: list[ProjectPromptActivityResponse]
    limit: int
    next_cursor: str | None
    query: str | None
    scanned: int
    session_id: str | None
    total: int | None


class ProjectFilesResponse(StrictResponse):
    files: list[FileTreeNodeResponse]
    limit: int
    total: int
    truncated: bool


class GithubRepositoryOptionResponse(StrictResponse):
    default_branch: str
    description: str | None
    full_name: str
    html_url: str
    id: int | str | None
    name: str
    owner: str
    private: bool
    updated_at: str | None


class GithubRepositoriesResponse(StrictResponse):
    available: bool
    message: str | None
    repositories: list[GithubRepositoryOptionResponse]
    status: Literal["github_repository_access_required", "ok"]


class ProjectGithubFilesResponse(StrictResponse):
    available: bool
    default_branch: str | None = None
    files: list[FileTreeNodeResponse]
    message: str | None
    repository: str | None
    status: Literal[
        "github_repository_access_required",
        "ok",
        "repository_not_connected",
    ]
    truncated: bool | None = None


class ProjectGithubFileContentResponse(StrictResponse):
    available: bool
    branch: str | None = None
    content: str | None
    html_url: str | None = None
    message: str | None
    name: str | None = None
    path: str | None = None
    repository: str | None = None
    size: int | None = None
    status: Literal[
        "github_repository_access_required",
        "ok",
        "repository_not_connected",
    ]
