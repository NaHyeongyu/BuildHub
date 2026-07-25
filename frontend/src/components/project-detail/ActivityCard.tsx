import { useMemo, useState } from "react";
import { Share2, Trash2 } from "lucide-react";
import { AiModelBadge } from "./AiModelBadge";
import type {
  ActivityItem,
  PromptActivityItem,
  PromptWorkGroup,
} from "./types";
import { useI18n } from "../../i18n/I18nProvider";

const PROMPT_PREVIEW_LINES = 10;

function PromptText({
  expandable = true,
  text,
}: {
  expandable?: boolean;
  text: string;
}) {
  const { t } = useI18n();
  const [isExpanded, setIsExpanded] = useState(false);
  const { isLong, preview } = useMemo(() => {
    const lines = text.split(/\r?\n/);
    return {
      isLong: lines.length > PROMPT_PREVIEW_LINES,
      preview:
        lines.length > PROMPT_PREVIEW_LINES
          ? `${lines.slice(0, PROMPT_PREVIEW_LINES).join("\n")}\n...`
          : text,
    };
  }, [text]);

  return (
    <div className="bh-prompt-text">
      <p>{isExpanded || !isLong ? text : preview}</p>
      {expandable && isLong ? (
        <button
          className="bh-prompt-more-button"
          onClick={(event) => {
            event.stopPropagation();
            setIsExpanded((expanded) => !expanded);
          }}
          onKeyDown={(event) => {
            event.stopPropagation();
          }}
          type="button"
        >
          {isExpanded ? t("activity.showLess") : t("activity.showMore")}
        </button>
      ) : null}
    </div>
  );
}

function promptTruncatedLabel(activity: PromptActivityItem) {
  if (!activity.promptTruncated) {
    return null;
  }
  if (activity.promptStorageLimit) {
    return `truncated at ${activity.promptStorageLimit.toLocaleString()} chars`;
  }
  if (activity.promptOriginalLength) {
    return `${activity.promptOriginalLength.toLocaleString()} chars truncated`;
  }
  return "truncated";
}

function responseTruncatedLabel(activity: PromptActivityItem) {
  if (!activity.responseTruncated) {
    return null;
  }
  if (activity.responseStorageLimit) {
    return `response truncated at ${activity.responseStorageLimit.toLocaleString()} chars`;
  }
  if (activity.responseOriginalLength) {
    return `${activity.responseOriginalLength.toLocaleString()} response chars truncated`;
  }
  return "response truncated";
}

type ActivityCardProps = {
  activity: ActivityItem;
  isSelected?: boolean;
  onOpen?: () => void;
};

export function ActivityCard({
  activity,
  isSelected = false,
  onOpen,
}: ActivityCardProps) {
  const { t } = useI18n();
  const displayLabel = activity.label ?? activity.model;
  return (
    <article
      className="bh-session-row"
      data-active={isSelected}
      aria-pressed={isSelected}
      aria-label={`${displayLabel} session`}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen?.();
        }
      }}
      role="button"
      tabIndex={0}
    >
      <div className="bh-session-row-main">
        <div className="bh-session-row-header">
          <strong>{displayLabel}</strong>
          <span>{t("activity.promptsCount", { count: activity.prompts })}</span>
        </div>
        <span>{activity.label ? activity.model : t("activity.session", { id: activity.id.slice(0, 8) })}</span>
        <span>
          {t("activity.promptsCount", { count: activity.prompts })} · {t("activity.fileCount", { count: activity.filesChanged })} ·{" "}
          {activity.lastActivity}
        </span>
      </div>
    </article>
  );
}

type PromptActivityCardProps = {
  activity: PromptActivityItem;
  isSelected: boolean;
  onOpen: () => void;
  promptLabel?: string;
};

export function PromptActivityCard({
  activity,
  isSelected,
  onOpen,
  promptLabel,
}: PromptActivityCardProps) {
  const { t } = useI18n();
  const truncatedLabel = promptTruncatedLabel(activity);
  const responseLimitLabel = responseTruncatedLabel(activity);

  return (
    <article
      className="bh-prompt-row"
      data-active={isSelected}
      aria-label={`Select prompt submitted at ${activity.submittedAt}`}
      aria-pressed={isSelected}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      role="button"
      tabIndex={0}
    >
      <div className="bh-prompt-row-main">
        <PromptText expandable={false} text={activity.prompt} />
        <div className="bh-prompt-row-meta" aria-label={t("activity.prompt")}>
          <AiModelBadge className="is-compact" model={activity.model} />
          <span className="bh-prompt-row-chip">{activity.submittedAt}</span>
          {promptLabel ? (
            <span className="bh-prompt-row-chip">{promptLabel}</span>
          ) : null}
          <span className="bh-prompt-row-chip">{t("activity.fileCount", { count: activity.filesChanged })}</span>
          {truncatedLabel ? (
            <span className="bh-prompt-row-chip">{truncatedLabel}</span>
          ) : null}
          {responseLimitLabel ? (
            <span className="bh-prompt-row-chip">{responseLimitLabel}</span>
          ) : null}
        </div>
      </div>
    </article>
  );
}

function promptWorkStatus(group: PromptWorkGroup) {
  const latestPrompt = group.prompts[group.prompts.length - 1];
  if (latestPrompt?.response || latestPrompt?.responseReceivedAt) {
    return "completed";
  }
  return "inProgress";
}

export function PromptWorkGroupCard({
  group,
  isSelected,
  onOpen,
}: {
  group: PromptWorkGroup;
  isSelected: boolean;
  onOpen: () => void;
}) {
  const { t } = useI18n();
  const originalPrompt = group.prompts[0];
  const latestPrompt = group.prompts[group.prompts.length - 1];
  const filesChanged = group.prompts.reduce(
    (total, prompt) => total + prompt.filesChanged,
    0,
  );
  const status = promptWorkStatus(group);
  const statusLabel =
    status === "completed"
      ? t("activity.workStatus.completed")
      : t("activity.workStatus.inProgress");

  return (
    <article
      aria-label={originalPrompt.prompt}
      aria-pressed={isSelected}
      className="bh-prompt-work-row"
      data-active={isSelected}
      data-status={status}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      role="button"
      tabIndex={0}
    >
      <div className="bh-prompt-work-row-header">
        <span className="bh-prompt-work-status">{statusLabel}</span>
        <time>{latestPrompt.submittedAt}</time>
      </div>
      <PromptText expandable={false} text={originalPrompt.prompt} />
      <div className="bh-prompt-row-meta">
        <AiModelBadge className="is-compact" model={latestPrompt.model} />
        <span className="bh-prompt-row-chip">
          {t("activity.promptsCount", { count: group.prompts.length })}
        </span>
        <span className="bh-prompt-row-chip">
          {t("activity.fileCount", { count: filesChanged })}
        </span>
      </div>
    </article>
  );
}

export function PromptWorkTimeline({
  group,
  onDeletePrompt,
  onSelectPrompt,
  onSharePrompt,
  selectedActivity,
}: {
  group: PromptWorkGroup | null;
  onDeletePrompt?: (activity: PromptActivityItem) => void;
  onSelectPrompt: (activity: PromptActivityItem) => void;
  onSharePrompt?: (activity: PromptActivityItem) => void;
  selectedActivity: PromptActivityItem | null;
}) {
  const { t } = useI18n();
  if (!group || !selectedActivity) {
    return (
      <PromptChangeDetail
        activity={selectedActivity}
        onDeletePrompt={onDeletePrompt}
        onSharePrompt={onSharePrompt}
      />
    );
  }

  return (
    <div className="bh-prompt-work-detail">
      <section className="bh-prompt-work-timeline" aria-labelledby="prompt-work-title">
        <div className="bh-prompt-work-title">
          <div>
            <span>{t("activity.workTimeline")}</span>
            <h2 id="prompt-work-title">{group.prompts[0].prompt}</h2>
          </div>
          <strong>{t("activity.promptsCount", { count: group.prompts.length })}</strong>
        </div>
        <ol>
          {group.prompts.map((prompt, index) => {
            const hasLaterPrompt = index < group.prompts.length - 1;
            const hasResponse = Boolean(prompt.response || prompt.responseReceivedAt);
            const status = hasResponse
              ? "completed"
              : hasLaterPrompt
                ? "interrupted"
                : "inProgress";
            const stepLabel =
              index === 0
                ? t("activity.originalRequest")
                : prompt.submissionContext === "during_output"
                  ? t("activity.savedDuringOutput")
                  : t("activity.followUpRequest");
            const statusLabel =
              status === "completed"
                ? t("activity.workStatus.completed")
                : status === "interrupted"
                  ? t("activity.workStatus.interrupted")
                  : t("activity.workStatus.inProgress");

            return (
              <li data-status={status} key={prompt.id}>
                <span className="bh-prompt-work-marker" aria-hidden="true" />
                <button
                  aria-pressed={selectedActivity.id === prompt.id}
                  className="bh-prompt-work-step"
                  data-active={selectedActivity.id === prompt.id}
                  onClick={() => onSelectPrompt(prompt)}
                  type="button"
                >
                  <div className="bh-prompt-work-step-header">
                    <strong>{stepLabel}</strong>
                    <span>{statusLabel}</span>
                  </div>
                  <p>{prompt.prompt}</p>
                  <div className="bh-prompt-work-step-meta">
                    <time>{prompt.submittedAt}</time>
                    {hasResponse ? <span>{t("activity.responseSaved")}</span> : null}
                    {prompt.filesChanged > 0 ? (
                      <span>{t("activity.fileCount", { count: prompt.filesChanged })}</span>
                    ) : null}
                  </div>
                </button>
              </li>
            );
          })}
        </ol>
      </section>
      <PromptChangeDetail
        activity={selectedActivity}
        onDeletePrompt={onDeletePrompt}
        onSharePrompt={onSharePrompt}
      />
    </div>
  );
}

type PromptChangeDetailProps = {
  activity: PromptActivityItem | null;
  onDeletePrompt?: (activity: PromptActivityItem) => void;
  onSharePrompt?: (activity: PromptActivityItem) => void;
};

function diffLineKind(line: string) {
  if (line.startsWith("@@")) {
    return "hunk";
  }
  if (line.startsWith("+++") || line.startsWith("---")) {
    return "meta";
  }
  if (line.startsWith("+")) {
    return "addition";
  }
  if (line.startsWith("-")) {
    return "deletion";
  }
  return "context";
}

function patchOmittedLabel(reason: string | null | undefined) {
  if (!reason) {
    return "Diff patch is not available for this file.";
  }
  const labels: Record<string, string> = {
    binary: "Binary file diff is not shown.",
    content_unavailable: "File content was not available when this prompt was captured.",
    empty_patch: "No line-level patch was produced for this file.",
    excluded_path: "This path is excluded from patch capture.",
    sensitive_path: "This path may contain secrets, so patch content was not stored.",
  };
  return labels[reason] ?? `Diff patch omitted: ${reason}`;
}

function DiffViewer({ patch }: { patch: string }) {
  const lines = patch.split("\n");

  return (
    <div className="bh-diff-viewer" aria-label="Unified diff">
      {lines.map((line, index) => (
        <code
          className="bh-diff-line"
          data-kind={diffLineKind(line)}
          key={`${index}-${line}`}
        >
          <span>{line.slice(0, 1) || " "}</span>
          <span>{line.slice(1) || " "}</span>
        </code>
      ))}
    </div>
  );
}

export function PromptChangeDetail({
  activity,
  onDeletePrompt,
  onSharePrompt,
}: PromptChangeDetailProps) {
  const { t } = useI18n();
  if (!activity) {
    return (
      <section
        className="bh-activity-detail-placeholder"
        id="activity-detail-placeholder"
        aria-labelledby="activity-detail-placeholder-title"
      >
        <div>
          <h2 id="activity-detail-placeholder-title">{t("activity.promptDetail")}</h2>
          <p>{t("activity.openPrompt")}</p>
        </div>
      </section>
    );
  }

  return (
    <section
      className="bh-prompt-change-detail"
      id="activity-detail-placeholder"
      aria-labelledby="activity-detail-placeholder-title"
    >
      <div className="bh-prompt-change-header">
        <div>
          <span>{t("activity.selectedPrompt")}</span>
          <h2 id="activity-detail-placeholder-title">{t("activity.promptDetail")}</h2>
        </div>
        <div className="bh-prompt-change-header-actions">
          {onDeletePrompt ? (
            <button
              className="bh-header-action-button is-danger-quiet"
              onClick={() => onDeletePrompt(activity)}
              type="button"
            >
              <Trash2 aria-hidden="true" size={15} strokeWidth={1.6} />
              <span>{t("activity.deletePrompt")}</span>
            </button>
          ) : null}
          {onSharePrompt ? (
            <button
              className="bh-header-action-button is-primary"
              onClick={() => onSharePrompt(activity)}
              type="button"
            >
              <Share2 aria-hidden="true" size={15} strokeWidth={1.5} />
              <span>{t("community.prepareDraft")}</span>
            </button>
          ) : null}
          <strong>{t("activity.fileCount", { count: activity.filesChanged })}</strong>
        </div>
      </div>

      <div className="bh-prompt-change-summary">
        <span className="bh-prompt-detail-section-label">{t("activity.prompt")}</span>
        <PromptText text={activity.prompt} />
        {activity.promptTruncated ? (
          <div className="bh-prompt-storage-note">
            Prompt text was truncated for storage policy.
          </div>
        ) : null}
      </div>

      {activity.response ? (
        <div className="bh-ai-response-summary">
          <div className="bh-ai-response-label">
            <span>{t("activity.aiResponse")}</span>
            {activity.responseReceivedAt ? (
              <strong>{activity.responseReceivedAt}</strong>
            ) : null}
          </div>
          <PromptText text={activity.response} />
          {activity.responseTruncated ? (
            <div className="bh-prompt-storage-note">
              AI response text was truncated for storage policy.
            </div>
          ) : null}
        </div>
      ) : null}

      {activity.fileChanges.length > 0 ? (
        <div className="bh-prompt-change-list" aria-label="Prompt file changes">
          <div className="bh-prompt-detail-section-header">
            <span>{t("activity.fileChanges")}</span>
            <strong>{activity.fileChanges.length}</strong>
          </div>
          {activity.fileChanges.map((change) => (
            <article className="bh-diff-file" key={`${activity.id}-${change.path}`}>
              <div className="bh-diff-file-header">
                <div>
                  <span>{change.status}</span>
                  <strong>{change.path}</strong>
                  {change.oldPath ? <em>renamed from {change.oldPath}</em> : null}
                </div>
                <div className="bh-prompt-change-delta">
                  {change.additions !== null ? (
                    <span className="is-addition">+{change.additions}</span>
                  ) : null}
                  {change.deletions !== null ? (
                    <span className="is-deletion">-{change.deletions}</span>
                  ) : null}
                </div>
              </div>

              {change.patch ? (
                <>
                  <DiffViewer patch={change.patch} />
                  {change.patchTruncated ? (
                    <div className="bh-diff-note">Patch truncated for storage limits.</div>
                  ) : null}
                </>
              ) : (
                <div className="bh-diff-note">
                  {change.patchOmittedReason === "public_redacted"
                    ? t("activity.diffPublicRedacted")
                    : patchOmittedLabel(
                        change.binary ? "binary" : change.patchOmittedReason,
                      )}
                </div>
              )}
            </article>
          ))}
        </div>
      ) : (
        <div className="bh-prompt-change-empty">
          {t("activity.noPromptFiles")}
        </div>
      )}
    </section>
  );
}

type SessionDetailProps = {
  activity: ActivityItem | null;
  prompts: PromptActivityItem[];
};

export function SessionDetail({ activity, prompts }: SessionDetailProps) {
  const { t } = useI18n();
  if (!activity) {
    return (
      <section
        className="bh-activity-detail-placeholder"
        id="activity-detail-placeholder"
        aria-labelledby="activity-detail-placeholder-title"
      >
        <div>
          <h2 id="activity-detail-placeholder-title">{t("activity.sessionDetail")}</h2>
          <p>{t("activity.openSession")}</p>
        </div>
      </section>
    );
  }

  return (
    <section
      className="bh-session-detail"
      id="activity-detail-placeholder"
      aria-labelledby="activity-detail-placeholder-title"
    >
      <div className="bh-prompt-change-header">
        <div>
          <span>{t("activity.selectedSession")}</span>
          <h2 id="activity-detail-placeholder-title">{activity.model}</h2>
        </div>
        <strong>{t("activity.events", { count: activity.events })}</strong>
      </div>

      <dl className="bh-session-detail-meta">
        <div>
          <dt>{t("activity.started")}</dt>
          <dd>{activity.startedAt}</dd>
        </div>
        <div>
          <dt>{t("activity.lastActivity")}</dt>
          <dd>{activity.lastActivity}</dd>
        </div>
      </dl>

      <dl className="bh-session-detail-stats">
        <div>
          <dt>{t("project.prompts")}</dt>
          <dd>{activity.prompts}</dd>
        </div>
        <div>
          <dt>{t("activity.responses")}</dt>
          <dd>{activity.responses}</dd>
        </div>
        <div>
          <dt>{t("project.files")}</dt>
          <dd>{activity.filesChanged}</dd>
        </div>
      </dl>

      <div className="bh-session-conversations">
        <div className="bh-session-conversations-header">
          <span>{t("activity.conversations")}</span>
          <strong>{prompts.length}</strong>
        </div>

        {prompts.length > 0 ? (
          <div className="bh-session-conversation-list">
            {prompts.map((prompt) => (
              <article className="bh-session-conversation-row" key={prompt.id}>
                <div>
                  <div className="bh-session-conversation-meta">
                    <span>{t("activity.promptLabel", { sequence: prompt.sequence })}</span>
                    <time>{prompt.submittedAt}</time>
                  </div>
                  <PromptText text={prompt.prompt} />
                  {prompt.response ? (
                    <div className="bh-session-response-preview">
                      <span>{t("activity.aiResponse")}</span>
                      <PromptText text={prompt.response} />
                    </div>
                  ) : null}
                </div>
                <strong>{t("activity.fileCount", { count: prompt.filesChanged })}</strong>
              </article>
            ))}
          </div>
        ) : (
          <div className="bh-prompt-change-empty">
            {t("activity.noSessionPrompts")}
          </div>
        )}
      </div>
    </section>
  );
}
