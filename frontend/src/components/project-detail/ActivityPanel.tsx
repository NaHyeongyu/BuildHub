import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Activity,
  LoaderCircle,
  Search,
  Share2,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { fetchProjectPromptActivities } from "../../api/projects";
import { promptActivityPageFromApi } from "../../workspace/projectDetailMappers";
import {
  ActivityCard,
  PromptActivityCard,
  PromptChangeDetail,
  PromptWorkGroupCard,
  PromptWorkTimeline,
} from "./ActivityCard";
import {
  buildPromptWorkGroups,
  workTypeCounts,
  workTypeForFiles,
  type WorkTypeFilter,
} from "./activityHelpers";
import { EmptyState } from "./EmptyState";
import type {
  ActivityNavigationState,
  ActivityItem,
  ProjectDetailData,
  PromptActivityItem,
  PromptWorkGroup,
} from "./types";
import { WorkTypeFilterControl } from "./WorkTypeFilterControl";
import { useI18n } from "../../i18n/I18nProvider";
import { focusableModalElements } from "./modalFocus";

const PROMPT_ACTIVITY_PAGE_LIMIT = 50;

function restoreActiveActivityViewFocus() {
  const focusIfLost = () => {
    if (document.activeElement !== document.body) {
      return;
    }
    document
      .querySelector<HTMLElement>(
        '.bh-activity-view-tabs [aria-pressed="true"]',
      )
      ?.focus();
  };
  const observer = new MutationObserver(focusIfLost);
  observer.observe(document.body, { childList: true, subtree: true });
  window.requestAnimationFrame(focusIfLost);
  window.setTimeout(() => {
    focusIfLost();
    observer.disconnect();
  }, 1_000);
}

const defaultActivityNavigation: ActivityNavigationState = {
  selectedPromptId: null,
  selectedSessionId: null,
  selectedSessionPromptId: null,
  view: "prompts",
};

type ActivityFeedItem =
  | {
      activity: PromptActivityItem;
      kind: "prompt";
      key: string;
      sequenceIndex: number;
      timestamp: number | null;
    }
  | {
      activity: ActivityItem;
      kind: "session";
      key: string;
      sequenceIndex: number;
      timestamp: number | null;
    };

type ActivityDeletionTarget =
  | { activity: PromptActivityItem; kind: "prompt" }
  | { activity: ActivityItem; kind: "session" };

function displayTimeValue(value: string) {
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? null : timestamp;
}

function activityFeedSearchText(item: ActivityFeedItem) {
  if (item.kind === "prompt") {
    return [
      item.activity.prompt,
      item.activity.model,
      item.activity.submittedAt,
      `prompt ${item.activity.sequence}`,
      String(item.activity.sequence),
    ].join(" ");
  }

  return [
    item.activity.id,
    item.activity.model,
    item.activity.startedAt,
    item.activity.lastActivity,
    `${item.activity.prompts} prompts`,
    `${item.activity.filesChanged} files`,
  ].join(" ");
}

function sortActivityFeedItems(
  first: ActivityFeedItem,
  second: ActivityFeedItem,
) {
  if (
    first.timestamp !== null &&
    second.timestamp !== null &&
    first.timestamp !== second.timestamp
  ) {
    return second.timestamp - first.timestamp;
  }

  return first.sequenceIndex - second.sequenceIndex;
}

function useDebouncedValue(value: string, delayMs: number) {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedValue(value);
    }, delayMs);

    return () => window.clearTimeout(timer);
  }, [delayMs, value]);

  return debouncedValue;
}

function appendUniquePromptActivities(
  currentItems: PromptActivityItem[],
  nextItems: PromptActivityItem[],
) {
  const seenIds = new Set(currentItems.map((item) => item.id));
  return [
    ...currentItems,
    ...nextItems.filter((item) => {
      if (seenIds.has(item.id)) {
        return false;
      }
      seenIds.add(item.id);
      return true;
    }),
  ];
}

export function ActivityPanel({
  activityNavigation,
  data,
  onActivityNavigationChange,
  onDeletePromptActivity,
  onDeleteSessionActivity,
  onSharePrompt,
  notice,
  providedDataError = null,
  providedDataLoading = false,
  useProvidedData = false,
}: {
  activityNavigation?: ActivityNavigationState;
  data: ProjectDetailData;
  onActivityNavigationChange?: (state: ActivityNavigationState) => void;
  onDeletePromptActivity?: (promptEventId: string) => Promise<void>;
  onDeleteSessionActivity?: (sessionId: string) => Promise<void>;
  onSharePrompt?: (activity: PromptActivityItem) => void;
  notice?: string;
  providedDataError?: string | null;
  providedDataLoading?: boolean;
  useProvidedData?: boolean;
}) {
  const { t } = useI18n();
  const [localActivityNavigation, setLocalActivityNavigation] =
    useState<ActivityNavigationState>(defaultActivityNavigation);
  const [activitySearchInput, setActivitySearchInput] = useState("");
  const promptSearchQuery = useDebouncedValue(activitySearchInput, 300);
  const [promptActivities, setPromptActivities] = useState<PromptActivityItem[]>(
    data.promptActivities,
  );
  const [promptActivityTotal, setPromptActivityTotal] = useState<number | null>(null);
  const [promptActivityNextCursor, setPromptActivityNextCursor] =
    useState<string | null>(null);
  const [promptActivityHasMore, setPromptActivityHasMore] = useState(false);
  const [hasLoadedPromptActivityPage, setHasLoadedPromptActivityPage] = useState(useProvidedData);
  const [isPromptActivityLoading, setIsPromptActivityLoading] = useState(providedDataLoading);
  const [isPromptActivityLoadingMore, setIsPromptActivityLoadingMore] = useState(false);
  const [promptActivityError, setPromptActivityError] = useState<string | null>(null);
  const [promptActivityRequestVersion, setPromptActivityRequestVersion] = useState(0);
  const [activityWorkTypeFilter, setActivityWorkTypeFilter] =
    useState<WorkTypeFilter>("all");
  const [sessionConversationSearchInput, setSessionConversationSearchInput] =
    useState("");
  const sessionConversationSearchQuery = useDebouncedValue(
    sessionConversationSearchInput,
    300,
  );
  const [sessionPrompts, setSessionPrompts] = useState<PromptActivityItem[]>([]);
  const [sessionPromptTotal, setSessionPromptTotal] = useState<number | null>(null);
  const [sessionPromptNextCursor, setSessionPromptNextCursor] =
    useState<string | null>(null);
  const [sessionPromptHasMore, setSessionPromptHasMore] = useState(false);
  const [isSessionPromptLoading, setIsSessionPromptLoading] = useState(false);
  const [isSessionPromptLoadingMore, setIsSessionPromptLoadingMore] = useState(false);
  const [sessionPromptError, setSessionPromptError] = useState<string | null>(null);
  const [sessionPromptRequestVersion, setSessionPromptRequestVersion] = useState(0);
  const [deletedSessionIds, setDeletedSessionIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [deletionTarget, setDeletionTarget] =
    useState<ActivityDeletionTarget | null>(null);
  const [deletionError, setDeletionError] = useState<string | null>(null);
  const [isDeletingActivity, setIsDeletingActivity] = useState(false);
  const isDeletingActivityRef = useRef(false);
  const deletionDialogRef = useRef<HTMLElement | null>(null);
  const deletionReturnFocusRef = useRef<HTMLElement | null>(null);
  const deletionWasConfirmedRef = useRef(false);
  const activityViewTabsRef = useRef<HTMLDivElement | null>(null);
  const currentActivityNavigation =
    activityNavigation ?? localActivityNavigation;
  const updateActivityNavigation = (state: Partial<ActivityNavigationState>) => {
    const nextActivityNavigation = {
      ...currentActivityNavigation,
      ...state,
    };

    if (onActivityNavigationChange) {
      onActivityNavigationChange(nextActivityNavigation);
      return;
    }

    setLocalActivityNavigation(nextActivityNavigation);
  };
  const view = currentActivityNavigation.view;

  useEffect(() => {
    setDeletedSessionIds(new Set());
  }, [data.project.id]);

  useEffect(() => {
    if (!deletionTarget) {
      if (!deletionWasConfirmedRef.current) {
        return;
      }
      deletionWasConfirmedRef.current = false;
      const focusFrame = window.requestAnimationFrame(() => {
        activityViewTabsRef.current
          ?.querySelector<HTMLElement>('[aria-pressed="true"]')
          ?.focus();
      });
      return () => window.cancelAnimationFrame(focusFrame);
    }
    const dialog = deletionDialogRef.current;
    if (!dialog) {
      return;
    }
    const timer = window.setTimeout(() => {
      focusableModalElements(dialog)[0]?.focus();
    }, 0);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isDeletingActivityRef.current) {
        event.preventDefault();
        setDeletionTarget(null);
        setDeletionError(null);
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
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("keydown", handleKeyDown);
      const returnTarget = deletionReturnFocusRef.current;
      if (!deletionWasConfirmedRef.current) {
        window.requestAnimationFrame(() => {
          if (!returnTarget?.isConnected) {
            return;
          }
          returnTarget.focus();
        });
      }
    };
  }, [deletionTarget]);

  const openDeletionDialog = (target: ActivityDeletionTarget) => {
    deletionWasConfirmedRef.current = false;
    deletionReturnFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setDeletionError(null);
    setDeletionTarget(target);
  };

  const closeDeletionDialog = () => {
    if (isDeletingActivity) {
      return;
    }
    setDeletionTarget(null);
    setDeletionError(null);
  };

  const confirmActivityDeletion = async () => {
    if (!deletionTarget) {
      return;
    }
    isDeletingActivityRef.current = true;
    setIsDeletingActivity(true);
    setDeletionError(null);
    try {
      if (deletionTarget.kind === "prompt") {
        if (!onDeletePromptActivity) {
          return;
        }
        const promptId = deletionTarget.activity.id;
        await onDeletePromptActivity(promptId);
        deletionWasConfirmedRef.current = true;
        setPromptActivities((items) => items.filter((item) => item.id !== promptId));
        setSessionPrompts((items) => items.filter((item) => item.id !== promptId));
        setPromptActivityTotal((count) => count === null ? null : Math.max(count - 1, 0));
        setSessionPromptTotal((count) => count === null ? null : Math.max(count - 1, 0));
        setPromptActivityRequestVersion((version) => version + 1);
        setSessionPromptRequestVersion((version) => version + 1);
      } else {
        if (!onDeleteSessionActivity) {
          return;
        }
        await onDeleteSessionActivity(deletionTarget.activity.id);
        deletionWasConfirmedRef.current = true;
        setDeletedSessionIds((current) => {
          const next = new Set(current);
          next.add(deletionTarget.activity.id);
          return next;
        });
        setSessionPrompts([]);
        setSessionPromptTotal(null);
        setPromptActivityRequestVersion((version) => version + 1);
      }
      updateActivityNavigation({
        selectedPromptId: null,
        selectedSessionId: null,
        selectedSessionPromptId: null,
      });
      setDeletionTarget(null);
      restoreActiveActivityViewFocus();
    } catch (error) {
      setDeletionError(
        error instanceof Error ? error.message : t("activity.deleteFailed"),
      );
    } finally {
      isDeletingActivityRef.current = false;
      setIsDeletingActivity(false);
    }
  };
  const selectedPromptId = currentActivityNavigation.selectedPromptId;
  const selectedSessionId = currentActivityNavigation.selectedSessionId;
  const selectedSessionPromptId =
    currentActivityNavigation.selectedSessionPromptId;
  const hasPromptActivity = promptActivities.length > 0;
  const visibleSessionActivities = useMemo(
    () => data.activities.filter((activity) => !deletedSessionIds.has(activity.id)),
    [data.activities, deletedSessionIds],
  );
  const hasSessionActivity = visibleSessionActivities.length > 0;
  const hasActivityForView = view === "prompts" ? hasPromptActivity : hasSessionActivity;
  const unfilteredActivityFeedItems = useMemo<ActivityFeedItem[]>(() => {
    const promptItems: ActivityFeedItem[] = promptActivities.map(
      (activity, index) => ({
        activity,
        key: `prompt-${activity.id}`,
        kind: "prompt",
        sequenceIndex: index,
        timestamp: displayTimeValue(activity.submittedAt),
      }),
    );
    const sessionItems: ActivityFeedItem[] = visibleSessionActivities.map(
      (activity, index) => ({
        activity,
        key: `session-${activity.id}`,
        kind: "session",
        sequenceIndex: promptActivities.length + index,
        timestamp: displayTimeValue(activity.lastActivity),
      }),
    );
    const items = view === "sessions" ? sessionItems : promptItems;

    return [...items].sort(sortActivityFeedItems);
  }, [promptActivities, view, visibleSessionActivities]);
  const searchMatchedActivityFeedItems = useMemo(() => {
    const query = promptSearchQuery.trim().toLowerCase();

    if (view === "prompts" && !useProvidedData) {
      return unfilteredActivityFeedItems;
    }

    return unfilteredActivityFeedItems.filter((item) => {
      if (!query) {
        return true;
      }

      return activityFeedSearchText(item)
        .toLowerCase()
        .includes(query);
    });
  }, [promptSearchQuery, unfilteredActivityFeedItems, useProvidedData, view]);
  const activityWorkTypeCounts = useMemo(
    () => workTypeCounts(searchMatchedActivityFeedItems.map((item) => item.activity)),
    [searchMatchedActivityFeedItems],
  );
  const filteredActivityFeedItems = useMemo(() => {
    if (view === "sessions" || activityWorkTypeFilter === "all") {
      return searchMatchedActivityFeedItems;
    }

    return searchMatchedActivityFeedItems.filter(
      (item) => workTypeForFiles(item.activity.filesChanged) === activityWorkTypeFilter,
    );
  }, [activityWorkTypeFilter, searchMatchedActivityFeedItems, view]);
  const promptWorkGroups = useMemo<PromptWorkGroup[]>(() => {
    return buildPromptWorkGroups(
      filteredActivityFeedItems.flatMap((item) =>
        item.kind === "prompt" ? [item.activity] : [],
      ),
    );
  }, [filteredActivityFeedItems]);
  const selectedFeedItem =
    filteredActivityFeedItems.find((item) =>
      item.kind === "prompt"
        ? item.activity.id === selectedPromptId
        : item.activity.id === selectedSessionId,
    ) ??
    filteredActivityFeedItems[0] ??
    null;
  const selectedPromptWorkGroup =
    promptWorkGroups.find((group) =>
      group.prompts.some((prompt) => prompt.id === selectedPromptId),
    ) ??
    (selectedFeedItem?.kind === "prompt"
      ? promptWorkGroups.find((group) =>
          group.prompts.some((prompt) => prompt.id === selectedFeedItem.activity.id),
        )
      : null) ??
    promptWorkGroups[0] ??
    null;
  const selectedPrompt =
    selectedPromptWorkGroup?.prompts.find((prompt) => prompt.id === selectedPromptId) ??
    selectedPromptWorkGroup?.prompts[selectedPromptWorkGroup.prompts.length - 1] ??
    null;
  const selectedSession =
    selectedFeedItem?.kind === "session" ? selectedFeedItem.activity : null;
  const selectedSessionPrompts = selectedSession ? sessionPrompts : [];
  const promptTargetForCurrentSelection =
    selectedFeedItem?.kind === "prompt"
      ? selectedPrompt
      : selectedSessionPrompts.find(
          (activity) => activity.id === selectedSessionPromptId,
        ) ??
        selectedSessionPrompts[0] ??
        null;
  const selectedSessionIdForFetch = selectedSession?.id ?? null;

  useEffect(() => {
    setPromptActivities(data.promptActivities);
    setPromptActivityTotal(useProvidedData ? data.promptActivities.length : null);
    setPromptActivityNextCursor(null);
    setPromptActivityHasMore(false);
    setHasLoadedPromptActivityPage(useProvidedData && !providedDataLoading);
    setIsPromptActivityLoading(useProvidedData && providedDataLoading);
    setPromptActivityError(useProvidedData ? providedDataError : null);
    setSessionPrompts([]);
    setSessionPromptTotal(null);
    setSessionPromptNextCursor(null);
    setSessionPromptHasMore(false);
    setSessionPromptError(null);
  }, [data.project.id, data.promptActivities, providedDataError, providedDataLoading, useProvidedData]);

  useEffect(() => {
    if (useProvidedData || view !== "prompts" || !data.project.id) {
      return;
    }

    const controller = new AbortController();
    setIsPromptActivityLoading(true);
    setPromptActivityError(null);
    setPromptActivities([]);
    setPromptActivityTotal(null);
    setPromptActivityNextCursor(null);
    setPromptActivityHasMore(false);

    void fetchProjectPromptActivities({
      limit: PROMPT_ACTIVITY_PAGE_LIMIT,
      projectId: data.project.id,
      query: promptSearchQuery,
      signal: controller.signal,
    })
      .then((payload) => {
        const page = promptActivityPageFromApi(payload);
        setPromptActivities(page.items);
        setPromptActivityTotal(page.total);
        setPromptActivityNextCursor(page.nextCursor);
        setPromptActivityHasMore(page.hasMore);
        setHasLoadedPromptActivityPage(true);
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setPromptActivityError(
          error instanceof Error ? error.message : "Prompt activity request failed",
        );
        setHasLoadedPromptActivityPage(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsPromptActivityLoading(false);
        }
      });

    return () => controller.abort();
  }, [data.project.id, promptActivityRequestVersion, promptSearchQuery, useProvidedData, view]);

  useEffect(() => {
    if (useProvidedData) {
      const query = sessionConversationSearchQuery.trim().toLowerCase();
      const providedSessionPrompts = selectedSessionIdForFetch
        ? data.promptActivities.filter((activity) =>
            activity.sessionId === selectedSessionIdForFetch &&
            (!query || [activity.prompt, activity.response, activity.model]
              .filter(Boolean)
              .some((value) => value!.toLowerCase().includes(query))),
          )
        : [];
      setSessionPrompts(providedSessionPrompts);
      setSessionPromptTotal(providedSessionPrompts.length);
      setSessionPromptNextCursor(null);
      setSessionPromptHasMore(false);
      setSessionPromptError(null);
      return;
    }
    if (view !== "sessions" || !data.project.id || !selectedSessionIdForFetch) {
      setSessionPrompts([]);
      setSessionPromptTotal(null);
      setSessionPromptNextCursor(null);
      setSessionPromptHasMore(false);
      return;
    }

    const controller = new AbortController();
    setIsSessionPromptLoading(true);
    setSessionPromptError(null);
    setSessionPrompts([]);
    setSessionPromptTotal(null);
    setSessionPromptNextCursor(null);
    setSessionPromptHasMore(false);

    void fetchProjectPromptActivities({
      limit: PROMPT_ACTIVITY_PAGE_LIMIT,
      projectId: data.project.id,
      query: sessionConversationSearchQuery,
      sessionId: selectedSessionIdForFetch,
      signal: controller.signal,
    })
      .then((payload) => {
        const page = promptActivityPageFromApi(payload);
        setSessionPrompts(page.items);
        setSessionPromptTotal(page.total);
        setSessionPromptNextCursor(page.nextCursor);
        setSessionPromptHasMore(page.hasMore);
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setSessionPromptError(
          error instanceof Error ? error.message : "Session prompt request failed",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsSessionPromptLoading(false);
        }
      });

    return () => controller.abort();
  }, [
    data.project.id,
    selectedSessionIdForFetch,
    sessionConversationSearchQuery,
    sessionPromptRequestVersion,
    useProvidedData,
    view,
  ]);

  const loadMorePromptActivities = async () => {
    if (
      isPromptActivityLoadingMore ||
      !promptActivityHasMore ||
      !promptActivityNextCursor
    ) {
      return;
    }

    setIsPromptActivityLoadingMore(true);
    setPromptActivityError(null);
    try {
      const page = promptActivityPageFromApi(
        await fetchProjectPromptActivities({
          limit: PROMPT_ACTIVITY_PAGE_LIMIT,
          cursor: promptActivityNextCursor,
          projectId: data.project.id,
          query: promptSearchQuery,
        }),
      );
      setPromptActivities((currentItems) =>
        appendUniquePromptActivities(currentItems, page.items),
      );
      setPromptActivityTotal(page.total);
      setPromptActivityNextCursor(page.nextCursor);
      setPromptActivityHasMore(page.hasMore);
    } catch (error) {
      setPromptActivityError(
        error instanceof Error ? error.message : "Prompt activity request failed",
      );
    } finally {
      setIsPromptActivityLoadingMore(false);
    }
  };

  const loadMoreSessionPrompts = async () => {
    if (
      isSessionPromptLoadingMore ||
      !sessionPromptHasMore ||
      !sessionPromptNextCursor ||
      !selectedSessionIdForFetch
    ) {
      return;
    }

    setIsSessionPromptLoadingMore(true);
    setSessionPromptError(null);
    try {
      const page = promptActivityPageFromApi(
        await fetchProjectPromptActivities({
          limit: PROMPT_ACTIVITY_PAGE_LIMIT,
          cursor: sessionPromptNextCursor,
          projectId: data.project.id,
          query: sessionConversationSearchQuery,
          sessionId: selectedSessionIdForFetch,
        }),
      );
      setSessionPrompts((currentItems) =>
        appendUniquePromptActivities(currentItems, page.items),
      );
      setSessionPromptTotal(page.total);
      setSessionPromptNextCursor(page.nextCursor);
      setSessionPromptHasMore(page.hasMore);
    } catch (error) {
      setSessionPromptError(
        error instanceof Error ? error.message : "Session prompt request failed",
      );
    } finally {
      setIsSessionPromptLoadingMore(false);
    }
  };

  const updateActivityView = (nextView: ActivityNavigationState["view"]) => {
    if (nextView === "prompts") {
      updateActivityNavigation({
        selectedPromptId: promptTargetForCurrentSelection?.id ?? selectedPromptId,
        selectedSessionId: null,
        selectedSessionPromptId: null,
        view: "prompts",
      });
      return;
    }

    const sessionTarget =
      promptTargetForCurrentSelection !== null
        ? data.activities.find(
            (activity) => activity.id === promptTargetForCurrentSelection.sessionId,
          ) ?? null
        : selectedSession;

    updateActivityNavigation({
      selectedPromptId: null,
      selectedSessionId: sessionTarget?.id ?? selectedSessionId,
      selectedSessionPromptId: promptTargetForCurrentSelection?.id ?? null,
      view: "sessions",
    });
    setSessionConversationSearchInput("");
  };
  const filteredSessionPrompts = selectedSessionPrompts;
  const selectedSessionPrompt =
    filteredSessionPrompts.find(
      (activity) => activity.id === selectedSessionPromptId,
    ) ??
    filteredSessionPrompts[0] ??
    null;
  const activityViewOptions: ActivityNavigationState["view"][] = [
    "prompts",
    "sessions",
  ];
  const activityViewTabs = (
    <div
      aria-label={t("activity.filters")}
      className="bh-activity-view-tabs"
      ref={activityViewTabsRef}
      role="group"
    >
      {activityViewOptions.map((activityView) => (
        <button
          aria-pressed={view === activityView}
          className="bh-activity-view-tab"
          data-active={view === activityView}
          key={activityView}
          onClick={() => updateActivityView(activityView)}
          type="button"
        >
          {activityView === "prompts" ? t("activity.byWork") : t("activity.bySession")}
        </button>
      ))}
    </div>
  );

  if (
    view === "prompts" &&
    promptActivityError &&
    !hasPromptActivity &&
    hasLoadedPromptActivityPage &&
    !isPromptActivityLoading
  ) {
    return (
      <div className="bh-activity-layout" data-notice={notice ? "true" : undefined} data-view={view}>
        {notice ? <div className="bh-activity-notice">{notice}</div> : null}
        {activityViewTabs}
        <EmptyState
          description={promptActivityError}
          icon={Activity}
          title={t("activity.loadFailed")}
        >
          <button
            className="bh-empty-state-button"
            onClick={() => setPromptActivityRequestVersion((version) => version + 1)}
            type="button"
          >
            {t("common.retry")}
          </button>
        </EmptyState>
      </div>
    );
  }

  if (
    !hasActivityForView &&
    (view === "sessions" || hasLoadedPromptActivityPage) &&
    (view === "sessions" ? !isSessionPromptLoading : !isPromptActivityLoading)
  ) {
    return (
      <div className="bh-activity-layout" data-notice={notice ? "true" : undefined} data-view={view}>
        {notice ? <div className="bh-activity-notice">{notice}</div> : null}
        {activityViewTabs}
        <EmptyState
          description={t("activity.noActivityDescription")}
          icon={Activity}
          title={t("activity.noActivity")}
        />
      </div>
    );
  }

  return (
    <div className="bh-activity-layout" data-notice={notice ? "true" : undefined} data-view={view}>
      {notice ? <div className="bh-activity-notice">{notice}</div> : null}
      {activityViewTabs}

      <div
        className="bh-activity-feed-layout"
        data-detail={selectedFeedItem?.kind ?? "prompt"}
      >
        <div className="bh-activity-feed-sidebar">
          <label className="bh-prompt-search">
            <Search aria-hidden="true" size={15} strokeWidth={1.7} />
            <input
              aria-label={t("activity.searchFull")}
              onChange={(event) => setActivitySearchInput(event.target.value)}
              placeholder={t("activity.search")}
              type="search"
              value={activitySearchInput}
            />
          </label>
          {view === "prompts" ? (
            <WorkTypeFilterControl
              ariaLabel="Filter activity by work type"
              counts={activityWorkTypeCounts}
              onChange={setActivityWorkTypeFilter}
              value={activityWorkTypeFilter}
            />
          ) : null}

          <div className="bh-latest-prompt-list">
            {view === "prompts" && promptActivityError ? (
              <div className="bh-inline-request-error" role="alert">
                <span>{promptActivityError}</span>
                <button
                  onClick={() =>
                    setPromptActivityRequestVersion((version) => version + 1)
                  }
                  type="button"
                >
                  {t("common.retry")}
                </button>
              </div>
            ) : null}
            {view === "prompts" &&
            isPromptActivityLoading &&
            promptActivities.length === 0 ? (
              <div className="bh-prompt-search-empty">{t("activity.loadingPrompts")}</div>
            ) : filteredActivityFeedItems.length > 0 ? (
              <div className="bh-prompt-list">
                {view === "prompts"
                  ? promptWorkGroups.map((group) => {
                      const latestPrompt = group.prompts[group.prompts.length - 1];
                      return (
                        <PromptWorkGroupCard
                          group={group}
                          isSelected={group.id === selectedPromptWorkGroup?.id}
                          key={group.id}
                          onOpen={() =>
                            updateActivityNavigation({
                              selectedPromptId: latestPrompt.id,
                              selectedSessionId: null,
                              selectedSessionPromptId: null,
                            })
                          }
                        />
                      );
                    })
                  : filteredActivityFeedItems.map((item) =>
                      item.kind === "session" ? (
                        <ActivityCard
                          activity={item.activity}
                          isSelected={item.key === selectedFeedItem?.key}
                          key={item.key}
                          onOpen={() => {
                            updateActivityNavigation({
                              selectedPromptId: null,
                              selectedSessionId: item.activity.id,
                              selectedSessionPromptId: null,
                            });
                            setSessionConversationSearchInput("");
                          }}
                        />
                      ) : null,
                    )}
              </div>
            ) : !promptActivityError ? (
              <div className="bh-prompt-search-empty">
                {t("activity.noFilterMatches")}
              </div>
            ) : null}
            {view === "prompts" && promptActivityHasMore && promptActivityNextCursor ? (
              <button
                className="bh-prompt-page-action"
                disabled={isPromptActivityLoadingMore}
                onClick={() => {
                  void loadMorePromptActivities();
                }}
                type="button"
              >
                {isPromptActivityLoadingMore ? t("activity.loading") : t("activity.loadMore")}
              </button>
            ) : null}
            {view === "prompts" && promptActivityTotal !== null ? (
              <div className="bh-prompt-page-count">
                {promptActivities.length}/{promptActivityTotal}
              </div>
            ) : null}
          </div>
        </div>

        {selectedFeedItem?.kind === "session" ? (
          <>
            <section
              aria-label={t("activity.sessionConversations")}
              className="bh-session-conversation-panel"
            >
              {selectedSession ? (
                <>
                  <div className="bh-session-conversation-toolbar">
                    <div>
                      <span>{t("activity.selectedSession")}</span>
                      <strong>{t("activity.session", { id: selectedSession.id.slice(0, 8) })}</strong>
                    </div>
                    {onDeleteSessionActivity ? (
                      <button
                        className="bh-header-action-button is-danger-quiet"
                        onClick={() => openDeletionDialog({
                          activity: selectedSession,
                          kind: "session",
                        })}
                        type="button"
                      >
                        <Trash2 aria-hidden="true" size={15} strokeWidth={1.6} />
                        <span>{t("activity.deleteSession")}</span>
                      </button>
                    ) : null}
                  </div>
                  <label className="bh-prompt-search">
                    <Search aria-hidden="true" size={15} strokeWidth={1.7} />
                    <input
                      aria-label={t("activity.searchConversations")}
                      onChange={(event) =>
                        setSessionConversationSearchInput(event.target.value)
                      }
                      placeholder={t("activity.searchConversations")}
                      type="search"
                      value={sessionConversationSearchInput}
                    />
                  </label>

                  <div className="bh-session-prompt-list">
                    {sessionPromptError ? (
                      <div className="bh-inline-request-error" role="alert">
                        <span>{sessionPromptError}</span>
                        <button
                          onClick={() =>
                            setSessionPromptRequestVersion((version) => version + 1)
                          }
                          type="button"
                        >
                          {t("common.retry")}
                        </button>
                      </div>
                    ) : null}
                    {isSessionPromptLoading && selectedSessionPrompts.length === 0 ? (
                      <div className="bh-prompt-search-empty">
                        {t("activity.loadingConversations")}
                      </div>
                    ) : selectedSessionPrompts.length > 0 ? (
                      filteredSessionPrompts.length > 0 ? (
                        <>
                          <div className="bh-prompt-list">
                            {filteredSessionPrompts.map((activity) => (
                              <PromptActivityCard
                                activity={activity}
                                isSelected={activity.id === selectedSessionPrompt?.id}
                                key={activity.id}
                                onOpen={() => {
                                  updateActivityNavigation({
                                    selectedPromptId: null,
                                    selectedSessionId: selectedSession.id,
                                    selectedSessionPromptId: activity.id,
                                  });
                                }}
                                promptLabel={t("activity.promptLabel", { sequence: activity.sequence })}
                              />
                            ))}
                          </div>
                          {sessionPromptHasMore && sessionPromptNextCursor ? (
                            <button
                              className="bh-prompt-page-action"
                              disabled={isSessionPromptLoadingMore}
                              onClick={() => {
                                void loadMoreSessionPrompts();
                              }}
                              type="button"
                            >
                              {isSessionPromptLoadingMore ? t("activity.loading") : t("activity.loadMore")}
                            </button>
                          ) : null}
                          {sessionPromptTotal !== null ? (
                            <div className="bh-prompt-page-count">
                              {sessionPrompts.length}/{sessionPromptTotal}
                            </div>
                          ) : null}
                        </>
                      ) : (
                        !sessionPromptError ? (
                          <div className="bh-prompt-search-empty">
                            {t("activity.noConversationsMatch")}
                          </div>
                        ) : null
                      )
                    ) : (
                      !sessionPromptError ? (
                        <div className="bh-prompt-search-empty">
                          {t("activity.noSessionPrompts")}
                        </div>
                      ) : null
                    )}
                  </div>
                </>
              ) : (
                <div className="bh-prompt-search-empty">
                  {t("activity.selectSession")}
                </div>
              )}
            </section>

            <PromptChangeDetail
              activity={selectedSessionPrompt}
              onDeletePrompt={onDeletePromptActivity ? (activity) =>
                openDeletionDialog({ activity, kind: "prompt" }) : undefined}
              onSharePrompt={onSharePrompt}
            />
          </>
        ) : (
          <>
            <PromptWorkTimeline
              group={selectedPromptWorkGroup}
              onDeletePrompt={onDeletePromptActivity ? (activity) =>
                openDeletionDialog({ activity, kind: "prompt" }) : undefined}
              onSelectPrompt={(activity) =>
                updateActivityNavigation({
                  selectedPromptId: activity.id,
                  selectedSessionId: null,
                  selectedSessionPromptId: null,
                })}
              onSharePrompt={onSharePrompt}
              selectedActivity={selectedPrompt}
            />
          </>
        )}
      </div>

      {deletionTarget ? (
        <div className="bh-activity-delete-overlay" role="presentation">
          <section
            aria-describedby="activity-delete-description"
            aria-labelledby="activity-delete-title"
            aria-modal="true"
            className="bh-activity-delete-dialog"
            ref={deletionDialogRef}
            role="alertdialog"
          >
            <header className="bh-activity-delete-header">
              <span aria-hidden="true"><AlertTriangle size={20} strokeWidth={1.7} /></span>
              <div>
                <small>{t("activity.deleteEyebrow")}</small>
                <h2 id="activity-delete-title">
                  {deletionTarget.kind === "prompt"
                    ? t("activity.deletePromptTitle")
                    : t("activity.deleteSessionTitle")}
                </h2>
              </div>
              <button
                aria-label={t("common.close")}
                disabled={isDeletingActivity}
                onClick={closeDeletionDialog}
                type="button"
              >
                <X aria-hidden="true" size={17} />
              </button>
            </header>

            <p id="activity-delete-description">
              {deletionTarget.kind === "prompt"
                ? t("activity.deletePromptDescription")
                : t("activity.deleteSessionDescription", {
                    count: deletionTarget.activity.prompts,
                  })}
            </p>

            <div className="bh-activity-delete-scope">
              <div>
                <Trash2 aria-hidden="true" size={16} />
                <span>
                  <strong>{t("activity.deleteRawScopeTitle")}</strong>
                  {deletionTarget.kind === "prompt"
                    ? t("activity.deletePromptScope")
                    : t("activity.deleteSessionScope")}
                </span>
              </div>
              <div>
                <Share2 aria-hidden="true" size={16} />
                <span>
                  <strong>{t("activity.deleteCommunityTitle")}</strong>
                  {t("activity.deleteCommunityNotice")}
                </span>
              </div>
              <div data-preserved="true">
                <Sparkles aria-hidden="true" size={16} />
                <span>
                  <strong>{t("activity.deleteMemoryTitle")}</strong>
                  {t("activity.deleteMemoryNotice")}
                </span>
              </div>
            </div>

            <div className="bh-activity-delete-warning">
              <AlertTriangle aria-hidden="true" size={16} />
              <span>{t("activity.deletePermanent")}</span>
            </div>
            {deletionError ? (
              <p className="bh-activity-delete-error" role="alert">{deletionError}</p>
            ) : null}
            <footer>
              <button
                disabled={isDeletingActivity}
                onClick={closeDeletionDialog}
                type="button"
              >
                {t("common.cancel")}
              </button>
              <button
                className="is-danger"
                disabled={isDeletingActivity}
                onClick={() => void confirmActivityDeletion()}
                type="button"
              >
                {isDeletingActivity ? (
                  <LoaderCircle aria-hidden="true" className="bh-spin" size={16} />
                ) : (
                  <Trash2 aria-hidden="true" size={16} />
                )}
                {isDeletingActivity
                  ? t("activity.deleting")
                  : t("activity.deleteConfirm")}
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </div>
  );
}
