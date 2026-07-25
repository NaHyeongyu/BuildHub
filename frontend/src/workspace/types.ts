import type {
  FileTreeNode,
  PublishedFlowAsset,
  PublishedFlowDetail,
} from "../components/project-detail";

export type SidebarItemId =
  | "projects"
  | "pinned"
  | "community"
  | "admin"
  | "support"
  | "settings"
  | "profile";

export type CommunityContentType = "projects" | "flows";
export type MemoryGroupingMode = "session" | "chronological";

export type Project = {
  defaultBranch?: string;
  id: string;
  name: string;
  createdTimestamp: string;
  slug?: string;
  tags: string[];
  visibility: "private" | "public";
  latestTimestamp: string;
  latestUpdatedAt: string;
  latestActivityLabel: string;
  sessions: number;
  events: number;
  filesChanged: number;
  prompts: number;
  trackedFiles: number;
  models: string[];
  githubUrl?: string;
  isBookmarked: boolean;
  latestMemoryAt?: string;
  memoryCount: number;
  memoryGroupingMode: MemoryGroupingMode;
  pendingMemoryCount: number;
  projectUrl?: string;
};

export type AuthUser = {
  id: string;
  username: string;
  email: string | null;
  avatar_url: string | null;
  github_repository_access: boolean;
  is_admin: boolean;
  preferred_locale: "en" | "ja" | "ko" | "zh";
};

export type AccountGithubConnection = {
  connected: boolean;
  created_at: string | null;
  revoked_at: string | null;
  scopes: string[];
  status: "connected" | "not_connected";
  token_type: string | null;
  updated_at: string | null;
};

export type AccountCollectorToken = {
  collector_version: string | null;
  created_at: string | null;
  id: string;
  last_used_at: string | null;
  name: string;
  revoked_at: string | null;
  status: "active" | "revoked";
};

export type AccountOverview = {
  collector_tokens: AccountCollectorToken[];
  github_connection: AccountGithubConnection;
  latest_collector_version: string;
  policy_consents: AccountPolicyConsents;
  user: AuthUser;
};

export type AccountPolicyConsents = {
  current_policy_version: string;
  eligibility_confirmed: boolean;
  external_ai_allowed: boolean;
  external_ai_consented_at: string | null;
  external_ai_providers: Array<"gemini" | "openai">;
  policy_accepted: boolean;
  policy_accepted_at: string | null;
};

export type AccountCollectorTokenCreateResponse = {
  collector_token: AccountCollectorToken;
  token: string;
};

export type AccountDeletionResponse = {
  counts: {
    collector_tokens: number;
    projects: number;
    published_flows: number;
  };
  status: "deleted";
};

export type SupportedTool = "claude-code" | "codex-cli" | "cursor" | "gemini-cli";

export type EventType =
  | "SessionStarted"
  | "PromptSubmitted"
  | "ResponseReceived"
  | "FilesChanged"
  | "CommitCreated"
  | "SessionEnded";

export type EventRecord = {
  schema_version: number;
  id: string;
  project_id: string;
  session_id: string;
  sequence: number;
  tool: SupportedTool;
  event_type: EventType;
  timestamp: string;
  payload: Record<string, unknown>;
};

export type ProjectSummary = {
  id: string;
  slug: string;
  name: string;
  git_remote: string | null;
  github_url: string | null;
  is_bookmarked: boolean;
  default_branch: string;
  created_at: string;
  connected_models: string[];
  tags: string[];
  sessions: number;
  events: number;
  prompts: number;
  tracked_files: number;
  latest_event_at: string | null;
  latest_memory_at: string | null;
  memory_count: number;
  memory_grouping_mode: MemoryGroupingMode;
  pending_memory_count: number;
  project_url: string | null;
  updated_at: string;
  visibility: "private" | "public";
};

export type PublicProjectOwner = {
  avatar_url: string | null;
  id: string;
  username: string;
};

export type PublicProjectSummary = {
  connected_models: string[];
  created_at: string;
  default_branch: string;
  description: string | null;
  events: number;
  github_url: string | null;
  id: string;
  is_owner: boolean;
  is_saved: boolean;
  latest_event_at: string | null;
  latest_memory_at: string | null;
  memory_count: number;
  name: string;
  owner: PublicProjectOwner;
  project_url: string | null;
  prompts: number;
  sessions: number;
  slug: string;
  tags: string[];
  tracked_files: number;
  updated_at: string;
  view_count?: number;
  weekly_popularity_score?: number;
  weekly_saves?: number;
  weekly_unique_viewers?: number;
  weekly_views?: number;
  visibility: "public";
};

export type PublicProjectPage = {
  items: PublicProjectSummary[];
  limit: number;
  offset: number;
  total: number;
};

export type PublicProfileResponse = PublicProjectPage & {
  profile: PublicProjectOwner;
};

export type PublicProjectViewAnalytics = {
  project_id: string;
  recorded: boolean;
  unique_viewers: number;
  view_count: number;
  view_history: Array<{ date: string; views: number }>;
  views_7d: number;
};

export type ProjectSortMode = "recent" | "added";

export type ProjectDetailApiResponse = {
  activities: Array<{
    events: number;
    files_changed: number;
    id: string;
    last_activity_at: string | null;
    model: string;
    prompts: number;
    responses: number;
    started_at: string | null;
  }>;
  community?: {
    draft_flows: number;
    latest_flow_at: string | null;
    published_flows: number;
    recent_flows: Array<{
      file_count: number;
      id: string;
      prompt_count: number;
      published_at: string | null;
      slug: string;
      status: string;
      summary: string | null;
      title: string;
      updated_at: string | null;
      visibility: string;
    }>;
    total_flows: number;
  };
  memory?: {
    latest_artifact_at: string | null;
    latest_batch?: {
      batch_id: string;
      message: string;
      retryable?: boolean;
      status: string | null;
    } | null;
    recent_artifacts: Array<{
      artifact_stage?: string | null;
      changed_file_count: number;
      changed_files?: Array<{
        additions?: number | null;
        deletions?: number | null;
        path: string;
        status?: string | null;
      }>;
      commit_sha?: string | null;
      created_at: string | null;
      draft_confidence?: number | null;
      draft_generator?: string | null;
      draft_type?: string | null;
      end_sequence?: number | null;
      fallback_reason?: string | null;
      first_event_at?: string | null;
      generator: string | null;
      id: string;
      last_event_at?: string | null;
      memory_batch_id?: string | null;
      memory_batch_ids?: string[];
      memory_scope?: string | null;
      model: string | null;
      needs_user_verification?: boolean | null;
      outcome: string | null;
      prompt_count?: number | null;
      reason?: string | null;
      review_state?: string | null;
      requested_generator?: string | null;
      sections?: Array<{
        summary: string;
        title: string;
      }>;
      session_id?: string | null;
      source_draft_ids?: string[];
      source_session_ids?: string[];
      slice_index?: number | null;
      start_sequence?: number | null;
      summary: string | null;
      summary_level?: number | null;
      suggested_user_action?: string | null;
      tags: string[];
      technologies?: string[];
      title: string;
      trigger_reason?: string | null;
      type: string;
      updated_at: string | null;
      why_it_matters?: string | null;
      window_reason?: string | null;
      versions?: Array<{
        changed_file_count: number;
        changed_files?: Array<{
          additions?: number | null;
          deletions?: number | null;
          path: string;
          status?: string | null;
        }>;
        commit_sha?: string | null;
        created_at: string | null;
        end_sequence?: number | null;
        generator: string | null;
        id: string;
        memory_scope?: string | null;
        model: string | null;
        outcome: string | null;
        prompt_count?: number | null;
        reason?: string | null;
        review_state?: string | null;
        sections?: Array<{
          summary: string;
          title: string;
        }>;
        session_id: string | null;
        slice_index?: number | null;
        start_sequence?: number | null;
        summary: string | null;
        tags: string[];
        technologies?: string[];
        title: string;
        version: number;
        window_reason?: string | null;
      }>;
    }>;
    total_artifacts: number;
  };
  prompt_activities?: Array<{
    continuation_of?: string | null;
    delivery_mode?: "unknown" | "queued" | "interrupt" | null;
    file_changes?: Array<{
      additions: number | null;
      binary?: boolean;
      deletions: number | null;
      event_id?: string | null;
      old_path?: string | null;
      patch?: string | null;
      patch_omitted_reason?: string | null;
      patch_truncated?: boolean;
      path: string;
      status: string;
    }>;
    files_changed?: number;
    id: string;
    model: string;
    prompt: string;
    prompt_original_length?: number | null;
    prompt_storage_limit?: number | null;
    prompt_truncated?: boolean;
    response?: string | null;
    response_original_length?: number | null;
    response_received_at?: string | null;
    response_source?: string | null;
    response_storage_limit?: number | null;
    response_truncated?: boolean;
    root_prompt_event_id?: string | null;
    sequence: number;
    session_id: string;
    submission_context?: "idle" | "during_output" | null;
    submitted_at: string | null;
  }>;
  files?: FileTreeNode[];
  metrics: {
    activity_history?: Array<{
      date: string;
      files_changed: number;
      memories: number;
      prompts: number;
      sessions: number;
    }>;
    connected_models: string[];
    connected_tools?: string[];
    files_changed_since_yesterday?: number;
    latest_activity_at: string | null;
    last_modified_at: string | null;
    memory_artifacts_since_yesterday?: number;
    prompts_since_yesterday?: number;
    repository_connected: boolean;
    sessions_since_yesterday?: number;
    total_events: number;
    total_prompts?: number;
    total_sessions: number;
    tracked_files: number;
  };
  project: {
    created_at?: string | null;
    default_branch: string;
    description: string | null;
    id: string;
    is_bookmarked?: boolean;
    memory_grouping_mode?: MemoryGroupingMode;
    name: string;
    project_url: string | null;
    repository_status: string;
    repository_url: string | null;
    slug?: string;
    tags?: string[];
    updated_at: string | null;
    visibility?: string | null;
  };
};

export type ProjectMemoryArtifactApiResponse =
  NonNullable<ProjectDetailApiResponse["memory"]>["recent_artifacts"][number];

export type PublicProjectMemoryArtifactApiResponse = Omit<
  ProjectMemoryArtifactApiResponse,
  | "changed_files"
  | "commit_sha"
  | "draft_confidence"
  | "draft_generator"
  | "draft_type"
  | "end_sequence"
  | "fallback_reason"
  | "memory_batch_id"
  | "memory_batch_ids"
  | "needs_user_verification"
  | "requested_generator"
  | "review_state"
  | "session_id"
  | "slice_index"
  | "source_draft_ids"
  | "source_session_ids"
  | "start_sequence"
  | "suggested_user_action"
  | "trigger_reason"
  | "versions"
  | "window_reason"
> & {
  review_state: "edited" | "verified";
};

export type PublicProjectDetailResponse = Omit<
  ProjectDetailApiResponse,
  "activities" | "files" | "memory" | "prompt_activities"
> & {
  activities: [];
  files: [];
  is_owner: boolean;
  is_saved: boolean;
  memory: {
    latest_artifact_at: string | null;
    recent_artifacts: PublicProjectMemoryArtifactApiResponse[];
    total_artifacts: number;
  };
  owner: PublicProjectOwner;
  prompt_activities: [];
  unique_viewers?: number;
  view_count?: number;
  view_history?: Array<{ date: string; views: number }>;
  views_7d?: number;
};

export type ProjectPromptActivityApiItem = NonNullable<
  ProjectDetailApiResponse["prompt_activities"]
>[number];

export type ProjectPromptActivitiesApiResponse = {
  cursor: string | null;
  has_more: boolean;
  items: ProjectPromptActivityApiItem[];
  limit: number;
  next_cursor: string | null;
  query: string | null;
  scanned?: number;
  session_id: string | null;
  total: number | null;
};

export type ProjectFilesApiResponse = {
  files: FileTreeNode[];
  limit: number;
  total: number;
  truncated: boolean;
};

export type ProjectMemoryPendingRangeApiResponse = {
  can_checkpoint: boolean;
  changed_file_count: number;
  draft_id: string;
  end_sequence: number;
  event_count: number;
  file_change_event_count: number;
  first_event_at: string | null;
  last_event_at: string | null;
  prompt_count: number;
  response_count: number;
  session_id: string;
  start_sequence: number;
  tool: string;
};

export type MemoryReviewQueueSnapshotApiResponse = {
  errors?: Array<{
    message: string;
    project_id: string;
  }>;
  project_summaries: ProjectSummary[];
  projects: Array<{
    pending_count: number;
    project_id: string;
    ranges: ProjectMemoryPendingRangeApiResponse[];
  }>;
  total_pending_count: number;
};

export type ProjectGithubFilesApiResponse = {
  available: boolean;
  default_branch?: string;
  files: FileTreeNode[];
  message: string | null;
  repository: string | null;
  status: string;
  truncated?: boolean;
};

export type ProjectGithubFileContentApiResponse = {
  available: boolean;
  branch?: string;
  content: string | null;
  html_url?: string | null;
  message: string | null;
  name?: string;
  path?: string;
  repository?: string;
  size?: number | null;
  status: string;
};

export type PublishedFlowSummary = PublishedFlowDetail & {
  author: {
    avatar_url: string | null;
    id: string | null;
    username: string;
  };
  created_at: string | null;
  file_count: number;
  is_owner: boolean;
  metrics: Record<string, unknown>;
  model_name: string | null;
  prompt_count: number;
  published_at: string | null;
  slug: string;
  status: "archived" | "draft" | "published";
  summary: string | null;
  tags: string[];
  title: string;
  tool_name: string | null;
  updated_at: string | null;
  visibility: "private" | "public" | "unlisted";
};

export type PublishedFlowDetailResponse = PublishedFlowSummary & {
  assets: PublishedFlowAsset[];
  context_summary: string | null;
  end_sequence: number | null;
  files: Array<{
    additions: number;
    change_type: string | null;
    deletions: number;
    diff: string | null;
    file_path: string;
    id: string;
    is_included: boolean;
    language: string | null;
    source_event_id: string | null;
  }>;
  items: Array<{
    files_changed: number;
    id: string;
    is_included: boolean;
    item_order: number;
    model_name: string | null;
    prompt_text: string;
    response_received_at: string | null;
    response_text: string | null;
    sequence: number;
    source_event_id: string | null;
    submitted_at: string | null;
    tool_name: string | null;
  }>;
  notes: string | null;
  source_end_event_id: string | null;
  source_project_id: string | null;
  source_session_id: string | null;
  source_start_event_id: string | null;
  start_sequence: number | null;
};

export type AdminOverview = {
  action_items: Array<{
    area: string;
    condition_hash: string;
    count: number | null;
    detail: string;
    key: string;
    severity: string;
    snoozed_until: string | null;
    state: "read" | "snoozed" | "unread";
    target: string;
    title: string;
    window: string;
  }>;
  action_summary: {
    active: number;
    read: number;
    resolved: number;
    snoozed: number;
    unread: number;
  };
  ai_activity: {
    prompts_24h: number;
    response_gap: number;
    response_gap_24h: number;
    responses_24h: number;
    session_gaps: Array<{
      latest_event_at: string | null;
      missing_responses: number;
      project: {
        id: string;
        name: string;
      };
      prompts: number;
      responses: number;
      session_id: string;
      tool: string | null;
      user: {
        id: string;
        username: string;
      };
    }>;
  };
  breakdowns: {
    events_by_tool: Array<{ count: number; key: string }>;
    events_by_type: Array<{ count: number; key: string }>;
    jobs_by_status: Array<{ count: number; key: string }>;
    projects_by_visibility: Array<{ count: number; key: string }>;
  };
  generated_at: string | null;
  metrics: {
    active_collector_tokens: number;
    events: number;
    events_24h: number;
    events_7d: number;
    failed_jobs: number;
    failed_support_notifications: number;
    github_connections: number;
    memory_artifacts: number;
    memory_artifacts_24h: number;
    pending_jobs: number;
    pending_memory_drafts: number;
    open_support_inquiries: number;
    projects: number;
    projects_without_activity: number;
    projects_without_repo: number;
    public_project_views: number;
    public_project_views_24h: number;
    public_project_views_7d: number;
    prompts: number;
    prompts_24h: number;
    responses: number;
    responses_24h: number;
    running_jobs: number;
    sessions: number;
    stale_jobs: number;
    tracked_files: number;
    unique_public_viewers_7d: number;
    users: number;
  };
  memory_monitor: {
    failed_jobs: number;
    pending_drafts: number;
    pending_projects: number;
    recent_artifacts: Array<{
      changed_file_count: number;
      created_at: string | null;
      id: string;
      project: {
        id: string;
        name: string;
      };
      summary: string | null;
      title: string;
      updated_at: string | null;
    }>;
    stale_jobs: number;
    summaries_24h: number;
    total_summaries: number;
  };
  project_monitor: {
    without_activity: number;
    without_repo: number;
  };
  recent_admin_audit_logs: Array<{
    action: string;
    actor: {
      github_id: string;
      id: string | null;
      username: string;
    };
    created_at: string | null;
    id: string;
    request_method: string;
    request_path: string;
    resource_id: string | null;
    resource_type: string | null;
    status_code: number;
  }>;
  recent_events: Array<{
    created_at: string | null;
    event_type: string;
    id: string;
    project_id: string;
    sequence: number;
    session_id: string;
    tool: string;
  }>;
  recent_projects: Array<{
    counts: {
      events: number;
      files: number;
      memory: number;
      prompts: number;
      sessions: number;
      views: number;
    };
    default_branch: string;
    failed_jobs: number;
    github_connected: boolean;
    id: string;
    latest_event_at: string | null;
    latest_memory_at: string | null;
    name: string;
    owner: {
      id: string;
      username: string;
    };
    slug: string;
    tags: string[];
    updated_at: string | null;
  }>;
  recent_users: Array<{
    created_at: string | null;
    email: string | null;
    event_count: number;
    github_connected: boolean;
    id: string;
    latest_activity_at: string | null;
    prompt_count: number;
    project_count: number;
    session_count: number;
    username: string;
  }>;
  risks: Array<{
    acknowledged: boolean;
    acknowledged_at: string | null;
    acknowledged_by: string | null;
    detail: string;
    key: string;
    severity: string;
    title: string;
  }>;
  system: {
    admin_audit_retention_days: number;
    admin_configured: boolean;
    admin_rate_limit: {
      requests: number;
      window_seconds: number;
    };
    app_url: string;
    auth_rate_limit: {
      requests: number;
      window_seconds: number;
    };
    cors_origins: string[];
    gemini_configured: boolean;
    memory_generators: {
      draft: string;
      project: string;
    };
    openai_configured: boolean;
    published_flows_enabled: boolean;
    session_cookie_secure: boolean;
    session_cookie_samesite: string;
  };
  view_analytics: {
    top_projects: Array<{
      id: string;
      name: string;
      owner_username: string;
      view_count: number;
      views_7d: number;
    }>;
    total_views: number;
    unique_viewers_7d: number;
    views_24h: number;
    views_7d: number;
  };
};

export type AdminPage<T> = {
  items: T[];
  limit: number;
  offset: number;
  total: number;
};

export type AdminSupportInquiry = {
  category: string;
  created_at: string | null;
  id: string;
  message: string;
  notification_error: string | null;
  notification_status: string;
  requester_email: string;
  requester_username: string;
  status: "new" | "in_progress" | "resolved";
  subject: string;
  updated_at: string | null;
};

export type AdminUser = {
  active_collector_tokens: number;
  avatar_url: string | null;
  collector_tokens: AccountCollectorToken[];
  counts: {
    events: number;
    projects: number;
    prompts: number;
    sessions: number;
  };
  created_at: string | null;
  email: string | null;
  github: {
    connected: boolean;
    scopes: string[];
    updated_at: string | null;
  };
  github_id: string;
  id: string;
  is_admin: boolean;
  last_collector_at: string | null;
  latest_activity_at: string | null;
  status: "active" | "suspended";
  suspended_at: string | null;
  suspension_reason: string | null;
  username: string;
};

export type AdminProject = {
  active_jobs: number;
  created_at: string | null;
  default_branch: string;
  description: string | null;
  event_count: number;
  failed_jobs: number;
  github_connected: boolean;
  github_url: string | null;
  id: string;
  latest_activity_at: string | null;
  latest_memory_at: string | null;
  memory_count: number;
  name: string;
  owner: {
    id: string;
    username: string;
  };
  project_url: string | null;
  prompt_count: number;
  save_count: number;
  saves_7d: number;
  slug: string;
  tags: string[];
  updated_at: string | null;
  view_count: number;
  views_7d: number;
  unique_viewers_7d: number;
  weekly_popularity_score: number;
  visibility: "private" | "public";
};

export type AdminJob = {
  attempt_count: number;
  cancellable: boolean;
  completed_at: string | null;
  created_at: string | null;
  error: string | null;
  error_code: string | null;
  generator: string;
  id: string;
  lease_expires_at: string | null;
  owner: {
    id: string;
    username: string;
  };
  project: {
    id: string;
    name: string;
    slug: string;
  };
  reason: string;
  result_status: string | null;
  retryable: boolean;
  session_id: string | null;
  stale: boolean;
  status: "pending" | "running" | "succeeded" | "failed" | "superseded";
  updated_at: string | null;
};

export type AdminEvent = {
  created_at: string | null;
  event_type: string;
  id: string;
  owner: { id: string; username: string };
  payload: Record<string, unknown>;
  project: { id: string; name: string; slug: string };
  schema_version: number;
  sequence: number;
  session_id: string;
  tool: string;
};

export type AdminEventPage = AdminPage<AdminEvent> & {
  search_truncated: boolean;
};

export type AdminSystem = {
  database: {
    connections: Record<string, number>;
    dialect: string;
    migration: string | null;
    pool: string;
    size_bytes: number | null;
    table_sizes: Array<{ name: string; size_bytes: number }>;
  };
  deployment: {
    environment: string;
    region: string | null;
    release_sha: string | null;
  };
  providers: {
    gemini: { configured: boolean; model: string };
    openai: { configured: boolean; model: string };
    real_billing_available: boolean;
  };
  runtime: {
    api_url: string;
    app_url: string;
    platform: string;
    python: string;
    started_at: string | null;
    uptime_seconds: number;
  };
  worker: {
    pending_batches: number;
    running_batches: number;
    status: string;
  };
};

export type AdminAuditLog = AdminOverview["recent_admin_audit_logs"][number];

export type ProjectGithubFilesState = {
  files: FileTreeNode[];
  message?: string;
  repository?: string;
  status?: string;
  truncated?: boolean;
};

export type AuthStatus = "loading" | "authenticated" | "unauthenticated" | "error";
