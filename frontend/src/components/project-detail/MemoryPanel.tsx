import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import {
  ArrowRight,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  FileText,
  MessageSquareText,
  Network,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import {
  type MemoryGenerationReviewPrompt,
  type MemoryGenerationReviewResponse,
  type ProjectMemoryGenerationResponse,
} from "../../api/projects";
import { useI18n } from "../../i18n/I18nProvider";
import { MarkdownContent } from "../MarkdownContent";
import { ContextGraphPanel } from "./ContextGraphPanel";
import { displayMemoryOutcome } from "./memoryOutcome";
import { focusableModalElements } from "./modalFocus";
import type {
  ProjectDetailData,
  ProjectMemoryArtifact,
} from "./types";

export type MemoryGenerationResult = ProjectMemoryGenerationResponse;

export type MemoryPanelView = "current" | "graph" | "history";

type MemoryReviewBrowseMode = "prompts" | "sessions";

type MemoryReviewDeletionTarget =
  | { eventId: string; kind: "prompt" }
  | { kind: "session"; promptCount: number; sessionId: string };

const REVIEW_TEXT_PREVIEW_CHARACTERS = 320;
const REVIEW_TEXT_PREVIEW_LINES = 6;

export const MEMORY_PANEL_VIEWS: MemoryPanelView[] = ["history", "current", "graph"];

function memoryReviewPromptMatches(
  prompt: MemoryGenerationReviewPrompt,
  normalizedQuery: string,
) {
  if (!normalizedQuery) {
    return true;
  }
  return [
    prompt.text,
    prompt.response_preview ?? "",
    prompt.session_id,
    prompt.tool,
    prompt.created_at,
    String(prompt.sequence),
  ].some((value) => value.toLocaleLowerCase().includes(normalizedQuery));
}

function compactReviewText(text: string) {
  const lines = text.split(/\r?\n/);
  const previewByLines = lines.slice(0, REVIEW_TEXT_PREVIEW_LINES).join("\n");
  const preview = previewByLines.slice(0, REVIEW_TEXT_PREVIEW_CHARACTERS).trimEnd();
  const isLong =
    lines.length > REVIEW_TEXT_PREVIEW_LINES ||
    previewByLines.length > REVIEW_TEXT_PREVIEW_CHARACTERS;
  return {
    isLong,
    preview: isLong ? `${preview}…` : text,
  };
}

function ExpandableReviewText({ text }: { text: string }) {
  const { t } = useI18n();
  const [isExpanded, setIsExpanded] = useState(false);
  const { isLong, preview } = useMemo(() => compactReviewText(text), [text]);

  return (
    <div className="bh-memory-review-expandable-text" data-expanded={isExpanded || undefined}>
      <p>{isExpanded || !isLong ? text : preview}</p>
      {isLong ? (
        <button
          aria-expanded={isExpanded}
          onClick={() => setIsExpanded((expanded) => !expanded)}
          type="button"
        >
          {isExpanded ? t("activity.showLess") : t("activity.showMore")}
        </button>
      ) : null}
    </div>
  );
}

export function nextMemoryPanelView(
  activeView: MemoryPanelView,
  key: "ArrowLeft" | "ArrowRight" | "End" | "Home",
) {
  const currentIndex = MEMORY_PANEL_VIEWS.indexOf(activeView);
  const nextIndex =
    key === "Home"
      ? 0
      : key === "End"
        ? MEMORY_PANEL_VIEWS.length - 1
        : key === "ArrowRight"
          ? (currentIndex + 1) % MEMORY_PANEL_VIEWS.length
          : (currentIndex - 1 + MEMORY_PANEL_VIEWS.length) %
            MEMORY_PANEL_VIEWS.length;
  return MEMORY_PANEL_VIEWS[nextIndex] ?? activeView;
}

function isGeneratedMemoryArtifact(artifact: ProjectMemoryArtifact) {
  return (
    artifact.artifactStage === "generated_memory" ||
    artifact.artifactStage === "verified_memory" ||
    artifact.memoryScope === "generated" ||
    artifact.memoryScope === "verified"
  );
}

function memoryArtifactStatusLabel(artifact: ProjectMemoryArtifact) {
  if (artifact.memoryScope === "verified" || artifact.artifactStage === "verified_memory") {
    return "Verified";
  }
  return null;
}

function memoryArtifactFileCount(artifact: ProjectMemoryArtifact) {
  const listedFileCount = (artifact.changedFiles ?? []).filter((file) =>
    file.path?.trim(),
  ).length;
  return Math.max(artifact.changedFileCount ?? 0, listedFileCount);
}

function memoryArtifactFileCountLabel(artifact: ProjectMemoryArtifact) {
  const count = memoryArtifactFileCount(artifact);
  if (count === 0) {
    return null;
  }
  return `${count} ${count === 1 ? "file" : "files"} changed`;
}

function memoryArtifactSortTimestamp(artifact: ProjectMemoryArtifact) {
  const candidates = [
    artifact.lastEventAt,
    artifact.firstEventAt,
    artifact.updatedAt,
    artifact.createdAt,
  ];
  for (const value of candidates) {
    if (!value) {
      continue;
    }
    const timestamp = new Date(value).getTime();
    if (!Number.isNaN(timestamp)) {
      return timestamp;
    }
  }
  return 0;
}

function compareMemoryArtifactsByLatest(
  left: ProjectMemoryArtifact,
  right: ProjectMemoryArtifact,
) {
  const timestampDifference =
    memoryArtifactSortTimestamp(right) - memoryArtifactSortTimestamp(left);
  if (timestampDifference !== 0) {
    return timestampDifference;
  }

  const rightSequence = right.endSequence ?? right.startSequence ?? -1;
  const leftSequence = left.endSequence ?? left.startSequence ?? -1;
  if (rightSequence !== leftSequence) {
    return rightSequence - leftSequence;
  }

  return right.id.localeCompare(left.id);
}

function formatMemoryDate(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return Intl.DateTimeFormat("en-US", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(date);
}

function formatMemoryDateRange(
  firstEventAt: string | null | undefined,
  lastEventAt: string | null | undefined,
) {
  const start = formatMemoryDate(firstEventAt);
  const end = formatMemoryDate(lastEventAt ?? firstEventAt);
  if (!start && !end) {
    return null;
  }
  if (!start) {
    return end;
  }
  if (!end) {
    return start;
  }
  return `${start} - ${end}`;
}

function combinedMemoryDateRange(
  items: Array<{ firstEventAt: string | null; lastEventAt: string | null }>,
) {
  const timestamps = items
    .flatMap((item) => [item.firstEventAt, item.lastEventAt])
    .map((value) => (value ? new Date(value).getTime() : Number.NaN))
    .filter((value) => !Number.isNaN(value));
  if (timestamps.length === 0) {
    return null;
  }
  return formatMemoryDateRange(
    new Date(Math.min(...timestamps)).toISOString(),
    new Date(Math.max(...timestamps)).toISOString(),
  );
}

function MemoryArtifactCard({
  artifact,
  isSelected = false,
  onSelect,
}: {
  artifact: ProjectMemoryArtifact;
  isSelected?: boolean;
  onSelect: (artifact: ProjectMemoryArtifact) => void;
}) {
  const dateRange = formatMemoryDateRange(artifact.firstEventAt, artifact.lastEventAt);
  const statusLabel = memoryArtifactStatusLabel(artifact);
  const fileCountLabel = memoryArtifactFileCountLabel(artifact);
  const hasHeaderMeta = Boolean(dateRange || statusLabel);

  return (
    <button
      aria-haspopup="dialog"
      aria-pressed={isSelected}
      className="bh-memory-context-item"
      data-selected={isSelected ? "true" : "false"}
      onClick={() => onSelect(artifact)}
      type="button"
    >
      {hasHeaderMeta ? (
        <span className="bh-memory-context-card-header">
          {dateRange ? <span className="bh-memory-context-date">{dateRange}</span> : null}
          {statusLabel ? (
            <span className="bh-memory-context-kind">{statusLabel}</span>
          ) : null}
        </span>
      ) : null}
      <span className="bh-memory-context-title-row">
        <span className="bh-memory-context-title">{artifact.title}</span>
        <ChevronRight aria-hidden="true" size={16} strokeWidth={1.6} />
      </span>
      {artifact.summary ? (
        <span className="bh-memory-context-summary">{artifact.summary}</span>
      ) : null}
      {fileCountLabel ? (
        <small className="bh-memory-context-fallback-meta">{fileCountLabel}</small>
      ) : null}
    </button>
  );
}

function MemoryArtifactDetailDrawer({
  artifact,
  artifactCount,
  artifactPosition,
  onClose,
  onNext,
  onOpenSession,
  onPrevious,
}: {
  artifact: ProjectMemoryArtifact;
  artifactCount: number;
  artifactPosition: number;
  onClose: () => void;
  onNext?: () => void;
  onOpenSession?: (sessionId: string) => void;
  onPrevious?: () => void;
}) {
  const { t } = useI18n();
  const drawerRef = useRef<HTMLElement | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const sections = (artifact.sections ?? []).filter(
    (section) => section.title?.trim() && section.summary?.trim(),
  );
  const outcome = displayMemoryOutcome(artifact.outcome, artifact.summary);
  const dateRange = formatMemoryDateRange(artifact.firstEventAt, artifact.lastEventAt);
  const statusLabel = memoryArtifactStatusLabel(artifact);
  const changedFiles = (artifact.changedFiles ?? []).filter((file) => file.path?.trim());
  const changedFileCount = memoryArtifactFileCount(artifact);
  const sourceSessionIds = Array.from(
    new Set(
      [artifact.sessionId, ...(artifact.sourceSessionIds ?? [])].filter(
        (sessionId): sessionId is string => Boolean(sessionId),
      ),
    ),
  );
  const metricRows = [
    {
      label: sourceSessionIds.length === 1 ? "Session" : "Sessions",
      value: sourceSessionIds.length > 0 ? sourceSessionIds.length.toString() : null,
    },
    {
      label: artifact.promptCount === 1 ? "Prompt" : "Prompts",
      value: artifact.promptCount ? artifact.promptCount.toString() : null,
    },
    {
      label: changedFileCount === 1 ? "File" : "Files",
      value: changedFileCount > 0 ? changedFileCount.toString() : null,
    },
  ].filter((row) => row.value);
  const provenanceRows = [
    { label: "Covered dates", value: dateRange },
    { label: "Created", value: formatMemoryDate(artifact.createdAt) },
    { label: "Last updated", value: formatMemoryDate(artifact.updatedAt) },
    { label: "Status", value: statusLabel },
  ].filter((row) => row.value);

  useEffect(() => {
    const previousActiveElement = document.activeElement;
    drawerRef.current?.focus();
    return () => {
      if (previousActiveElement instanceof HTMLElement) {
        previousActiveElement.focus();
      }
    };
  }, []);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: 0 });
  }, [artifact.id]);

  const keepFocusInsideDrawer = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key !== "Tab") {
      return;
    }
    const focusableElements = Array.from(
      drawerRef.current?.querySelectorAll<HTMLElement>(
        [
          "a[href]",
          "button:not([disabled])",
          "textarea:not([disabled])",
          "input:not([disabled])",
          "select:not([disabled])",
          "summary",
          "[tabindex]:not([tabindex='-1'])",
        ].join(","),
      ) ?? [],
    );
    if (focusableElements.length === 0) {
      event.preventDefault();
      return;
    }
    const firstElement = focusableElements[0];
    const lastElement = focusableElements[focusableElements.length - 1];
    if (event.shiftKey && document.activeElement === firstElement) {
      event.preventDefault();
      lastElement.focus();
      return;
    }
    if (!event.shiftKey && document.activeElement === lastElement) {
      event.preventDefault();
      firstElement.focus();
    }
  };

  return (
    <div
      className="bh-memory-detail-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
      role="presentation"
    >
      <section
        aria-labelledby="memory-detail-title"
        aria-modal="true"
        className="bh-memory-detail-drawer"
        ref={drawerRef}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            onClose();
            return;
          }
          keepFocusInsideDrawer(event);
        }}
        role="dialog"
        tabIndex={-1}
      >
        <header className="bh-memory-detail-header">
          <div className="bh-memory-detail-topbar">
            <div
              aria-label={t("memory.historyNavigation")}
              className="bh-memory-detail-navigation"
              role="group"
            >
              <button
                aria-label={t("memory.viewNewer")}
                className="bh-memory-detail-navigation-button"
                disabled={!onPrevious}
                onClick={onPrevious}
                title={t("memory.newer")}
                type="button"
              >
                <ChevronLeft aria-hidden="true" size={16} strokeWidth={1.5} />
              </button>
              <span>
                {artifactPosition} of {artifactCount}
              </span>
              <button
                aria-label={t("memory.viewOlder")}
                className="bh-memory-detail-navigation-button"
                disabled={!onNext}
                onClick={onNext}
                title={t("memory.older")}
                type="button"
              >
                <ChevronRight aria-hidden="true" size={16} strokeWidth={1.5} />
              </button>
            </div>
            <button
              aria-label={t("memory.closeDetails")}
              className="bh-icon-button"
              onClick={onClose}
              type="button"
            >
              <X aria-hidden="true" size={16} strokeWidth={1.5} />
            </button>
          </div>
          <div className="bh-memory-detail-heading">
            <div className="bh-memory-detail-eyebrow">
              <span>{dateRange ?? t("memory.projectMemory")}</span>
              {statusLabel ? (
                <span className="bh-memory-detail-status">{statusLabel}</span>
              ) : null}
            </div>
            <h2 id="memory-detail-title">{artifact.title}</h2>
          </div>

          {metricRows.length > 0 ? (
            <dl className="bh-memory-detail-metrics">
              {metricRows.map((row) => (
                <div key={row.label}>
                  <dt>{row.label}</dt>
                  <dd>{row.value}</dd>
                </div>
              ))}
            </dl>
          ) : null}
        </header>

        <div className="bh-memory-detail-body" ref={bodyRef}>
          <article className="bh-memory-detail-summary">
            <span className="bh-memory-step-kicker">{t("memory.summary")}</span>
            <p>{artifact.summary || t("memory.noSummary")}</p>
          </article>

          {outcome ? (
            <article className="bh-memory-detail-outcome">
              <CheckCircle2 aria-hidden="true" size={18} strokeWidth={1.5} />
              <div>
                <span className="bh-memory-step-kicker">{t("memory.outcome")}</span>
                <p>{outcome}</p>
              </div>
            </article>
          ) : null}

          {sections.length > 0 ? (
            <section
              aria-labelledby="memory-detail-notes-title"
              className="bh-memory-detail-content-section"
            >
              <div className="bh-memory-detail-section-heading">
                <h3 id="memory-detail-notes-title">{t("memory.keyNotes")}</h3>
                <span>{sections.length}</span>
              </div>
              <ol className="bh-memory-detail-note-list">
                {sections.map((section, index) => (
                  <li key={`${artifact.id}-${section.title}`}>
                    <span aria-hidden="true" className="bh-memory-detail-note-number">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <div>
                      <h4>{section.title}</h4>
                      <p>{section.summary}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </section>
          ) : null}

          {changedFiles.length > 0 ? (
            <section
              aria-labelledby="memory-detail-files-title"
              className="bh-memory-detail-content-section"
            >
              <div className="bh-memory-detail-section-heading">
                <h3 id="memory-detail-files-title">{t("memory.changedFiles")}</h3>
                <span>{changedFileCount}</span>
              </div>
              <ul className="bh-memory-detail-file-list" id="memory-detail-files-list">
                {changedFiles.map((file) => {
                  const pathSegments = file.path.split("/");
                  const fileName = pathSegments.pop() ?? file.path;
                  const directory = pathSegments.join("/");
                  return (
                    <li key={`${artifact.id}-${file.path}`}>
                      <span
                        aria-label={file.status ? `${file.status} file` : "Changed file"}
                        className="bh-memory-detail-file-status"
                        data-status={file.status}
                      >
                        {file.status?.trim().charAt(0).toUpperCase() || "•"}
                      </span>
                      <span className="bh-memory-detail-file-copy">
                        <strong>{fileName}</strong>
                        {directory ? <small>{directory}</small> : null}
                      </span>
                    </li>
                  );
                })}
              </ul>
              {changedFileCount > changedFiles.length ? (
                <small className="bh-memory-detail-muted">
                  {changedFileCount - changedFiles.length} additional changed{" "}
                  {changedFileCount - changedFiles.length === 1
                    ? "file is"
                    : "files are"}{" "}
                  not listed.
                </small>
              ) : null}
            </section>
          ) : null}

          {provenanceRows.length > 0 || sourceSessionIds.length > 0 ? (
            <details className="bh-memory-detail-provenance">
              <summary>
                <span>
                  <strong>{t("memory.sourcesDetails")}</strong>
                  <small>
                    {sourceSessionIds.length > 0
                      ? `${sourceSessionIds.length} source ${
                          sourceSessionIds.length === 1 ? "session" : "sessions"
                        }`
                      : t("memory.metadata")}
                  </small>
                </span>
                <ChevronRight aria-hidden="true" size={16} strokeWidth={1.5} />
              </summary>
              <div className="bh-memory-detail-provenance-body">
                {provenanceRows.length > 0 ? (
                  <dl className="bh-memory-detail-meta">
                    {provenanceRows.map((row) => (
                      <div key={row.label}>
                        <dt>{row.label}</dt>
                        <dd>{row.value}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
                {sourceSessionIds.length > 0 && onOpenSession ? (
                  <div className="bh-memory-source-actions">
                    {sourceSessionIds.map((sessionId, index) => (
                      <button
                        className="bh-memory-source-action"
                        key={sessionId}
                        onClick={() => onOpenSession(sessionId)}
                        type="button"
                      >
                        <span>
                          {sourceSessionIds.length === 1
                            ? "Open source session"
                            : `Open source session ${index + 1}`}
                        </span>
                        <ArrowRight aria-hidden="true" size={15} strokeWidth={1.5} />
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            </details>
          ) : null}
        </div>
      </section>
    </div>
  );
}

export function MemoryPanel({
  data,
  externalAiAllowed = false,
  externalAiProviders = [],
  isExternalAiConsentSaving = false,
  isProjectMemoryGenerationActive = false,
  isProjectMemoryGenerationDelayed = false,
  onApproveProjectMemory,
  onDeletePromptActivity,
  onDeleteSessionActivity,
  onGenerateProjectMemory,
  onLoadMemoryGenerationReview,
  onLoadMemoryArtifacts,
  onOpenSession,
  onUpdateExternalAiConsent,
}: {
  data: ProjectDetailData;
  externalAiAllowed?: boolean;
  externalAiProviders?: Array<"gemini" | "openai">;
  isExternalAiConsentSaving?: boolean;
  isProjectMemoryGenerationActive?: boolean;
  isProjectMemoryGenerationDelayed?: boolean;
  onApproveProjectMemory?: () => Promise<void>;
  onDeletePromptActivity?: (promptEventId: string) => Promise<void>;
  onDeleteSessionActivity?: (sessionId: string) => Promise<void>;
  onGenerateProjectMemory?: (
    reviewToken: string,
    excludedPromptEventIds: string[],
  ) => Promise<MemoryGenerationResult>;
  onLoadMemoryGenerationReview?: () => Promise<MemoryGenerationReviewResponse>;
  onLoadMemoryArtifacts?: (limit: number) => Promise<ProjectMemoryArtifact[]>;
  onOpenSession?: (sessionId: string) => void;
  onUpdateExternalAiConsent?: (allowExternalAi: boolean) => Promise<boolean>;
}) {
  const { localeTag, t } = useI18n();
  const [loadedArtifacts, setLoadedArtifacts] = useState<ProjectMemoryArtifact[] | null>(null);
  const [isApprovingForAgents, setIsApprovingForAgents] = useState(false);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const displayedArtifacts = loadedArtifacts ?? data.memory.recentArtifacts;
  const projectMemoryArtifact = data.memory.recentArtifacts.find(
    (artifact) =>
      artifact.artifactStage === "project_memory" || artifact.memoryScope === "project",
  );
  const projectMemoryNeedsApproval = projectMemoryArtifact?.reviewState === "generated";
  const projectMemoryIsApproved =
    projectMemoryArtifact?.reviewState === "verified" ||
    projectMemoryArtifact?.reviewState === "edited";
  const generatedArtifacts = useMemo(
    () =>
      [...displayedArtifacts]
        .filter(isGeneratedMemoryArtifact)
        .sort(compareMemoryArtifactsByLatest),
    [displayedArtifacts],
  );
  const totalGeneratedArtifactCount = Math.max(
    data.memory.totalArtifacts,
    generatedArtifacts.length,
  );
  const pendingDateRange = combinedMemoryDateRange(data.memory.pendingRanges);
  const pendingDraftKey = data.memory.pendingRanges
    .map((range) => range.draftId)
    .sort()
    .join(":");
  const resultDateRange = combinedMemoryDateRange(generatedArtifacts);
  const hasPendingDocumentation = data.memory.pendingRanges.length > 0;
  const pendingPromptCount = data.memory.pendingRanges.reduce(
    (total, range) => total + range.promptCount,
    0,
  );
  const pendingSessionCount = new Set(
    data.memory.pendingRanges.map((range) => range.sessionId),
  ).size;
  const generatedSummaryCountLabel =
    totalGeneratedArtifactCount > generatedArtifacts.length
      ? `${generatedArtifacts.length} of ${totalGeneratedArtifactCount} memory items`
      : `${generatedArtifacts.length} ${
          generatedArtifacts.length === 1 ? "memory item" : "memory items"
        }`;
  const nextArtifactLoadLimit = Math.min(
    Math.max(generatedArtifacts.length + 10, 20),
    Math.max(totalGeneratedArtifactCount, generatedArtifacts.length + 10),
    100,
  );
  const canLoadMoreArtifacts =
    Boolean(onLoadMemoryArtifacts) &&
    generatedArtifacts.length < totalGeneratedArtifactCount &&
    generatedArtifacts.length < 100;
  const [generationStatus, setGenerationStatus] = useState<string | null>(null);
  const [isGenerationStatusDelayed, setIsGenerationStatusDelayed] = useState(false);
  const [generationError, setGenerationError] = useState<string | null>(null);
  const [generationRetryable, setGenerationRetryable] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);
  const [generationReview, setGenerationReview] = useState<MemoryGenerationReviewResponse | null>(null);
  const [reviewBrowseMode, setReviewBrowseMode] =
    useState<MemoryReviewBrowseMode>("sessions");
  const [reviewSearch, setReviewSearch] = useState("");
  const [expandedReviewSessionIds, setExpandedReviewSessionIds] =
    useState<Set<string>>(() => new Set());
  const [reviewDeletionTarget, setReviewDeletionTarget] =
    useState<MemoryReviewDeletionTarget | null>(null);
  const [isReviewDeleting, setIsReviewDeleting] = useState(false);
  const [showExternalAiConsent, setShowExternalAiConsent] = useState(false);
  const [externalAiConsentChecked, setExternalAiConsentChecked] = useState(false);
  const [isReviewLoading, setIsReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [isRemoteGenerationActive, setIsRemoteGenerationActive] = useState(false);
  const [isArtifactHistoryLoading, setIsArtifactHistoryLoading] = useState(false);
  const [artifactHistoryError, setArtifactHistoryError] = useState<string | null>(null);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [activeMemoryView, setActiveMemoryView] =
    useState<MemoryPanelView>("history");
  const memoryViewTabListRef = useRef<HTMLDivElement | null>(null);
  const generationDialogRef = useRef<HTMLElement | null>(null);
  const generationDialogReturnFocusRef = useRef<HTMLElement | null>(null);
  const generationDialogBlockedRef = useRef(false);
  const generationRequestRef = useRef(0);
  const projectIdRef = useRef(data.project.id);
  const previousSharedDelayedRef = useRef(isProjectMemoryGenerationDelayed);
  const persistedGenerationError =
    data.memory.latestBatch?.status === "generation_failed"
      ? data.memory.latestBatch.message
      : null;
  const visibleGenerationError = generationError ?? persistedGenerationError;
  const visibleGenerationRetryable = generationError
    ? generationRetryable
    : (data.memory.latestBatch?.retryable ?? true);
  const isGenerationActive =
    isGenerating ||
    isRemoteGenerationActive ||
    isProjectMemoryGenerationActive;
  const selectedArtifact =
    generatedArtifacts.find((artifact) => artifact.id === selectedArtifactId) ?? null;
  const selectedArtifactIndex = selectedArtifact
    ? generatedArtifacts.findIndex((artifact) => artifact.id === selectedArtifact.id)
    : -1;
  const generationModalMode = showExternalAiConsent
    ? "consent"
    : generationReview
      ? "review"
      : null;
  const isGenerationModalOpen = generationModalMode !== null;
  generationDialogBlockedRef.current =
    isGenerating || isExternalAiConsentSaving || isReviewDeleting;
  const reviewSessionGroups = useMemo(() => {
    const groups = new Map<string, MemoryGenerationReviewPrompt[]>();
    for (const prompt of generationReview?.prompts ?? []) {
      const prompts = groups.get(prompt.session_id) ?? [];
      prompts.push(prompt);
      groups.set(prompt.session_id, prompts);
    }
    return Array.from(groups, ([sessionId, prompts]) => ({ prompts, sessionId }));
  }, [generationReview]);
  const normalizedReviewSearch = reviewSearch.trim().toLocaleLowerCase();
  const visibleReviewPrompts = useMemo(
    () =>
      (generationReview?.prompts ?? []).filter((prompt) =>
        memoryReviewPromptMatches(prompt, normalizedReviewSearch),
      ),
    [generationReview, normalizedReviewSearch],
  );
  const visibleReviewSessionGroups = useMemo(
    () =>
      reviewSessionGroups.flatMap((group) => {
        if (group.sessionId.toLocaleLowerCase().includes(normalizedReviewSearch)) {
          return [group];
        }
        const prompts = group.prompts.filter((prompt) =>
          memoryReviewPromptMatches(prompt, normalizedReviewSearch),
        );
        return prompts.length ? [{ ...group, prompts }] : [];
      }),
    [normalizedReviewSearch, reviewSessionGroups],
  );
  const providerNames = (
    generationReview?.providers.length
      ? generationReview.providers
      : externalAiProviders
  ).map((provider) => (provider === "openai" ? "OpenAI" : "Google Gemini"));
  const providerLabel = providerNames.join(" / ") || t("memory.aiConsent.providerFallback");

  useEffect(() => {
    setLoadedArtifacts(null);
    setArtifactHistoryError(null);
  }, [data.project.id, data.memory.latestArtifactAt, data.memory.totalArtifacts]);

  useEffect(() => {
    setActiveMemoryView("history");
  }, [data.project.id]);

  useEffect(() => {
    projectIdRef.current = data.project.id;
    generationRequestRef.current += 1;
    setGenerationError(null);
    setGenerationRetryable(true);
    setGenerationStatus(null);
    setIsGenerationStatusDelayed(false);
    setIsGenerating(false);
    setIsRemoteGenerationActive(false);
    setGenerationReview(null);
    setReviewBrowseMode("sessions");
    setReviewSearch("");
    setExpandedReviewSessionIds(new Set());
    setReviewDeletionTarget(null);
    setIsReviewDeleting(false);
    setShowExternalAiConsent(false);
    setExternalAiConsentChecked(false);
  }, [data.project.id]);

  useEffect(() => {
    if (!isGenerationModalOpen) {
      return;
    }
    generationDialogReturnFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.body.classList.add("modal-open");
    const handleModalKeyDown = (event: KeyboardEvent) => {
      const dialog = generationDialogRef.current;
      if (!dialog) {
        return;
      }
      if (event.key === "Escape") {
        if (generationDialogBlockedRef.current) {
          return;
        }
        event.preventDefault();
        setGenerationReview(null);
        setShowExternalAiConsent(false);
        setReviewDeletionTarget(null);
        setReviewError(null);
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const focusable = focusableModalElements(dialog);
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleModalKeyDown);
    return () => {
      window.removeEventListener("keydown", handleModalKeyDown);
      document.body.classList.remove("modal-open");
      generationDialogReturnFocusRef.current?.focus();
      generationDialogReturnFocusRef.current = null;
    };
  }, [isGenerationModalOpen]);

  useEffect(() => {
    if (!generationModalMode) {
      return;
    }
    const focusDialog = window.requestAnimationFrame(() => {
      const dialog = generationDialogRef.current;
      if (dialog) {
        focusableModalElements(dialog)[0]?.focus();
      }
    });
    return () => window.cancelAnimationFrame(focusDialog);
  }, [generationModalMode]);

  useEffect(() => {
    setGenerationError(null);
    setGenerationRetryable(true);
  }, [pendingDraftKey]);

  useEffect(() => {
    const wasDelayed = previousSharedDelayedRef.current;
    previousSharedDelayedRef.current = isProjectMemoryGenerationDelayed;
    if (
      wasDelayed &&
      !isProjectMemoryGenerationDelayed &&
      isGenerationStatusDelayed
    ) {
      setGenerationStatus(null);
      setIsGenerationStatusDelayed(false);
    }
  }, [isGenerationStatusDelayed, isProjectMemoryGenerationDelayed]);

  useEffect(() => {
    if (!selectedArtifactId) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelectedArtifactId(null);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [selectedArtifactId]);

  useEffect(() => {
    if (
      selectedArtifactId &&
      !generatedArtifacts.some((artifact) => artifact.id === selectedArtifactId)
    ) {
      setSelectedArtifactId(null);
    }
  }, [generatedArtifacts, selectedArtifactId]);

  const createProjectMemory = async () => {
    if (
      !onGenerateProjectMemory ||
      !generationReview ||
      !hasPendingDocumentation ||
      isGenerationActive ||
      (visibleGenerationError !== null && !visibleGenerationRetryable)
    ) {
      return;
    }

    setIsGenerating(true);
    setGenerationError(null);
    setGenerationRetryable(true);
    setGenerationStatus(null);
    setIsGenerationStatusDelayed(false);
    setIsRemoteGenerationActive(false);
    const projectId = data.project.id;
    const requestId = generationRequestRef.current + 1;
    generationRequestRef.current = requestId;
    const reviewToken = generationReview.review_token;
    setGenerationReview(null);
    try {
      const result = await onGenerateProjectMemory(reviewToken, []);
      if (
        projectIdRef.current !== projectId ||
        generationRequestRef.current !== requestId
      ) {
        return;
      }
      if (result.status === "generation_failed") {
        setGenerationError(result.message);
        setGenerationRetryable(result.retryable !== false);
      } else if (result.status === "generation_in_progress") {
        setGenerationStatus(result.message);
        setIsGenerationStatusDelayed(false);
        setIsRemoteGenerationActive(true);
      } else if (result.status === "generation_delayed") {
        setGenerationStatus(result.message);
        setIsGenerationStatusDelayed(true);
      } else {
        setGenerationStatus(result.message);
        setIsGenerationStatusDelayed(false);
      }
    } catch (error) {
      if (
        projectIdRef.current !== projectId ||
        generationRequestRef.current !== requestId
      ) {
        return;
      }
      setGenerationError(
        error instanceof Error ? error.message : t("memory.failed"),
      );
    } finally {
      if (
        projectIdRef.current === projectId &&
        generationRequestRef.current === requestId
      ) {
        setIsGenerating(false);
      }
    }
  };

  const loadMoreArtifacts = async () => {
    if (!onLoadMemoryArtifacts || isArtifactHistoryLoading) {
      return;
    }

    setIsArtifactHistoryLoading(true);
    setArtifactHistoryError(null);
    try {
      setLoadedArtifacts(await onLoadMemoryArtifacts(nextArtifactLoadLimit));
    } catch (error) {
      setArtifactHistoryError(
        error instanceof Error ? error.message : t("memory.historyLoadFailed"),
      );
    } finally {
      setIsArtifactHistoryLoading(false);
    }
  };

  const openGenerationReview = async () => {
    if (!onLoadMemoryGenerationReview || isGenerationActive) return;
    setIsReviewLoading(true);
    setReviewError(null);
    try {
      const review = await onLoadMemoryGenerationReview();
      setGenerationReview(review);
      setReviewBrowseMode("sessions");
      setReviewSearch("");
      setExpandedReviewSessionIds(
        new Set(review.prompts.map((prompt) => prompt.session_id)),
      );
      setReviewDeletionTarget(null);
      setShowExternalAiConsent(false);
    } catch (error) {
      setReviewError(error instanceof Error ? error.message : t("memory.review.loadFailed"));
    } finally {
      setIsReviewLoading(false);
    }
  };

  const startProjectMemoryFlow = () => {
    setReviewError(null);
    if (!externalAiAllowed) {
      setExternalAiConsentChecked(false);
      setShowExternalAiConsent(true);
      return;
    }
    void openGenerationReview();
  };

  const acceptExternalAiAndReview = async () => {
    if (
      !externalAiConsentChecked ||
      !onUpdateExternalAiConsent ||
      isExternalAiConsentSaving
    ) {
      return;
    }
    setReviewError(null);
    const saved = await onUpdateExternalAiConsent(true);
    if (!saved) {
      setReviewError(t("memory.aiConsent.saveFailed"));
      return;
    }
    await openGenerationReview();
  };

  const confirmReviewActivityDeletion = async () => {
    if (!reviewDeletionTarget || isReviewDeleting || !generationReview) {
      return;
    }
    const target = reviewDeletionTarget;
    const canDelete = target.kind === "prompt"
      ? Boolean(onDeletePromptActivity)
      : Boolean(onDeleteSessionActivity);
    if (!canDelete) {
      return;
    }

    const previousReview = generationReview;
    const keepPromptAfterDeletion = (prompt: MemoryGenerationReviewPrompt) =>
      target.kind === "prompt"
        ? prompt.event_id !== target.eventId
        : prompt.session_id !== target.sessionId;
    const remainingPrompts = generationReview.prompts.filter(
      keepPromptAfterDeletion,
    );

    setIsReviewDeleting(true);
    setReviewError(null);
    setGenerationReview({
      ...generationReview,
      prompt_count: remainingPrompts.length,
      prompts: remainingPrompts,
    });
    setReviewDeletionTarget(null);
    try {
      if (target.kind === "prompt") {
        await onDeletePromptActivity?.(target.eventId);
      } else {
        await onDeleteSessionActivity?.(target.sessionId);
        setExpandedReviewSessionIds((current) => {
          const next = new Set(current);
          next.delete(target.sessionId);
          return next;
        });
      }

      if (onLoadMemoryGenerationReview) {
        try {
          const refreshedReview = await onLoadMemoryGenerationReview();
          const refreshedPrompts = refreshedReview.prompts.filter(
            keepPromptAfterDeletion,
          );
          setGenerationReview({
            ...refreshedReview,
            prompt_count: refreshedPrompts.length,
            prompts: refreshedPrompts,
          });
        } catch {
          setReviewError(t("memory.review.loadFailed"));
        }
      }
    } catch (error) {
      setGenerationReview(previousReview);
      setReviewDeletionTarget(target);
      setReviewError(
        error instanceof Error ? error.message : t("activity.deleteFailed"),
      );
    } finally {
      setIsReviewDeleting(false);
    }
  };

  const toggleReviewSession = (sessionId: string) => {
    setExpandedReviewSessionIds((current) => {
      const next = new Set(current);
      if (next.has(sessionId)) {
        next.delete(sessionId);
      } else {
        next.add(sessionId);
      }
      return next;
    });
  };

  const renderReviewDeletionConfirmation = (
    target: MemoryReviewDeletionTarget,
  ) => (
    <div className="bh-memory-review-delete-confirm" role="alert">
      <div>
        <strong>
          {target.kind === "prompt"
            ? t("activity.deletePromptTitle")
            : t("activity.deleteSessionTitle")}
        </strong>
        <span>
          {target.kind === "prompt"
            ? t("activity.deletePromptDescription")
            : t("activity.deleteSessionDescription", { count: target.promptCount })}
        </span>
      </div>
      <div>
        <button
          disabled={isReviewDeleting}
          onClick={() => setReviewDeletionTarget(null)}
          type="button"
        >
          {t("common.cancel")}
        </button>
        <button
          className="is-danger"
          disabled={isReviewDeleting}
          onClick={() => void confirmReviewActivityDeletion()}
          type="button"
        >
          {isReviewDeleting ? t("activity.deleting") : t("activity.deleteConfirm")}
        </button>
      </div>
    </div>
  );

  const renderReviewPrompt = (
    prompt: MemoryGenerationReviewPrompt,
    showSession = false,
  ) => {
    const isConfirming =
      reviewDeletionTarget?.kind === "prompt" &&
      reviewDeletionTarget.eventId === prompt.event_id;
    const promptDescriptionId = `memory-review-prompt-${prompt.event_id}`;
    return (
      <article
        className="bh-memory-review-item"
        data-confirming-delete={isConfirming || undefined}
        key={prompt.event_id}
      >
        <header>
          <div className="bh-memory-review-item-heading">
            <strong>{t("activity.promptLabel", { sequence: prompt.sequence })}</strong>
            <div className="bh-memory-review-item-meta">
              <span>{prompt.tool}</span>
              {showSession ? (
                <span>{t("memory.review.session", { id: prompt.session_id.slice(0, 8) })}</span>
              ) : null}
              <time dateTime={prompt.created_at}>
                {new Date(prompt.created_at).toLocaleString(localeTag)}
              </time>
            </div>
          </div>
          {onDeletePromptActivity ? (
            <button
              aria-label={t("activity.deletePrompt")}
              className="bh-memory-review-delete-action"
              disabled={isReviewDeleting}
              onClick={() => {
                setReviewError(null);
                setReviewDeletionTarget({
                  eventId: prompt.event_id,
                  kind: "prompt",
                });
              }}
              type="button"
            >
              <Trash2 aria-hidden="true" size={14} strokeWidth={1.7} />
              <span>{t("activity.deletePrompt")}</span>
            </button>
          ) : null}
        </header>
        <div className="bh-memory-review-content" id={promptDescriptionId}>
          <span>
            {t("memory.review.promptPreview")}
            {prompt.prompt_truncated
              ? ` · ${t("memory.review.truncated")}`
              : ""}
          </span>
          <ExpandableReviewText
            text={prompt.text || t("memory.review.emptyPrompt")}
          />
          {prompt.response_preview ? (
            <div className="bh-memory-review-response">
              <span>
                {t("memory.review.responsePreview")}
                {prompt.response_truncated
                  ? ` · ${t("memory.review.truncated")}`
                  : ""}
              </span>
              <ExpandableReviewText text={prompt.response_preview} />
            </div>
          ) : null}
        </div>
        {isConfirming
          ? renderReviewDeletionConfirmation({
              eventId: prompt.event_id,
              kind: "prompt",
            })
          : null}
      </article>
    );
  };

  const approveForAgentUse = async () => {
    if (!onApproveProjectMemory || isApprovingForAgents) {
      return;
    }
    setIsApprovingForAgents(true);
    setApprovalError(null);
    try {
      await onApproveProjectMemory();
    } catch (error) {
      setApprovalError(
        error instanceof Error ? error.message : t("memory.approvalFailed"),
      );
    } finally {
      setIsApprovingForAgents(false);
    }
  };

  const selectMemoryView = (view: MemoryPanelView, focus = false) => {
    setActiveMemoryView(view);
    if (!focus) {
      return;
    }
    window.requestAnimationFrame(() => {
      memoryViewTabListRef.current
        ?.querySelector<HTMLButtonElement>(`[data-memory-view="${view}"]`)
        ?.focus();
    });
  };

  const handleMemoryViewKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      return;
    }
    event.preventDefault();
    const nextView = nextMemoryPanelView(
      activeMemoryView,
      event.key as "ArrowLeft" | "ArrowRight" | "End" | "Home",
    );
    selectMemoryView(nextView, true);
  };

  return (
    <section className="bh-memory-workspace" aria-label={t("project.memory")}>
      <header className="bh-memory-toolbar">
        <div>
          <h2>{t("memory.projectMemory")}</h2>
          <p>{t("memory.description")}</p>
        </div>
      </header>

      {onGenerateProjectMemory && generationStatus ? (
        <div className="bh-memory-status" role="status">
          {generationStatus}
        </div>
      ) : null}
      <div className="bh-memory-documentation">
        {onGenerateProjectMemory ? (
          <section
            className="bh-memory-document-section"
            aria-label={t("memory.generation")}
          >
            {hasPendingDocumentation || isGenerationActive ? (
              <article
                aria-busy={isGenerationActive || undefined}
                className="bh-memory-document-request"
                data-generating={isGenerationActive ? "true" : "false"}
              >
                <div className="bh-memory-document-row-main">
                  <h3>
                    {isGenerationActive
                      ? t("memory.creating")
                      : t("memory.ready")}
                  </h3>
                  {hasPendingDocumentation ? (
                    <>
                      <div className="bh-memory-request-metrics">
                        <span>
                          <strong>{pendingPromptCount.toLocaleString()}</strong>
                          {pendingPromptCount === 1 ? "prompt" : "prompts"}
                        </span>
                        <span>
                          <strong>{pendingSessionCount.toLocaleString()}</strong>
                          {pendingSessionCount === 1 ? "session" : "sessions"}
                        </span>
                      </div>
                      <p>{pendingDateRange ?? t("memory.readyDescription")}</p>
                    </>
                  ) : (
                    <p>{t("memory.creatingDescription")}</p>
                  )}
                </div>
                <div className="bh-memory-generation-action">
                  <button
                    aria-busy={isGenerationActive || undefined}
                    aria-disabled={
                      !hasPendingDocumentation ||
                      isGenerationActive ||
                      (visibleGenerationError !== null && !visibleGenerationRetryable)
                    }
                    className="bh-memory-primary-action"
                    disabled={
                      isGenerationActive ||
                      isReviewLoading ||
                      (visibleGenerationError !== null && !visibleGenerationRetryable)
                    }
                    onClick={startProjectMemoryFlow}
                    type="button"
                  >
                    <Sparkles aria-hidden="true" size={16} strokeWidth={1.7} />
                    <span>
                      {isGenerationActive
                        ? t("memory.updating")
                        : isReviewLoading
                          ? t("memory.review.loading")
                        : isProjectMemoryGenerationDelayed
                          ? t("memory.updateStatus")
                          : visibleGenerationError
                            ? visibleGenerationRetryable
                              ? t("memory.retryUpdate")
                              : t("memory.failed")
                            : t("memory.create")}
                    </span>
                  </button>
                  <span>{t("memory.createHint")}</span>
                </div>
                {isGenerationActive ? (
                  <div
                    aria-live="polite"
                    className="bh-memory-generation-status"
                    role="status"
                  >
                    <div className="bh-memory-generation-progress" aria-hidden="true" />
                    <div className="bh-memory-generation-status-copy">
                      <strong>{t("memory.creating")}</strong>
                      <span>{t("memory.creatingDescription")}</span>
                    </div>
                  </div>
                ) : visibleGenerationError ? (
                  <div
                    className="bh-memory-generation-status"
                    data-error="true"
                    role="alert"
                  >
                    <div>
                      <strong>{t("memory.failed")}</strong>
                      <span>{visibleGenerationError}</span>
                    </div>
                  </div>
                ) : null}
              </article>
            ) : (
              <div className="bh-memory-document-idle" role="status">
                <span>
                  {projectMemoryArtifact
                    ? t("memory.noWaiting")
                    : t("memory.noCurrent")}
                </span>
              </div>
            )}
          </section>
        ) : null}

        {showExternalAiConsent ? (
          <div className="bh-memory-review-overlay" role="presentation">
            <section
              aria-describedby="memory-ai-consent-description"
              aria-labelledby="memory-ai-consent-title"
              aria-modal="true"
              className="bh-memory-review-dialog bh-memory-consent-dialog"
              ref={generationDialogRef}
              role="dialog"
            >
              <header className="bh-memory-review-header">
                <div className="bh-memory-review-title-row">
                  <span className="bh-memory-review-icon" aria-hidden="true">
                    <ShieldCheck size={20} strokeWidth={1.7} />
                  </span>
                  <div>
                    <span className="bh-memory-review-eyebrow">
                      {t("memory.aiConsent.eyebrow")}
                    </span>
                    <h3 id="memory-ai-consent-title">
                      {t("memory.aiConsent.title")}
                    </h3>
                  </div>
                </div>
                <button
                  aria-label={t("common.close")}
                  className="bh-icon-button"
                  disabled={isExternalAiConsentSaving || isReviewLoading}
                  onClick={() => {
                    setShowExternalAiConsent(false);
                    setReviewError(null);
                  }}
                  type="button"
                >
                  <X size={16} />
                </button>
              </header>

              <div className="bh-memory-review-scroll">
                <p id="memory-ai-consent-description">
                  {t("memory.aiConsent.description", { providers: providerLabel })}
                </p>

                <div className="bh-memory-flow-status" aria-label={t("memory.flow.progress")}>
                  <span>{t("memory.flow.stepConsent")}</span>
                  <strong>{providerLabel}</strong>
                </div>

                <div className="bh-memory-consent-scope-grid">
                  <section className="bh-memory-consent-scope" data-kind="sent">
                    <header>
                      <ArrowRight aria-hidden="true" size={16} />
                      <strong>{t("memory.aiConsent.sentTitle")}</strong>
                    </header>
                    <div>
                      <MessageSquareText aria-hidden="true" size={18} />
                      <span>
                        <strong>{t("memory.aiConsent.activityTitle")}</strong>
                        {t("memory.aiConsent.activityDescription")}
                      </span>
                    </div>
                    <div>
                      <FileText aria-hidden="true" size={18} />
                      <span>
                        <strong>{t("memory.aiConsent.metadataTitle")}</strong>
                        {t("memory.aiConsent.metadataDescription")}
                      </span>
                    </div>
                  </section>
                  <section className="bh-memory-consent-scope" data-kind="safe">
                    <header>
                      <ShieldCheck aria-hidden="true" size={18} />
                      <strong>{t("memory.aiConsent.notSentTitle")}</strong>
                    </header>
                    <div>
                      <ShieldCheck aria-hidden="true" size={18} />
                      <span>
                        <strong>{t("memory.aiConsent.sourceTitle")}</strong>
                        {t("memory.aiConsent.sourceDescription")}
                      </span>
                    </div>
                  </section>
                </div>

                <label className="bh-memory-consent-option">
                  <input
                    checked={externalAiConsentChecked}
                    onChange={(event) => setExternalAiConsentChecked(event.target.checked)}
                    type="checkbox"
                  />
                  <span>
                    <span>
                      {t("memory.aiConsent.checkbox", { providers: providerLabel })}
                    </span>
                  </span>
                </label>

                {reviewError ? (
                  <p className="bh-memory-review-error" role="alert">
                    {reviewError}
                  </p>
                ) : null}
              </div>
              <footer className="bh-memory-review-actions">
                <button
                  disabled={isExternalAiConsentSaving || isReviewLoading}
                  onClick={() => {
                    setShowExternalAiConsent(false);
                    setReviewError(null);
                  }}
                  type="button"
                >
                  {t("common.cancel")}
                </button>
                <button
                  className="bh-memory-primary-action"
                  disabled={
                    !externalAiConsentChecked ||
                    !onUpdateExternalAiConsent ||
                    isExternalAiConsentSaving ||
                    isReviewLoading
                  }
                  onClick={() => void acceptExternalAiAndReview()}
                  type="button"
                >
                  {isExternalAiConsentSaving || isReviewLoading
                    ? t("memory.aiConsent.saving")
                    : t("memory.aiConsent.continue")}
                </button>
              </footer>
            </section>
          </div>
        ) : null}

        {generationReview ? (
          <div className="bh-memory-review-overlay" role="presentation">
            <section
              aria-describedby="memory-review-description"
              aria-labelledby="memory-review-title"
              aria-modal="true"
              className="bh-memory-review-dialog"
              ref={generationDialogRef}
              role="dialog"
            >
              <header className="bh-memory-review-header">
                <div className="bh-memory-review-title-row">
                  <span className="bh-memory-review-icon" aria-hidden="true">
                    <Sparkles size={20} strokeWidth={1.7} />
                  </span>
                  <div>
                    <span className="bh-memory-review-eyebrow">
                      {t("memory.review.eyebrow")}
                    </span>
                    <h3 id="memory-review-title">{t("memory.review.title")}</h3>
                  </div>
                </div>
                <button
                  aria-label={t("common.close")}
                  className="bh-icon-button"
                  disabled={isReviewDeleting}
                  onClick={() => {
                    setGenerationReview(null);
                    setReviewDeletionTarget(null);
                    setReviewError(null);
                  }}
                  type="button"
                >
                  <X size={16} />
                </button>
              </header>
              <div className="bh-memory-review-scroll">
                <p id="memory-review-description">
                  {t("memory.review.description", { providers: providerLabel })}
                </p>

                <div className="bh-memory-review-overview" data-columns="2">
                  <span>
                    <strong>
                      {generationReview.prompts.length.toLocaleString(localeTag)}
                    </strong>
                    {t("memory.review.prompts")}
                  </span>
                  <span>
                    <strong>{reviewSessionGroups.length.toLocaleString(localeTag)}</strong>
                    {t("memory.review.sessions")}
                  </span>
                </div>

                <div className="bh-memory-review-facts" aria-label={t("memory.review.scope")}>
                  <span>{generationReview.response_count.toLocaleString(localeTag)} {t("memory.review.responses")}</span>
                  <span>{generationReview.changed_file_count.toLocaleString(localeTag)} {t("memory.review.filePaths")}</span>
                  <span>{generationReview.commit_count.toLocaleString(localeTag)} {t("memory.review.commits")}</span>
                  <span data-safe="true"><ShieldCheck aria-hidden="true" size={14} />{t("memory.review.sourceCodeExcluded")}</span>
                </div>

                <p className="bh-memory-review-context-note">
                  <FileText aria-hidden="true" size={15} />
                  <span>{t("memory.review.additionalContext")}</span>
                </p>

                <section
                  aria-label={t("memory.review.activityTitle")}
                  className="bh-memory-review-browser"
                >
                  <header className="bh-memory-review-browser-header">
                    <div>
                      <strong>{t("memory.review.activityTitle")}</strong>
                      <span>{t("memory.review.activityHint")}</span>
                    </div>
                    <div
                      aria-label={t("memory.review.browseMode")}
                      className="bh-memory-review-browse-tabs"
                      role="tablist"
                    >
                      <button
                        aria-selected={reviewBrowseMode === "prompts"}
                        data-active={reviewBrowseMode === "prompts" || undefined}
                        onClick={() => setReviewBrowseMode("prompts")}
                        role="tab"
                        type="button"
                      >
                        {t("activity.byPrompt")}
                        <span>{generationReview.prompts.length.toLocaleString(localeTag)}</span>
                      </button>
                      <button
                        aria-selected={reviewBrowseMode === "sessions"}
                        data-active={reviewBrowseMode === "sessions" || undefined}
                        onClick={() => setReviewBrowseMode("sessions")}
                        role="tab"
                        type="button"
                      >
                        {t("activity.bySession")}
                        <span>{reviewSessionGroups.length.toLocaleString(localeTag)}</span>
                      </button>
                    </div>
                  </header>

                  <label className="bh-memory-review-search">
                    <Search aria-hidden="true" size={15} strokeWidth={1.7} />
                    <input
                      aria-label={t("memory.review.search")}
                      onChange={(event) => setReviewSearch(event.target.value)}
                      placeholder={t("memory.review.search")}
                      type="search"
                      value={reviewSearch}
                    />
                  </label>

                  <div className="bh-memory-review-list">
                    {reviewBrowseMode === "prompts" ? (
                      visibleReviewPrompts.length ? (
                        visibleReviewPrompts.map((prompt) =>
                          renderReviewPrompt(prompt, true),
                        )
                      ) : (
                        <p className="bh-memory-review-empty">
                          {generationReview.prompts.length
                            ? t("memory.review.noMatches")
                            : t("memory.review.noPrompts")}
                        </p>
                      )
                    ) : visibleReviewSessionGroups.length ? (
                      visibleReviewSessionGroups.map(({ prompts, sessionId }) => {
                        const sessionPromptCount =
                          reviewSessionGroups.find(
                            (group) => group.sessionId === sessionId,
                          )?.prompts.length ?? prompts.length;
                        const isConfirmingSession =
                          reviewDeletionTarget?.kind === "session" &&
                          reviewDeletionTarget.sessionId === sessionId;
                        const isSessionExpanded =
                          expandedReviewSessionIds.has(sessionId);
                        const sessionPromptListId =
                          `memory-review-session-${sessionId}`;
                        return (
                          <section
                            className="bh-memory-review-section"
                            data-confirming-delete={isConfirmingSession || undefined}
                            data-expanded={isSessionExpanded || undefined}
                            key={sessionId}
                          >
                            <header>
                              <button
                                aria-controls={sessionPromptListId}
                                aria-expanded={isSessionExpanded}
                                aria-label={t(
                                  isSessionExpanded
                                    ? "memory.review.collapseSession"
                                    : "memory.review.expandSession",
                                  { id: sessionId.slice(0, 8) },
                                )}
                                className="bh-memory-review-session-toggle"
                                data-expanded={isSessionExpanded || undefined}
                                onClick={() => toggleReviewSession(sessionId)}
                                type="button"
                              >
                                <ChevronRight aria-hidden="true" size={16} strokeWidth={1.7} />
                                <span className="bh-memory-review-session-heading">
                                  <strong>
                                    {t("memory.review.session", { id: sessionId.slice(0, 8) })}
                                  </strong>
                                  <span>
                                    {t("memory.review.sessionPromptCount", {
                                      count: sessionPromptCount.toLocaleString(localeTag),
                                    })}
                                  </span>
                                </span>
                              </button>
                              {onDeleteSessionActivity ? (
                                <button
                                  aria-label={t("activity.deleteSession")}
                                  className="bh-memory-review-delete-action"
                                  disabled={isReviewDeleting}
                                  onClick={() => {
                                    setReviewError(null);
                                    setReviewDeletionTarget({
                                      kind: "session",
                                      promptCount: sessionPromptCount,
                                      sessionId,
                                    });
                                  }}
                                  type="button"
                                >
                                  <Trash2 aria-hidden="true" size={14} strokeWidth={1.7} />
                                  <span>{t("activity.deleteSession")}</span>
                                </button>
                              ) : null}
                            </header>
                            {isConfirmingSession
                              ? renderReviewDeletionConfirmation({
                                  kind: "session",
                                  promptCount: sessionPromptCount,
                                  sessionId,
                                })
                              : null}
                            {isSessionExpanded ? (
                              <div
                                className="bh-memory-review-session-prompts"
                                id={sessionPromptListId}
                              >
                                {prompts.map((prompt) => renderReviewPrompt(prompt))}
                              </div>
                            ) : null}
                          </section>
                        );
                      })
                    ) : (
                      <p className="bh-memory-review-empty">
                        {generationReview.prompts.length
                          ? t("memory.review.noMatches")
                          : t("memory.review.noPrompts")}
                      </p>
                    )}
                  </div>
                </section>
                {reviewError ? (
                  <p className="bh-memory-review-error" role="alert">
                    {reviewError}
                  </p>
                ) : null}
              </div>
              <footer className="bh-memory-review-actions">
                <span>{t("memory.review.readySummary", {
                  prompts: generationReview.prompts.length.toLocaleString(localeTag),
                  sessions: reviewSessionGroups.length.toLocaleString(localeTag),
                })}</span>
                <div>
                  <button
                    disabled={isReviewDeleting}
                    onClick={() => {
                      setGenerationReview(null);
                      setReviewDeletionTarget(null);
                      setReviewError(null);
                    }}
                    type="button"
                  >
                    {t("common.cancel")}
                  </button>
                  <button
                    className="bh-memory-primary-action"
                    disabled={isGenerating || isReviewDeleting}
                    onClick={() => void createProjectMemory()}
                    type="button"
                  >
                    {isGenerating ? t("memory.creating") : t("memory.review.generate")}
                  </button>
                </div>
              </footer>
            </section>
          </div>
        ) : null}

        <div
          aria-label={t("memory.views")}
          className="bh-memory-view-tabs"
          onKeyDown={handleMemoryViewKeyDown}
          ref={memoryViewTabListRef}
          role="tablist"
        >
          <button
            aria-controls="memory-view-panel-history"
            aria-selected={activeMemoryView === "history"}
            data-active={activeMemoryView === "history" || undefined}
            data-memory-view="history"
            id="memory-view-tab-history"
            onClick={() => selectMemoryView("history")}
            role="tab"
            tabIndex={activeMemoryView === "history" ? 0 : -1}
            type="button"
          >
            <span>{t("memory.history")}</span>
            <small>{totalGeneratedArtifactCount}</small>
          </button>
          <button
            aria-controls="memory-view-panel-current"
            aria-selected={activeMemoryView === "current"}
            data-active={activeMemoryView === "current" || undefined}
            data-memory-view="current"
            id="memory-view-tab-current"
            onClick={() => selectMemoryView("current")}
            role="tab"
            tabIndex={activeMemoryView === "current" ? 0 : -1}
            type="button"
          >
            <span>{t("memory.currentTab")}</span>
          </button>
          <button
            aria-controls="memory-view-panel-graph"
            aria-selected={activeMemoryView === "graph"}
            data-active={activeMemoryView === "graph" || undefined}
            data-memory-view="graph"
            id="memory-view-tab-graph"
            onClick={() => selectMemoryView("graph")}
            role="tab"
            tabIndex={activeMemoryView === "graph" ? 0 : -1}
            type="button"
          >
            <Network aria-hidden="true" size={14} strokeWidth={1.7} />
            <span>{t("memory.graph")}</span>
          </button>
        </div>

        {activeMemoryView === "current" ? (
          <div
            aria-labelledby="memory-view-tab-current"
            className="bh-memory-view-panel"
            id="memory-view-panel-current"
            role="tabpanel"
            tabIndex={0}
          >
            {projectMemoryArtifact ? (
              <section
                className="bh-memory-document-section"
                aria-labelledby="project-memory-document-title"
              >
                <div className="bh-memory-section-header">
                  <div>
                    <span className="bh-memory-step-kicker">
                      {t("memory.currentDocument")}
                    </span>
                    <h3 id="project-memory-document-title">
                      {t("memory.currentProjectMemory")}
                    </h3>
                    <p>
                      {projectMemoryArtifact.updatedAt
                        ? `Updated ${projectMemoryArtifact.updatedAt}`
                        : t("memory.compiledDocument")}
                    </p>
                  </div>
                  <div className="bh-memory-agent-approval">
                    <span>
                      {projectMemoryArtifact.sourceSessionIds.length > 0
                        ? `${projectMemoryArtifact.sourceSessionIds.length} source ${
                            projectMemoryArtifact.sourceSessionIds.length === 1
                              ? "session"
                              : "sessions"
                          }`
                        : t("memory.compiled")}
                    </span>
                    {projectMemoryNeedsApproval && onApproveProjectMemory ? (
                      <button
                        disabled={isApprovingForAgents}
                        onClick={() => void approveForAgentUse()}
                        type="button"
                      >
                        <CheckCircle2 aria-hidden="true" size={15} />
                        {isApprovingForAgents
                          ? t("memory.approvingForAgents")
                          : t("memory.approveForAgents")}
                      </button>
                    ) : projectMemoryIsApproved ? (
                      <small>{t("memory.approvedForAgents")}</small>
                    ) : null}
                  </div>
                </div>
                {approvalError ? <p role="alert">{approvalError}</p> : null}
                <MarkdownContent
                  className="bh-markdown-preview bh-memory-project-document"
                  emptyLabel={t("memory.noCompiled")}
                  value={
                    projectMemoryArtifact.outcome ?? projectMemoryArtifact.summary ?? ""
                  }
                />
              </section>
            ) : (
              <div className="bh-memory-view-empty" role="status">
                <span className="bh-memory-step-kicker">
                  {t("memory.currentDocument")}
                </span>
                <h3>{t("memory.noCurrent")}</h3>
                <p>{t("memory.currentEmptyDescription")}</p>
              </div>
            )}
          </div>
        ) : activeMemoryView === "graph" ? (
          <div
            aria-labelledby="memory-view-tab-graph"
            className="bh-memory-view-panel"
            id="memory-view-panel-graph"
            role="tabpanel"
            tabIndex={0}
          >
            <ContextGraphPanel
              onOpenSession={onOpenSession}
              projectId={data.project.id}
            />
          </div>
        ) : (
          <div
            aria-labelledby="memory-view-tab-history"
            className="bh-memory-view-panel"
            id="memory-view-panel-history"
            role="tabpanel"
            tabIndex={0}
          >
            {generatedArtifacts.length > 0 ? (
              <section
                className="bh-memory-document-section"
                aria-labelledby="memory-history-title"
              >
                <div className="bh-memory-section-header">
                  <div>
                    <span className="bh-memory-step-kicker">
                      {t("memory.longTerm")}
                    </span>
                    <h3 id="memory-history-title">{t("memory.history")}</h3>
                    <p>{resultDateRange}</p>
                  </div>
                  <span>{generatedSummaryCountLabel}</span>
                </div>

                <div className="bh-memory-context-list">
                  {generatedArtifacts.map((artifact) => (
                    <MemoryArtifactCard
                      artifact={artifact}
                      key={artifact.id}
                      isSelected={artifact.id === selectedArtifactId}
                      onSelect={(selectedArtifact) =>
                        setSelectedArtifactId(selectedArtifact.id)
                      }
                    />
                  ))}
                  {canLoadMoreArtifacts ? (
                    <button
                      className="bh-memory-load-more"
                      disabled={isArtifactHistoryLoading}
                      onClick={() => void loadMoreArtifacts()}
                      type="button"
                    >
                      {isArtifactHistoryLoading
                        ? t("activity.loading")
                        : t("memory.loadMore")}
                    </button>
                  ) : null}
                  {artifactHistoryError ? (
                    <div
                      className="bh-memory-generation-status"
                      data-error="true"
                      role="alert"
                    >
                      <div>
                        <strong>{t("memory.historyLoadFailedTitle")}</strong>
                        <span>{artifactHistoryError}</span>
                      </div>
                    </div>
                  ) : null}
                </div>
              </section>
            ) : (
              <div className="bh-memory-view-empty" role="status">
                <span className="bh-memory-step-kicker">{t("memory.longTerm")}</span>
                <h3>{t("memory.historyEmpty")}</h3>
                <p>{t("memory.historyEmptyDescription")}</p>
              </div>
            )}
          </div>
        )}
      </div>
      {selectedArtifact ? (
        <MemoryArtifactDetailDrawer
          artifact={selectedArtifact}
          artifactCount={generatedArtifacts.length}
          artifactPosition={selectedArtifactIndex + 1}
          onClose={() => setSelectedArtifactId(null)}
          onNext={
            selectedArtifactIndex < generatedArtifacts.length - 1
              ? () => setSelectedArtifactId(generatedArtifacts[selectedArtifactIndex + 1].id)
              : undefined
          }
          onOpenSession={(sessionId) => {
            setSelectedArtifactId(null);
            onOpenSession?.(sessionId);
          }}
          onPrevious={
            selectedArtifactIndex > 0
              ? () => setSelectedArtifactId(generatedArtifacts[selectedArtifactIndex - 1].id)
              : undefined
          }
        />
      ) : null}
    </section>
  );
}
