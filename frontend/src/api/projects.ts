import type {
  ProjectDetailApiResponse,
  ProjectFilesApiResponse,
  ProjectGithubFileContentApiResponse,
  ProjectGithubFilesApiResponse,
  ProjectMemoryArtifactApiResponse,
  ProjectMemoryPendingRangeApiResponse,
  MemoryReviewQueueSnapshotApiResponse,
  ProjectPromptActivitiesApiResponse,
  ProjectSummary,
  PublicProjectDetailResponse,
  PublicProjectPage,
  PublicProjectViewAnalytics,
  PublicProfileResponse,
} from "../workspace/types";
import {
  isCommunityPreview,
  previewPublicProjectDetail,
  previewPublicProfile,
  previewPublicProjects,
  previewRecordPublicProjectView,
  previewUpdatePublicProjectSave,
} from "../workspace/communityPreviewData";
import {
  requestConditionalJson,
  requestJson,
  requestJsonBody,
  requestVoid,
} from "./client";

export type ProjectDetailResourcesResponse = ProjectDetailApiResponse & {
  memory: NonNullable<ProjectDetailApiResponse["memory"]> & {
    drafts: [];
    latest_batch: ProjectMemoryGenerationResponse | null;
    pending_ranges: ProjectMemoryPendingRangeApiResponse[];
  };
};

export type ProjectMemoryGenerationStatus =
  | "generation_delayed"
  | "generation_in_progress"
  | "memory_generated"
  | "no_memory"
  | "no_pending"
  | "generation_failed";

export type ProjectMemoryGenerationResponse = {
  batch_id: string;
  error?: {
    code: string;
    message: string;
    retryable: boolean;
  } | null;
  message: string;
  retryable?: boolean;
  status: ProjectMemoryGenerationStatus;
};

export type MemoryGenerationReviewPrompt = {
  created_at: string;
  event_id: string;
  prompt_truncated: boolean;
  response_preview: string | null;
  response_truncated: boolean;
  sequence: number;
  session_id: string;
  text: string;
  tool: string;
};

export type MemoryGenerationReviewResponse = {
  changed_file_count: number;
  commit_count: number;
  draft_count: number;
  prompt_count: number;
  prompts: MemoryGenerationReviewPrompt[];
  providers: Array<"gemini" | "openai">;
  response_count: number;
  review_token: string;
  source_code_included: false;
};

export type ProjectCreatePayload = {
  default_branch?: string | null;
  description?: string | null;
  github_url: string;
  name?: string | null;
};

const PROJECT_MEMORY_GENERATION_TIMEOUT_MS = 10 * 60 * 1_000;
const PROJECT_MEMORY_POLL_BASE_INTERVAL_MS = 2_000;
const PROJECT_MEMORY_POLL_MAX_INTERVAL_MS = 10_000;

export function projectMemoryPollDelayMs(attempt: number) {
  const boundedAttempt = Number.isFinite(attempt)
    ? Math.max(0, Math.min(8, Math.trunc(attempt)))
    : 0;
  return Math.min(
    PROJECT_MEMORY_POLL_MAX_INTERVAL_MS,
    PROJECT_MEMORY_POLL_BASE_INTERVAL_MS * 2 ** boundedAttempt,
  );
}

function projectMemoryIdempotencyStorageKey(projectId: string) {
  return `promty:project-memory-batch:${projectId}`;
}

function projectMemoryIdempotencyKey(projectId: string) {
  const storageKey = projectMemoryIdempotencyStorageKey(projectId);
  const existing = window.sessionStorage.getItem(storageKey);
  if (existing) {
    return existing;
  }
  const idempotencyKey = crypto.randomUUID();
  window.sessionStorage.setItem(storageKey, idempotencyKey);
  return idempotencyKey;
}

function clearProjectMemoryIdempotencyKey(projectId: string) {
  window.sessionStorage.removeItem(projectMemoryIdempotencyStorageKey(projectId));
}

function delayedProjectMemoryResponse(
  response?: ProjectMemoryGenerationResponse,
): ProjectMemoryGenerationResponse {
  return {
    ...(response ?? { batch_id: "" }),
    message: "This update is taking longer than expected. Check its status to continue.",
    retryable: true,
    status: "generation_delayed",
  };
}

function waitForProjectMemoryPoll(delayMs: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason);
      return;
    }

    const timeoutId = window.setTimeout(() => {
      signal.removeEventListener("abort", handleAbort);
      resolve();
    }, delayMs);
    const handleAbort = () => {
      window.clearTimeout(timeoutId);
      reject(signal.reason);
    };
    signal.addEventListener("abort", handleAbort, { once: true });
  });
}

export function fetchProjectSummaries(signal?: AbortSignal): Promise<ProjectSummary[]> {
  return requestJson<ProjectSummary[]>("/api/projects", { signal }, {
    errorMessage: "Projects request failed",
    unauthorizedMessage: "Sign in again before loading projects.",
  });
}

export function fetchPublicProjects(
  options: {
    limit?: number;
    offset?: number;
    query?: string;
    savedOnly?: boolean;
    signal?: AbortSignal;
    sort?: "newest" | "popular" | "recent";
  } = {},
): Promise<PublicProjectPage> {
  if (isCommunityPreview()) {
    return Promise.resolve(previewPublicProjects({
      limit: options.limit ?? 24,
      offset: options.offset ?? 0,
      query: options.query,
      savedOnly: options.savedOnly ?? false,
      sort: options.sort ?? "popular",
    }));
  }
  const params = new URLSearchParams({
    limit: String(options.limit ?? 24),
    offset: String(options.offset ?? 0),
    sort: options.sort ?? "popular",
  });
  if (options.query?.trim()) params.set("query", options.query.trim());
  if (options.savedOnly) params.set("saved_only", "true");
  return requestJson<PublicProjectPage>(
    `/api/projects/public?${params.toString()}`,
    { signal: options.signal },
    {
      errorMessage: "Public projects request failed",
      unauthorizedMessage: "Sign in again before exploring public projects.",
    },
  );
}

export function fetchPublicProjectDetail(
  projectId: string,
  signal?: AbortSignal,
): Promise<PublicProjectDetailResponse> {
  if (isCommunityPreview()) {
    const project = previewPublicProjectDetail(projectId);
    return project
      ? Promise.resolve(project)
      : Promise.reject(new Error("Preview project not found"));
  }
  return requestJson<PublicProjectDetailResponse>(
    `/api/projects/public/${encodeURIComponent(projectId)}`,
    { signal },
    {
      errorMessage: "Public project request failed",
      unauthorizedMessage: "Sign in again before opening this public project.",
    },
  );
}

export function recordPublicProjectView(
  projectId: string,
): Promise<PublicProjectViewAnalytics> {
  if (isCommunityPreview()) {
    const response = previewRecordPublicProjectView(projectId);
    return response
      ? Promise.resolve(response)
      : Promise.reject(new Error("Preview project not found"));
  }
  return requestJsonBody<PublicProjectViewAnalytics>(
    `/api/projects/public/${encodeURIComponent(projectId)}/view`,
    "POST",
    {},
    {
      errorMessage: "Public project view could not be recorded",
      unauthorizedMessage: "Sign in again before opening this public project.",
    },
  );
}

export function updatePublicProjectSave(
  projectId: string,
  isSaved: boolean,
): Promise<{ is_saved: boolean; project_id: string }> {
  if (isCommunityPreview()) {
    const response = previewUpdatePublicProjectSave(projectId, isSaved);
    return response
      ? Promise.resolve(response)
      : Promise.reject(new Error("Preview project not found"));
  }
  return requestJsonBody<{ is_saved: boolean; project_id: string }>(
    `/api/projects/public/${encodeURIComponent(projectId)}/save`,
    "PATCH",
    { is_saved: isSaved },
    {
      errorMessage: "Public project save update failed",
      unauthorizedMessage: "Sign in again before saving public projects.",
    },
  );
}

export function fetchPublicProfile(
  userId: string,
  options: {
    limit?: number;
    offset?: number;
    signal?: AbortSignal;
  } = {},
): Promise<PublicProfileResponse> {
  if (isCommunityPreview()) {
    const profile = previewPublicProfile(userId, {
      limit: options.limit ?? 24,
      offset: options.offset ?? 0,
    });
    return profile
      ? Promise.resolve(profile)
      : Promise.reject(new Error("Preview profile not found"));
  }
  const params = new URLSearchParams({
    limit: String(options.limit ?? 24),
    offset: String(options.offset ?? 0),
  });
  return requestJson<PublicProfileResponse>(
    `/api/projects/public/profiles/${encodeURIComponent(userId)}?${params.toString()}`,
    { signal: options.signal },
    {
      errorMessage: "Public profile request failed",
      unauthorizedMessage: "Sign in again before opening this public profile.",
    },
  );
}

export function createProject(
  payload: ProjectCreatePayload,
): Promise<ProjectSummary> {
  return requestJsonBody<ProjectSummary>(
    "/api/projects",
    "POST",
    {
      default_branch: payload.default_branch ?? undefined,
      description: payload.description ?? undefined,
      github_url: payload.github_url,
      name: payload.name ?? undefined,
    },
    {
      errorMessage: "Project creation failed",
      unauthorizedMessage: "Sign in again before creating a project.",
    },
  );
}

export function deleteProject(projectId: string): Promise<void> {
  return requestVoid(
    `/api/projects/${encodeURIComponent(projectId)}`,
    { method: "DELETE" },
    {
      errorMessage: "Project deletion failed",
      unauthorizedMessage: "Sign in again before deleting this project.",
    },
  );
}

export function deleteProjectPromptActivity(
  projectId: string,
  promptEventId: string,
): Promise<void> {
  return requestVoid(
    `/api/projects/${encodeURIComponent(projectId)}/prompt-activities/${encodeURIComponent(promptEventId)}`,
    { method: "DELETE" },
    {
      errorMessage: "Prompt activity deletion failed",
      unauthorizedMessage: "Sign in again before deleting prompt activity.",
    },
  );
}

export function deleteProjectSessionActivity(
  projectId: string,
  sessionId: string,
): Promise<void> {
  return requestVoid(
    `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
    {
      errorMessage: "Session activity deletion failed",
      unauthorizedMessage: "Sign in again before deleting session activity.",
    },
  );
}

export function updateProjectBookmark(
  projectId: string,
  isBookmarked: boolean,
): Promise<ProjectSummary> {
  return requestJsonBody<ProjectSummary>(
    `/api/projects/${encodeURIComponent(projectId)}/bookmark`,
    "PATCH",
    { is_bookmarked: isBookmarked },
    {
      errorMessage: "Project bookmark update failed",
      unauthorizedMessage: "Sign in again before saving projects.",
    },
  );
}

export async function fetchProjectDetailResources(
  projectId: string,
  signal?: AbortSignal,
): Promise<ProjectDetailResourcesResponse> {
  return requestJson<ProjectDetailResourcesResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/workspace?pending_limit=100`,
    { signal },
    {
      errorMessage: "Project detail request failed",
    },
  );
}

export function fetchLatestProjectMemoryBatch(
  projectId: string,
  signal?: AbortSignal,
): Promise<ProjectMemoryGenerationResponse | null> {
  return requestJson<ProjectMemoryGenerationResponse | null>(
    `/api/projects/${encodeURIComponent(projectId)}/memory/batches/latest`,
    { signal },
    {
      errorMessage: "Project memory status request failed",
    },
  );
}

export function fetchProjectMemoryPendingRanges(
  projectId: string,
  signal?: AbortSignal,
  limit = 100,
): Promise<ProjectMemoryPendingRangeApiResponse[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  return requestJson<ProjectMemoryPendingRangeApiResponse[]>(
    `/api/projects/${encodeURIComponent(projectId)}/memory/pending?${params}`,
    { signal },
    {
      errorMessage: "Memory review queue request failed",
    },
  );
}

export function refreshMemoryReviewQueue(
  signal?: AbortSignal,
): Promise<MemoryReviewQueueSnapshotApiResponse> {
  return requestJson<MemoryReviewQueueSnapshotApiResponse>(
    "/api/projects/memory/review-queue/refresh?limit=100",
    { method: "POST", signal },
    {
      errorMessage: "Review queue refresh failed",
      unauthorizedMessage: "Sign in again before refreshing the review queue.",
    },
  );
}

export async function generateProjectMemory(
  projectId: string,
  reviewToken: string,
  excludedPromptEventIds: string[],
): Promise<ProjectMemoryGenerationResponse> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(
    () => controller.abort(),
    PROJECT_MEMORY_GENERATION_TIMEOUT_MS,
  );
  let response: ProjectMemoryGenerationResponse | undefined;
  let pollAttempt = 0;

  try {
    response = await requestJson<ProjectMemoryGenerationResponse>(
      `/api/projects/${encodeURIComponent(projectId)}/memory/generate`,
      {
        body: JSON.stringify({
          excluded_prompt_event_ids: excludedPromptEventIds,
          idempotency_key: projectMemoryIdempotencyKey(projectId),
          review_token: reviewToken,
        }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
        signal: controller.signal,
      },
      {
        errorMessage: "Project memory generation failed",
        unauthorizedMessage: "Sign in again before creating project memory.",
      },
    );
    while (response.status === "generation_in_progress") {
      await waitForProjectMemoryPoll(
        projectMemoryPollDelayMs(pollAttempt),
        controller.signal,
      );
      pollAttempt += 1;
      response = await requestJson<ProjectMemoryGenerationResponse>(
        `/api/projects/${encodeURIComponent(projectId)}/memory/batches/${encodeURIComponent(
          response.batch_id,
        )}`,
        { signal: controller.signal },
        {
          errorMessage: "Project memory status request failed",
          unauthorizedMessage: "Sign in again before checking project memory.",
        },
      );
    }
  } catch (error) {
    if (controller.signal.aborted) {
      return delayedProjectMemoryResponse(response);
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }

  if (!response) {
    return delayedProjectMemoryResponse();
  }
  if (
    ["memory_generated", "no_memory", "no_pending"].includes(response.status) ||
    response.status === "generation_failed"
  ) {
    clearProjectMemoryIdempotencyKey(projectId);
  }
  return response;
}

export function fetchMemoryGenerationReview(
  projectId: string,
): Promise<MemoryGenerationReviewResponse> {
  return requestJson<MemoryGenerationReviewResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/memory/generation-review`,
    {},
    { errorMessage: "Prompt review request failed" },
  );
}

export function approveProjectMemory(projectId: string): Promise<unknown> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/memory/project/approve`,
    { method: "POST" },
    {
      errorMessage: "Project memory approval failed",
      unauthorizedMessage: "Sign in again before approving project memory.",
    },
  );
}

export function fetchProjectMemoryArtifacts(
  projectId: string,
  limit: number,
  signal?: AbortSignal,
): Promise<ProjectMemoryArtifactApiResponse[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  return requestJson<ProjectMemoryArtifactApiResponse[]>(
    `/api/projects/${encodeURIComponent(projectId)}/artifacts?${params}`,
    { signal },
    {
      errorMessage: "Memory history request failed",
    },
  );
}

export async function fetchProjectGithubFiles(
  projectId: string,
  signal?: AbortSignal,
  etag: string | null = null,
  treeKey: string | null = null,
) {
  const result = await requestConditionalJson<ProjectGithubFilesApiResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/github/files`,
    etag,
    {
      headers: treeKey
        ? { "X-Promty-Repository-Tree-Key": treeKey }
        : undefined,
      signal,
    },
    {
      errorMessage: "GitHub files request failed",
    },
  );
  return {
    etag: result.etag,
    notModified: result.notModified,
    payload: result.payload,
    treeKey: result.headers.get("X-Promty-Repository-Tree-Key"),
  };
}

export function fetchProjectFiles(
  projectId: string,
  signal?: AbortSignal,
): Promise<ProjectFilesApiResponse> {
  return requestJson<ProjectFilesApiResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/files`,
    { signal },
    {
      errorMessage: "Tracked files request failed",
    },
  );
}

export function fetchProjectPromptActivities({
  cursor,
  limit,
  projectId,
  query,
  sessionId,
  signal,
}: {
  cursor?: string | null;
  limit: number;
  projectId: string;
  query?: string;
  sessionId?: string | null;
  signal?: AbortSignal;
}): Promise<ProjectPromptActivitiesApiResponse> {
  const params = new URLSearchParams({
    limit: String(limit),
  });
  if (cursor) {
    params.set("cursor", cursor);
  }
  if (query?.trim()) {
    params.set("q", query.trim());
  }
  if (sessionId) {
    params.set("session_id", sessionId);
  }

  return requestJson<ProjectPromptActivitiesApiResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/prompt-activities?${params}`,
    { signal },
    {
      errorMessage: "Prompt activity request failed",
    },
  );
}

export function fetchRepositoryFileContent(
  projectId: string,
  path: string,
  signal?: AbortSignal,
): Promise<ProjectGithubFileContentApiResponse> {
  return requestJson<ProjectGithubFileContentApiResponse>(
    `/api/projects/${encodeURIComponent(
      projectId,
    )}/github/files/content?${new URLSearchParams({ path })}`,
    { signal },
    {
      errorMessage: "GitHub file request failed",
    },
  );
}

export function updateRepositoryConnection(
  projectId: string,
  githubUrl: string,
): Promise<ProjectSummary> {
  return requestJsonBody<ProjectSummary>(
    `/api/projects/${encodeURIComponent(projectId)}/repository`,
    "PATCH",
    { github_url: githubUrl },
    {
      errorMessage: "Repository connection failed",
      unauthorizedMessage: "Sign in again before connecting a repository.",
    },
  );
}

export function updateProjectDescription(
  projectId: string,
  description: string,
): Promise<void> {
  return requestVoid(
    `/api/projects/${encodeURIComponent(projectId)}/description`,
    {
      body: JSON.stringify({ description }),
      headers: { "Content-Type": "application/json" },
      method: "PATCH",
    },
    {
      errorMessage: "Description update failed",
      unauthorizedMessage: "Sign in again before editing the project description.",
    },
  );
}

export type ProjectMetadataPatch = {
  memoryGroupingMode?: "session" | "chronological";
  name?: string;
  projectUrl?: string;
  slug?: string;
  tags?: string[];
  visibility?: "private" | "public";
};

export function updateProjectMetadata(
  projectId: string,
  metadata: ProjectMetadataPatch,
): Promise<ProjectSummary> {
  return requestJsonBody<ProjectSummary>(
    `/api/projects/${encodeURIComponent(projectId)}/metadata`,
    "PATCH",
    {
      ...(metadata.memoryGroupingMode !== undefined
        ? { memory_grouping_mode: metadata.memoryGroupingMode }
        : {}),
      ...(metadata.name !== undefined ? { name: metadata.name } : {}),
      ...(metadata.projectUrl !== undefined ? { project_url: metadata.projectUrl } : {}),
      ...(metadata.slug !== undefined ? { slug: metadata.slug } : {}),
      ...(metadata.tags !== undefined ? { tags: metadata.tags } : {}),
      ...(metadata.visibility !== undefined ? { visibility: metadata.visibility } : {}),
    },
    {
      errorMessage: "Project metadata update failed",
      unauthorizedMessage: "Sign in again before editing project metadata.",
    },
  );
}
