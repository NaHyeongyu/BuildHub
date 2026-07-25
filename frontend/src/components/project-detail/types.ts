import type { ComponentType, ReactNode } from "react";
import type { LucideProps } from "lucide-react";
import type { MemoryGroupingMode } from "../../workspace/types";

export type ProjectDetailTabId = "overview" | "memory" | "ai-activity" | "files";

export type ProjectDetailTab = {
  externalHref?: string;
  externalIcon?: "github";
  id: ProjectDetailTabId;
  label: string;
};

export type ActivityViewId = "prompts" | "sessions";

export type ActivityNavigationState = {
  selectedPromptId: string | null;
  selectedSessionId: string | null;
  selectedSessionPromptId: string | null;
  view: ActivityViewId;
};

export type ProjectHeaderProps = {
  isBookmarked?: boolean;
  isBookmarkUpdating?: boolean;
  isLoading?: boolean;
  isShareCopied?: boolean;
  lastActivityLabel?: string;
  modelNames?: string[];
  name: string;
  onOpenAllProjects?: () => void;
  onConnectRepository?: () => void;
  onProjectSelect?: (projectId: string) => void;
  onShareProject?: () => void;
  onToggleBookmark?: () => void;
  projectOptions?: ProjectHeaderProjectOption[];
  repositoryStatus?: string;
  repositoryUrl?: string;
  selectedProjectId?: string;
};

export type ProjectDetailProject = ProjectHeaderProps & {
  defaultBranch?: string;
  description: string;
  id: string;
  isBookmarked: boolean;
  memoryGroupingMode: MemoryGroupingMode;
  projectUrl?: string;
  slug?: string;
  tags: string[];
  visibility?: "private" | "public";
};

export type ProjectHeaderProjectOption = {
  id: string;
  latestUpdatedAt?: string;
  name: string;
};

export type OverviewItem = {
  title: string;
  value: string;
  description?: string;
  href?: string;
  actions?: string[];
};

export type ProjectCommunityFlow = {
  fileCount: number;
  id: string;
  promptCount: number;
  publishedAt: string | null;
  slug: string;
  status: string;
  summary?: string | null;
  title: string;
  updatedAt: string | null;
  visibility: string;
};

export type ProjectCommunityStatus = {
  draftFlows: number;
  latestFlowAt: string | null;
  publishedFlows: number;
  recentFlows: ProjectCommunityFlow[];
  totalFlows: number;
};

export type ProjectMemoryArtifactVersion = {
  changedFileCount: number;
  changedFiles: Array<{
    additions?: number | null;
    deletions?: number | null;
    path: string;
    status?: string | null;
  }>;
  commitSha: string | null;
  createdAt: string | null;
  draftConfidence: number | null;
  draftGenerator: string | null;
  draftType: string | null;
  endSequence: number | null;
  fallbackReason: string | null;
  generator: string | null;
  id: string;
  memoryScope: ProjectMemoryScope | null;
  model: string | null;
  needsUserVerification: boolean | null;
  outcome: string | null;
  promptCount: number | null;
  reason: string | null;
  requestedGenerator: string | null;
  sections: Array<{
    summary: string;
    title: string;
  }>;
  sessionId: string | null;
  sliceIndex: number | null;
  startSequence: number | null;
  summary: string | null;
  suggestedUserAction: string | null;
  tags: string[];
  technologies: string[];
  title: string;
  version: number;
  windowReason: string | null;
};

export type ProjectMemoryArtifactStage =
  | "generated_memory"
  | "memory_draft"
  | "project_memory"
  | "verified_memory";

export type ProjectMemoryReviewState =
  | "draft"
  | "edited"
  | "generation_failed"
  | "generated"
  | "ignored"
  | "saved"
  | "verified";

export type ProjectMemoryScope = "draft" | "generated" | "project" | "verified";

export type ProjectMemoryArtifact = {
  artifactStage: ProjectMemoryArtifactStage | null;
  changedFileCount: number;
  changedFiles: Array<{
    additions?: number | null;
    deletions?: number | null;
    path: string;
    status?: string | null;
  }>;
  commitSha: string | null;
  createdAt: string | null;
  draftConfidence: number | null;
  draftGenerator: string | null;
  draftType: string | null;
  endSequence: number | null;
  fallbackReason: string | null;
  firstEventAt: string | null;
  generator: string | null;
  id: string;
  lastEventAt: string | null;
  memoryBatchId: string | null;
  memoryBatchIds: string[];
  memoryScope: ProjectMemoryScope | null;
  model: string | null;
  needsUserVerification: boolean | null;
  outcome: string | null;
  promptCount: number | null;
  reason: string | null;
  reviewState: ProjectMemoryReviewState | null;
  requestedGenerator: string | null;
  sections: Array<{
    summary: string;
    title: string;
  }>;
  sessionId: string | null;
  sourceDraftIds: string[];
  sourceSessionIds: string[];
  sliceIndex: number | null;
  startSequence: number | null;
  summary: string | null;
  summaryLevel: number | null;
  suggestedUserAction: string | null;
  tags: string[];
  technologies: string[];
  title: string;
  triggerReason: string | null;
  updatedAt: string | null;
  versions: ProjectMemoryArtifactVersion[];
  whyItMatters: string | null;
  windowReason: string | null;
};

export type ProjectMemoryStatus = {
  drafts: ProjectMemoryArtifact[];
  latestArtifactAt: string | null;
  latestBatch: {
    batchId: string;
    message: string;
    retryable: boolean;
    status: string | null;
  } | null;
  pendingRanges: ProjectMemoryPendingRange[];
  recentArtifacts: ProjectMemoryArtifact[];
  totalArtifacts: number;
};

export type ProjectMemoryPendingRange = {
  canCheckpoint: boolean;
  changedFileCount: number;
  draftId: string;
  endSequence: number;
  eventCount: number;
  fileChangeEventCount: number;
  firstEventAt: string | null;
  lastEventAt: string | null;
  promptCount: number;
  responseCount: number;
  sessionId: string;
  startSequence: number;
  tool: string;
};

export type ActivityItem = {
  id: string;
  label?: string;
  model: string;
  startedAt: string;
  lastActivity: string;
  prompts: number;
  responses: number;
  events: number;
  filesChanged: number;
};

export type PromptFileChange = {
  additions: number | null;
  binary?: boolean;
  deletions: number | null;
  oldPath?: string | null;
  patch?: string | null;
  patchOmittedReason?: string | null;
  patchTruncated?: boolean;
  path: string;
  status: string;
};

export type PromptActivityItem = {
  continuationOf?: string | null;
  deliveryMode?: "unknown" | "queued" | "interrupt" | null;
  fileChanges: PromptFileChange[];
  filesChanged: number;
  id: string;
  model: string;
  prompt: string;
  promptOriginalLength?: number | null;
  promptStorageLimit?: number | null;
  promptTruncated?: boolean;
  response?: string | null;
  responseOriginalLength?: number | null;
  responseReceivedAt?: string | null;
  responseSource?: string | null;
  responseStorageLimit?: number | null;
  responseTruncated?: boolean;
  rootPromptEventId?: string | null;
  sequence: number;
  sessionId: string;
  submissionContext?: "idle" | "during_output" | null;
  submittedAt: string;
};

export type PromptWorkGroup = {
  id: string;
  prompts: PromptActivityItem[];
};

export type PromptActivityPage = {
  cursor: string | null;
  hasMore: boolean;
  items: PromptActivityItem[];
  limit: number;
  nextCursor: string | null;
  query: string | null;
  scanned?: number;
  sessionId: string | null;
  total: number | null;
};

export type FileTreeNode = {
  name: string;
  path?: string;
  type: "folder" | "file";
  children?: FileTreeNode[];
};

export type RepositoryFileContent = {
  branch?: string;
  content?: string | null;
  htmlUrl?: string | null;
  message?: string | null;
  name?: string;
  path?: string;
  repository?: string;
  size?: number | null;
  status?: string;
};

export type EmptyStateProps = {
  icon?: ComponentType<LucideProps>;
  title: string;
  description: string;
  children?: ReactNode;
};

export type ProjectDetailData = {
  activities: ActivityItem[];
  community: ProjectCommunityStatus;
  files: FileTreeNode[];
  filesError?: string | null;
  filesLoading?: boolean;
  filesTotal?: number | null;
  filesTruncated?: boolean;
  memory: ProjectMemoryStatus;
  metricHistory: Array<{
    date: string;
    filesChanged: number;
    memories: number;
    prompts: number;
    sessions: number;
  }>;
  overview: OverviewItem[];
  promptActivities: PromptActivityItem[];
  project: ProjectDetailProject;
  repositoryFileContent?: RepositoryFileContent | null;
  repositoryFileContentError?: string;
  repositoryFileContentLoading?: boolean;
  repositoryFileSelectedPath?: string | null;
  repositoryFiles: FileTreeNode[];
  repositoryFilesConnectUrl?: string;
  repositoryFilesError?: string | null;
  repositoryFilesLoading?: boolean;
  repositoryFilesMessage?: string;
  repositoryFilesRepository?: string;
  repositoryFilesStatus?: string;
  repositoryFilesTruncated?: boolean;
};

export type PromptFlowUpdatePayload = {
  context_summary?: string | null;
  included_file_ids?: string[];
  included_item_ids?: string[];
  notes?: string | null;
  status?: "archived" | "draft" | "published";
  summary?: string | null;
  tags?: string[];
  title?: string;
  visibility?: "private" | "public" | "unlisted";
};

export type PromptFlowCreatePayload = {
  context_summary?: string | null;
  end_prompt_event_id?: string | null;
  notes?: string | null;
  project_id: string;
  prompt_event_ids?: string[];
  session_id?: string | null;
  start_prompt_event_id?: string | null;
  status?: "draft" | "published";
  summary?: string | null;
  tags?: string[];
  title?: string | null;
  visibility?: "private" | "public" | "unlisted";
};

export type PublishedFlowAsset = {
  alt_text?: string | null;
  byte_size: number;
  content_type: string;
  created_at: string | null;
  file_name: string;
  id: string;
  markdown: string;
  sha256: string;
  url: string;
};

export type PublishedFlowDetail = {
  id: string;
  slug: string;
  title: string;
  summary?: string | null;
  visibility: "private" | "public" | "unlisted";
  status: "archived" | "draft" | "published";
  prompt_count: number;
  file_count: number;
  assets?: PublishedFlowAsset[];
};
