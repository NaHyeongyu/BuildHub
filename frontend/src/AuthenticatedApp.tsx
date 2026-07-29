import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";
import {
  clearCurrentUserCache,
  fetchCurrentUser,
  getCachedCurrentUser,
  logoutSession,
} from "./api/auth";
import { updateAdminAlertState } from "./api/admin";
import { UnauthorizedError } from "./api/client";
import { AdminDashboard } from "./components/app/AdminDashboard";
import { AccountWorkspaceRoute } from "./components/app/AccountWorkspaceRoute";
import { WebLoginPage } from "./components/app/AuthScreens";
import { ProjectsPage } from "./components/app/ProjectsPage";
import { CommunityHubPage } from "./components/app/CommunityHubPage";
import { CollectorUpdateModal } from "./components/app/CollectorUpdateModal";
import { PublicProjectsPage } from "./components/app/PublicProjectsPage";
import { PolicyConsentModal } from "./components/app/PolicyConsentModal";
import { ReviewQueueDrawer } from "./components/app/ReviewQueueDrawer";
import { RepositoryConnector } from "./components/app/RepositoryConnector";
import { SupportPage } from "./components/app/SupportPage";
import { WorkspaceSidebar } from "./components/app/WorkspaceSidebar";
import { LoadingScreen } from "./components/app/WorkspaceStates";
import {
  ProjectDetailPage,
  type ActivityNavigationState,
  type ProjectDetailTabId,
} from "./components/project-detail";
import { API_URL } from "./config";
import { fetchMemoryGenerationReview } from "./api/projects";
import { formatRelativeTimestamp } from "./lib/formatters";
import { getCollectorHealth } from "./lib/collectorHealth";
import { isCollectorUpdateAvailable } from "./lib/collectorVersion";
import { githubFileUrl } from "./lib/github";
import { useI18n } from "./i18n/I18nProvider";
import { navigateToAppUrl } from "./routing";
import { useAccountSettings } from "./hooks/useAccountSettings";
import { useAdminOverview } from "./hooks/useAdminOverview";
import { useProjectActions } from "./hooks/useProjectActions";
import { useProjectCatalog } from "./hooks/useProjectCatalog";
import { useProjectDetail } from "./hooks/useProjectDetail";
import { useProjectFiles } from "./hooks/useProjectFiles";
import { useProjectSharing } from "./hooks/useProjectSharing";
import { useRepositoryConnector } from "./hooks/useRepositoryConnector";
import { useRepositoryFiles } from "./hooks/useRepositoryFiles";
import {
  useInitialWorkspaceNavigationState,
  useWorkspaceNavigationState,
} from "./hooks/useWorkspaceNavigationState";
import { useWorkspaceData } from "./hooks/useWorkspaceData";
import {
  useWorkspaceAdminEffect,
  useWorkspaceProjectResourceEffects,
  useWorkspaceProjectRouteEffect,
} from "./hooks/useWorkspaceEffects";
import {
  DEFAULT_URL_NAVIGATION_STATE,
  currentWorkspaceReturnUrl,
  normalizeUrlNavigationState,
  sanitizeProjectRouteKey,
  writeUrlNavigationState,
  type UrlNavigationState,
  type UrlNavigationWriteMode,
} from "./workspace/navigation";
import { emptyProjectDetailData } from "./workspace/projectDetailMappers";
import { isMockGithubUnlinkedProject } from "./workspace/previewData";
import { pendingReviewProjectCount } from "./workspace/reviewQueue";
import type {
  AuthStatus,
  AuthUser,
  EventRecord,
  Project,
  SidebarItemId,
} from "./workspace/types";
import "./styles-loading.css";
import "./styles-workspace.css";
import "./styles-review-queue.css";
import "./styles-account.css";
import "./styles-responsive.css";
import "./styles-onboarding.css";
import "./styles-navigation.css";
import "./styles-projects.css";
import "./styles-public-projects.css";
import "./styles-support.css";

function githubRepositoryConnectUrl() {
  return `${API_URL}/api/auth/github/web/repository/start?${new URLSearchParams({
    return_to: currentWorkspaceReturnUrl(),
  })}`;
}

export function AuthenticatedApp() {
  const { setLocale, t } = useI18n();
  const initialNavigationState = useInitialWorkspaceNavigationState();
  const initialCachedUser = useMemo(getCachedCurrentUser, []);
  const [authStatus, setAuthStatus] = useState<AuthStatus>(
    initialCachedUser ? "authenticated" : "loading",
  );
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(initialCachedUser);
  const [authorizationNotice, setAuthorizationNotice] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("auth_error") ===
    "github_authorization_cancelled"
      ? t("auth.authorizationCancelled")
      : null,
  );
  const [isReviewQueueOpen, setIsReviewQueueOpen] = useState(
    () => new URLSearchParams(window.location.search).get("view") === "reviews",
  );
  const [isCollectorUpdateOpen, setIsCollectorUpdateOpen] = useState(false);
  const [collectorVerificationStartedAt, setCollectorVerificationStartedAt] =
    useState<number | null>(null);
  const [collectorUpdateTargetVersion, setCollectorUpdateTargetVersion] =
    useState<string | null>(null);
  const [reviewQueueProjectId, setReviewQueueProjectId] = useState<string | null>(null);
  const [unresolvedProjectRouteKey, setUnresolvedProjectRouteKey] =
    useState<string | null>(null);
  const reviewQueueReturnFocusRef = useRef<HTMLElement | null>(null);
  const handleUnauthorized = useCallback(() => {
    clearCurrentUserCache();
    setIsReviewQueueOpen(false);
    setReviewQueueProjectId(null);
    setAuthStatus("unauthenticated");
    setCurrentUser(null);
  }, []);
  const {
    clearWorkspaceData,
    errorMessage,
    hasLoadedWorkspaceData,
    isWorkspaceLoading,
    loadWorkspace,
    mergeProjectSummary: mergeWorkspaceProjectSummary,
    projects,
    removeProject,
    replaceProjectSummaries,
    setErrorMessage,
  } = useWorkspaceData({
    onAuthenticated: () => setAuthStatus("authenticated"),
    onLoadError: () =>
      setAuthStatus((status) => (status === "loading" ? "error" : status)),
    onUnauthorized: handleUnauthorized,
  });
  const {
    applyProjectSummaryToDetail,
    clearProjectDetail,
    isProjectDetailLoading,
    loadProjectDetail,
    loadProjectMemoryArtifacts,
    projectDetail,
    projectDetailError,
    setProjectDetail,
  } = useProjectDetail({ onUnauthorized: handleUnauthorized });
  const {
    clearProjectFiles,
    isProjectFilesLoading,
    loadProjectFiles,
    projectFiles,
    projectFilesError,
    projectFilesTotal,
    projectFilesTruncated,
  } = useProjectFiles({ onUnauthorized: handleUnauthorized });
  const {
    clearRepositoryBrowserState,
    clearRepositoryFileContent,
    clearRepositoryFileContentState,
    clearRepositoryFiles,
    isProjectGithubFilesLoading,
    isRepositoryFileContentLoading,
    loadProjectGithubFiles,
    loadRepositoryFileContent,
    projectGithubFiles,
    projectGithubFilesError,
    repositoryFileContent,
    repositoryFileContentError,
    repositoryFileContentPath,
    setRepositoryFileContentPath,
  } = useRepositoryFiles({
    initialPath: initialNavigationState.repositoryFileContentPath,
    onUnauthorized: handleUnauthorized,
  });
  const {
    closeRepositoryConnector,
    isRepositoryConnectorOpen,
    openRepositoryConnector,
    repositoryConnectorProjectId,
  } = useRepositoryConnector();
  const {
    activeDetailTab,
    activeItem,
    activityNavigation,
    applyNavigationState,
    currentNavigationState,
    selectedProjectId,
    selectedProjectRouteKey,
    selectedPublicProfileId,
    selectedPublicProjectId,
  } = useWorkspaceNavigationState({
    initialNavigationState,
    onPopState: () => {
      closeRepositoryConnector();
      setIsReviewQueueOpen(false);
    },
    repositoryFileContentPath,
    setRepositoryFileContentPath,
  });
  const {
    adminError,
    adminOverview,
    clearAdminOverview,
    isAdminLoading,
    loadAdminOverview,
  } = useAdminOverview({ onUnauthorized: handleUnauthorized });
  const accountSettings = useAccountSettings({ onUnauthorized: handleUnauthorized });
  const refreshAccountOverviewRef = useRef(accountSettings.refreshAccountOverview);
  useEffect(() => {
    refreshAccountOverviewRef.current = accountSettings.refreshAccountOverview;
  }, [accountSettings.refreshAccountOverview]);
  const activeCollectorTokens = useMemo(
    () =>
      accountSettings.accountOverview?.collector_tokens.filter(
        (token) => token.status === "active",
      ) ?? [],
    [accountSettings.accountOverview],
  );
  const latestCollectorToken = [...activeCollectorTokens]
    .filter((token) => token.last_used_at)
    .sort(
      (first, second) =>
        Date.parse(second.last_used_at ?? "") - Date.parse(first.last_used_at ?? ""),
    )[0];
  const currentCollectorVersion = latestCollectorToken?.collector_version ?? null;
  const latestCollectorVersion =
    accountSettings.accountOverview?.latest_collector_version ?? "";
  const collectorUpdateComplete = Boolean(
    collectorVerificationStartedAt &&
      collectorUpdateTargetVersion &&
      activeCollectorTokens.some(
        (token) =>
          token.last_used_at &&
          Date.parse(token.last_used_at) >= collectorVerificationStartedAt &&
          !isCollectorUpdateAvailable(
            token.collector_version,
            collectorUpdateTargetVersion,
          ),
      ),
  );
  const closeCollectorUpdate = useCallback(() => {
    setIsCollectorUpdateOpen(false);
    setCollectorVerificationStartedAt(null);
  }, []);
  useEffect(() => {
    if (
      !isCollectorUpdateOpen ||
      collectorVerificationStartedAt === null ||
      collectorUpdateComplete
    ) {
      return;
    }
    void refreshAccountOverviewRef.current();
    const intervalId = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void refreshAccountOverviewRef.current();
      }
    }, 5_000);
    return () => window.clearInterval(intervalId);
  }, [
    collectorUpdateComplete,
    collectorVerificationStartedAt,
    isCollectorUpdateOpen,
  ]);
  const previewMode = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("preview");
  }, []);
  const previewEmptyProjects = previewMode === "empty-projects";
  const previewGithubUnlinkedProject = previewMode === "github-unlinked-project";
  const previewProjectLoading = previewMode === "project-loading";
  const previewCommunity = previewMode === "community";
  const {
    bookmarkedProjects,
    displayProjects,
    projectCatalog,
    projectHeaderOptions,
    projectSearchQuery,
    projectSortMode,
    repositoryConnectorProject,
    selectedProject,
    setProjectSearchQuery,
    setProjectSortMode,
    visibleBookmarkedProjects,
    visibleProjects,
  } = useProjectCatalog({
    previewEmptyProjects,
    previewGithubUnlinkedProject,
    projects,
    repositoryConnectorProjectId,
    selectedProjectId,
  });
  const activeTitle =
    activeItem === "projects"
      ? t("nav.projects")
      : activeItem === "pinned"
        ? t("nav.pinned")
        : activeItem === "community"
          ? t("nav.community")
          : activeItem === "support"
            ? t("nav.support")
            : activeItem === "admin"
              ? t("nav.admin")
              : activeItem === "settings"
                ? t("settings.title")
                : t("settings.account");
  const projectRouteKey = (project: Project | null | undefined) =>
    sanitizeProjectRouteKey(project?.slug) ?? project?.id ?? null;
  const projectMatchesRouteKey = (project: Project, routeKey: string) =>
    projectRouteKey(project) === routeKey || project.id === routeKey;
  const {
    activeProjectMemoryGenerationIds,
    approveProjectMemoryForAgents,
    bookmarkUpdatingProjectId,
    createProjectFromRepository,
    deleteSelectedProject,
    deleteSelectedPromptActivity,
    deleteSelectedSessionActivity,
    delayedProjectMemoryGenerationIds,
    generateProjectMemory,
    saveProjectDescription,
    saveProjectMetadata,
    saveRepositoryConnection,
    toggleProjectBookmark,
  } = useProjectActions({
    applyProjectSummaryToDetail,
    clearRepositoryFiles,
    loadProjectDetail,
    loadProjectGithubFiles,
    mergeProjectSummary: mergeWorkspaceProjectSummary,
    onProjectDeleted: closeProjectDetail,
    onProjectSlugChange: (slug) => {
      const nextState = normalizeUrlNavigationState({
        ...currentNavigationState,
        selectedProjectRouteKey: slug,
      });
      applyNavigationState(nextState);
      writeUrlNavigationState(nextState, "replace");
    },
    onUnauthorized: handleUnauthorized,
    removeProject,
    selectedProject,
    selectedProjectMemoryBatch: projectDetail?.memory.latestBatch ?? null,
    selectedProjectMemoryBatchProjectId: projectDetail?.project.id ?? null,
    selectedProjectId,
    setErrorMessage,
  });
  const { copiedProjectId, shareProject } = useProjectSharing({
    projectRouteKey,
    setErrorMessage,
  });

  const navigateWorkspace = (
    state: Partial<UrlNavigationState>,
    mode: UrlNavigationWriteMode = "push",
  ) => {
    const requestedProjectId =
      state.selectedProjectId === undefined
        ? currentNavigationState.selectedProjectId
        : state.selectedProjectId;
    const requestedProject =
      requestedProjectId === null
        ? null
        : projectCatalog.find((project) => project.id === requestedProjectId) ?? null;
    const requestedProjectRouteKey = Object.prototype.hasOwnProperty.call(
      state,
      "selectedProjectRouteKey",
    )
      ? state.selectedProjectRouteKey
      : undefined;
    const fallbackProjectRouteKey =
      requestedProjectId === null
        ? null
        : projectRouteKey(requestedProject) ??
          requestedProjectId ??
          currentNavigationState.selectedProjectRouteKey;
    const nextState = normalizeUrlNavigationState({
      ...currentNavigationState,
      ...state,
      selectedProjectRouteKey:
        requestedProjectRouteKey !== undefined
          ? requestedProjectRouteKey
          : fallbackProjectRouteKey,
    });

    applyNavigationState(nextState);

    if (nextState.repositoryFileContentPath !== repositoryFileContentPath) {
      clearRepositoryFileContentState();
    }

    writeUrlNavigationState(nextState, mode);
  };

  const loadSession = async () => {
    if (!getCachedCurrentUser()) {
      setAuthStatus("loading");
      clearWorkspaceData();
      accountSettings.clearAccountSettings();
    }
    setErrorMessage(null);
    if (previewCommunity) {
      setCurrentUser({
        avatar_url: null,
        email: "preview@promty.dev",
        github_repository_access: false,
        id: "community-preview-user",
        is_admin: false,
        preferred_locale: "en",
        username: "promty.preview",
      });
      setLocale("en");
      setAuthStatus("authenticated");
      return;
    }
    try {
      const user = await fetchCurrentUser();
      setCurrentUser(user);
      setLocale(user.preferred_locale);
      setAuthStatus("authenticated");
      await Promise.all([loadWorkspace(), accountSettings.loadAccountOverview()]);
    } catch (error) {
      if (error instanceof UnauthorizedError) {
        handleUnauthorized();
        clearWorkspaceData();
        return;
      }
      setErrorMessage(error instanceof Error ? error.message : t("auth.sessionRequestFailed"));
      setAuthStatus("error");
    }
  };

  const logout = async () => {
    await logoutSession().catch(() => undefined);
    setCurrentUser(null);
    clearWorkspaceData();
    applyNavigationState(DEFAULT_URL_NAVIGATION_STATE);
    clearProjectDetail();
    clearProjectFiles();
    clearRepositoryFiles();
    clearAdminOverview();
    accountSettings.clearAccountSettings();
    closeRepositoryConnector();
    setIsReviewQueueOpen(false);
    setAuthStatus("unauthenticated");
    writeUrlNavigationState(DEFAULT_URL_NAVIGATION_STATE, "replace");
  };

  useEffect(() => {
    void loadSession();
  }, []);

  useEffect(() => {
    if (authStatus !== "authenticated" || previewCommunity) {
      return;
    }

    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") {
        void refreshAccountOverviewRef.current();
      }
    };
    const intervalId = window.setInterval(refreshWhenVisible, 5 * 60 * 1000);
    document.addEventListener("visibilitychange", refreshWhenVisible);

    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [authStatus, previewCommunity]);

  useWorkspaceAdminEffect({
    activeItem,
    authStatus,
    currentUserIsAdmin: currentUser?.is_admin,
    loadAdminOverview,
    navigateWorkspace,
  });
  useWorkspaceProjectRouteEffect({
    allowUnlistedProject: currentUser?.is_admin === true,
    activeItem,
    hasLoadedWorkspaceData,
    navigateWorkspace,
    onProjectRouteNotFound: setUnresolvedProjectRouteKey,
    projectCatalog,
    projectMatchesRouteKey,
    projectRouteKey,
    selectedProjectId,
    selectedProjectRouteKey,
  });
  useWorkspaceProjectResourceEffects({
    activeDetailTab,
    activeItem,
    clearProjectDetail,
    clearProjectFiles,
    clearRepositoryBrowserState,
    clearRepositoryFileContentState,
    clearRepositoryFiles,
    loadProjectDetail,
    loadProjectFiles,
    loadProjectGithubFiles,
    loadRepositoryFileContent,
    repositoryFileContentPath,
    selectedProject,
    selectedProjectId,
    selectedProjectRouteKey,
    setProjectDetail,
  });

  const openProjectDetail = (projectId: string) => {
    closeRepositoryConnector();
    setIsReviewQueueOpen(false);
    navigateWorkspace({
      activeDetailTab: "overview",
      activeItem: "projects",
      repositoryFileContentPath: null,
      selectedProjectId: projectId,
    });
  };
  const openProjectMemory = (projectId: string) => {
    closeRepositoryConnector();
    setIsReviewQueueOpen(false);
    navigateWorkspace({
      activeDetailTab: "memory",
      activeItem: "projects",
      repositoryFileContentPath: null,
      selectedProjectId: projectId,
    });
    window.setTimeout(() => {
      document.getElementById("project-tab-memory")?.focus();
    }, 0);
  };
  const openFirstCapturedEvent = async (event: EventRecord) => {
    await Promise.all([loadWorkspace(), accountSettings.loadAccountOverview()]);
    closeRepositoryConnector();
    setIsReviewQueueOpen(false);
    navigateWorkspace({
      activeDetailTab: "ai-activity",
      activeItem: "projects",
      activityNavigation: {
        selectedPromptId: null,
        selectedSessionId: event.session_id,
        selectedSessionPromptId: null,
        view: "sessions",
      },
      repositoryFileContentPath: null,
      selectedProjectId: event.project_id,
    });
  };
  const switchProjectDetail = (projectId: string) => {
    if (projectId === selectedProjectId) {
      return;
    }

    closeRepositoryConnector();
    setIsReviewQueueOpen(false);
    clearProjectDetail();
    clearProjectFiles();
    clearRepositoryBrowserState();
    navigateWorkspace({
      activeDetailTab,
      activeItem: "projects",
      activityNavigation: DEFAULT_URL_NAVIGATION_STATE.activityNavigation,
      repositoryFileContentPath: null,
      selectedProjectId: projectId,
    });
  };
  function closeProjectDetail() {
    closeRepositoryConnector();
    setIsReviewQueueOpen(false);
    navigateWorkspace({
      activeDetailTab: "overview",
      activeItem: "projects",
      repositoryFileContentPath: null,
      selectedProjectId: null,
    });
    clearProjectDetail();
    clearProjectFiles();
    clearRepositoryFiles();
  }
  const selectSidebarItem = (item: SidebarItemId) => {
    if (item === "admin" && !currentUser?.is_admin) {
      return;
    }

    if (item === "admin") {
      if (!navigateToAppUrl("/admin")) {
        window.location.assign("/admin");
      }
      return;
    }

    setIsReviewQueueOpen(false);

    if ((item === "projects" || item === "pinned") && item !== activeItem) {
      setProjectSearchQuery("");
    }

    if (item === "projects" && selectedProjectId) {
      closeProjectDetail();
      return;
    }

    closeRepositoryConnector();
    navigateWorkspace({
      activeDetailTab: "overview",
      activeItem: item,
      repositoryFileContentPath: null,
      selectedProjectId: null,
    });
  };
  const openReviewQueue = (
    returnFocusElement: HTMLElement | null,
    projectId: string | null = null,
  ) => {
    reviewQueueReturnFocusRef.current = returnFocusElement;
    setReviewQueueProjectId(projectId);
    closeRepositoryConnector();
    setIsReviewQueueOpen(true);
  };
  const openRepositoryConnectorOverlay = (projectId: string | null) => {
    setIsReviewQueueOpen(false);
    openRepositoryConnector(projectId);
  };
  const selectProjectDetailTab = (tab: ProjectDetailTabId) => {
    navigateWorkspace({
      activeDetailTab: tab,
      activeItem: "projects",
      activityNavigation:
        tab === "ai-activity"
          ? {
              selectedPromptId: null,
              selectedSessionId: null,
              selectedSessionPromptId: null,
              view: "prompts",
            }
          : activityNavigation,
      repositoryFileContentPath:
        tab === "files" ? repositoryFileContentPath : null,
    });
  };
  const selectRepositoryFile = (path: string) => {
    const url = githubFileUrl(
      projectDetail?.project.repositoryUrl ?? selectedProject?.githubUrl,
      projectDetail?.project.defaultBranch ?? selectedProject?.defaultBranch,
      path,
    );
    if (url) {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  };
  const selectActivityNavigation = (nextActivityNavigation: ActivityNavigationState) => {
    navigateWorkspace({
      activeDetailTab: "ai-activity",
      activeItem: "projects",
      activityNavigation: nextActivityNavigation,
      repositoryFileContentPath: null,
    });
  };
  if (authStatus === "loading") {
    return <LoadingScreen />;
  }

  if (authStatus === "unauthenticated") {
    return <WebLoginPage errorMessage={null} />;
  }

  if (authStatus === "error") {
    return <WebLoginPage errorMessage={errorMessage} isError />;
  }

  const activeProjectId =
    selectedProject?.id ?? projectDetail?.project.id ?? selectedProjectId;
  const projectDetailBase =
    projectDetail ?? (selectedProject ? emptyProjectDetailData(selectedProject) : null);
  const selectedProjectDetailData =
    projectDetailBase === null
      ? null
      : {
          ...projectDetailBase,
          files: projectFiles,
          filesError: projectFilesError,
          filesLoading: isProjectFilesLoading,
          filesTotal: projectFilesTotal,
          filesTruncated: projectFilesTruncated,
          repositoryFileContent,
          repositoryFileContentError: repositoryFileContentError ?? undefined,
          repositoryFileContentLoading: isRepositoryFileContentLoading,
          repositoryFileSelectedPath: repositoryFileContentPath,
          repositoryFiles: projectGithubFiles?.files ?? [],
          repositoryFilesConnectUrl: githubRepositoryConnectUrl(),
          repositoryFilesError: projectGithubFilesError,
          repositoryFilesLoading: isProjectGithubFilesLoading,
          repositoryFilesMessage: isProjectGithubFilesLoading
            ? t("files.repositoryLoadingPeriod")
            : projectGithubFilesError ??
              projectGithubFiles?.message ??
              (projectDetailBase.project.repositoryUrl
                ? t("files.repositoryAccessRequired")
                : t("files.noRemote")),
          repositoryFilesRepository: projectGithubFiles?.repository,
          repositoryFilesStatus: projectGithubFiles?.status,
          repositoryFilesTruncated: projectGithubFiles?.truncated,
        };
  const repositoryConnector = isRepositoryConnectorOpen ? (
    <RepositoryConnector
      existingProjectIds={projectCatalog.map((project) => project.id)}
      onManualConnect={
        repositoryConnectorProjectId &&
        !isMockGithubUnlinkedProject(repositoryConnectorProjectId)
          ? (githubUrl) =>
              saveRepositoryConnection(repositoryConnectorProjectId, githubUrl)
          : repositoryConnectorProjectId === null
            ? async (githubUrl) => {
                const project = await createProjectFromRepository(githubUrl);
                navigateWorkspace({
                  activeDetailTab: "overview",
                  activeItem: "projects",
                  repositoryFileContentPath: null,
                  selectedProjectId: project.id,
                  selectedProjectRouteKey:
                    sanitizeProjectRouteKey(project.slug) ?? project.id,
                });
              }
            : undefined
      }
      onClose={closeRepositoryConnector}
      onFirstEvent={(event) => {
        void openFirstCapturedEvent(event);
      }}
      pollingEnabled
      repositoryAccessAvailable={currentUser?.github_repository_access === true}
      repositoryConnectUrl={githubRepositoryConnectUrl()}
      targetProjectId={repositoryConnectorProjectId ?? undefined}
      targetProjectName={repositoryConnectorProject?.name}
    />
  ) : null;
  const canUseAdmin = currentUser?.is_admin === true;
  const connectedRepositoryCount = projectCatalog.filter((project) =>
    Boolean(project.githubUrl),
  ).length;
  const latestProfileActivityLabel =
    projectCatalog[0]?.latestActivityLabel ?? t("project.noProjectActivity");
  const memoryCount = projectCatalog.reduce(
    (total, project) => total + project.memoryCount,
    0,
  );
  const pendingMemoryCount = projectCatalog.reduce(
    (total, project) => total + project.pendingMemoryCount,
    0,
  );
  const latestMemoryAt = projectCatalog
    .map((project) => project.latestMemoryAt)
    .filter((value): value is string => Boolean(value))
    .sort((first, second) => Date.parse(second) - Date.parse(first))[0];
  const latestMemoryLabel = latestMemoryAt
    ? formatRelativeTimestamp(latestMemoryAt) ?? t("common.recently")
    : t("project.noMemoryGenerated");
  const refreshWorkspaceAndAccount = () => {
    void Promise.all([loadWorkspace(), accountSettings.loadAccountOverview()]);
  };
  const pendingProjectRouteKey = selectedProjectRouteKey ?? selectedProjectId;
  const isResolvingProjectDetail =
    activeItem === "projects" &&
    projectDetail === null &&
    !projectDetailError &&
    !unresolvedProjectRouteKey &&
    Boolean(pendingProjectRouteKey);
  const isShowingProjectDetail =
    activeItem === "projects" &&
    (selectedProject !== null || projectDetail !== null || Boolean(pendingProjectRouteKey));
  const projectDetailRenderData =
    selectedProjectDetailData ?? emptyProjectDetailData(selectedProject);
  const reviewProjectCount = pendingReviewProjectCount(projectCatalog);
  const collectorHealth = getCollectorHealth(accountSettings.accountOverview);
  const isWorkspaceDataPending = isWorkspaceLoading || !hasLoadedWorkspaceData;
  const openCollectorUpdate = () => {
    if (!latestCollectorVersion) return;
    setCollectorUpdateTargetVersion(latestCollectorVersion);
    setCollectorVerificationStartedAt(null);
    setIsCollectorUpdateOpen(true);
  };
  const collectorLastSeen = collectorHealth.latestUsedAt
    ? formatRelativeTimestamp(collectorHealth.latestUsedAt) ?? t("common.recently")
    : t("common.recently");
  const collectorStatus = (() => {
    switch (collectorHealth.state) {
      case "checking":
        return {
          detail: t("collector.checkingStatus"),
          tone: "muted" as const,
          updateAvailable: false,
        };
      case "not-configured":
        return {
          detail: t("collector.notSetUp"),
          tone: "attention" as const,
          updateAvailable: false,
        };
      case "waiting":
        return {
          detail: t("settings.statusWaitingSync"),
          tone: "attention" as const,
          updateAvailable: false,
        };
      case "update-required":
        return {
          detail: t("collector.updateTo", {
            version: accountSettings.accountOverview?.latest_collector_version ?? "",
          }),
          tone: "attention" as const,
          updateAvailable: true,
        };
      case "delayed":
        return {
          detail: t("collector.delayed", { time: collectorLastSeen }),
          tone: "attention" as const,
          updateAvailable: collectorHealth.updateAvailable,
        };
      case "disconnected":
        return {
          detail: t("collector.disconnected", { time: collectorLastSeen }),
          tone: "danger" as const,
          updateAvailable: collectorHealth.updateAvailable,
        };
      default:
        return {
          detail: t("collector.synced", { time: collectorLastSeen }),
          tone: "connected" as const,
          updateAvailable: false,
        };
    }
  })();

  return (
    <div className="app-shell">
      {accountSettings.accountOverview &&
      !accountSettings.accountOverview.policy_consents.policy_accepted ? (
        <PolicyConsentModal
          isSaving={accountSettings.isAccountSaving}
          onAccept={accountSettings.acceptPolicies}
        />
      ) : null}
      <WorkspaceSidebar
        activeItem={activeItem}
        adminAlertCount={adminOverview?.action_summary?.unread ?? adminOverview?.action_items.length ?? 0}
        canUseAdmin={canUseAdmin}
        collectorStatus={collectorStatus}
        currentUser={currentUser}
        isReviewQueueOpen={isReviewQueueOpen}
        onLogout={logout}
        onOpenReviewQueue={openReviewQueue}
        onOpenCollectorUpdate={openCollectorUpdate}
        onSelectItem={selectSidebarItem}
        pendingReviewProjectCount={reviewProjectCount}
        savedProjectCount={bookmarkedProjects.length}
      />

      <CollectorUpdateModal
        currentVersion={currentCollectorVersion}
        defaultEnvironment={
          ["promty.org", "www.promty.org"].includes(window.location.hostname)
            ? "prod"
            : "dev"
        }
        isComplete={collectorUpdateComplete}
        isOpen={isCollectorUpdateOpen}
        isVerifying={collectorVerificationStartedAt !== null}
        latestVersion={collectorUpdateTargetVersion ?? latestCollectorVersion}
        onBeginVerification={() => setCollectorVerificationStartedAt(Date.now())}
        onClose={closeCollectorUpdate}
      />

      <main className={`page${isShowingProjectDetail ? " page-project-detail" : ""}`}>
        {authorizationNotice ? (
          <div className="workspace-notice" role="status">
            <span>{authorizationNotice}</span>
            <button
              aria-label={t("auth.dismissNotice")}
              onClick={() => {
                setAuthorizationNotice(null);
                const url = new URL(window.location.href);
                url.searchParams.delete("auth_error");
                window.history.replaceState(null, "", url);
              }}
              type="button"
            >
              <X aria-hidden="true" size={16} strokeWidth={1.5} />
            </button>
          </div>
        ) : null}
        {isShowingProjectDetail ? (
          <>
            {repositoryConnector}
            <ProjectDetailPage
              activityNavigation={activityNavigation}
              activeTab={activeDetailTab}
              data={projectDetailRenderData}
              errorMessage={
                unresolvedProjectRouteKey
                  ? t("project.notFoundDescription")
                  : projectDetailError
              }
              errorTitle={
                unresolvedProjectRouteKey ? t("project.notFoundTitle") : undefined
              }
              externalAiAllowed={
                accountSettings.accountOverview?.policy_consents.external_ai_allowed ?? false
              }
              externalAiProviders={
                accountSettings.accountOverview?.policy_consents.external_ai_providers ?? []
              }
              isExternalAiConsentSaving={accountSettings.isAccountSaving}
              isProjectResolving={isResolvingProjectDetail}
              isLoading={
                isResolvingProjectDetail ||
                (isProjectDetailLoading && projectDetail === null)
              }
              isBookmarkUpdating={
                selectedProject
                  ? bookmarkUpdatingProjectId === selectedProject.id
                  : false
              }
              isProjectMemoryGenerationActive={
                activeProjectId
                  ? activeProjectMemoryGenerationIds.has(activeProjectId) ||
                    (
                      projectDetailRenderData.project.id === activeProjectId &&
                      projectDetailRenderData.memory.latestBatch?.status ===
                        "generation_in_progress"
                    )
                  : false
              }
              isProjectMemoryGenerationDelayed={
                activeProjectId
                  ? delayedProjectMemoryGenerationIds.has(activeProjectId)
                  : false
              }
              isRefreshing={isProjectDetailLoading && projectDetail !== null}
              isShareCopied={
                selectedProject ? copiedProjectId === selectedProject.id : false
              }
              onActivityNavigationChange={selectActivityNavigation}
              onApproveProjectMemory={
                selectedProject
                  ? () => approveProjectMemoryForAgents(selectedProject.id)
                  : undefined
              }
              onGenerateProjectMemory={
                selectedProject
                  ? (reviewToken, excludedPromptEventIds) =>
                    generateProjectMemory(
                      selectedProject.id,
                      reviewToken,
                      excludedPromptEventIds,
                    )
                  : undefined
              }
              onLoadMemoryGenerationReview={
                selectedProject
                  ? () => fetchMemoryGenerationReview(selectedProject.id)
                  : undefined
              }
              onUpdateExternalAiConsent={accountSettings.updateExternalAiConsent}
              onConnectRepository={
                selectedProject
                  ? () => openRepositoryConnectorOverlay(selectedProject.id)
                  : undefined
              }
              onDeleteProject={selectedProject ? deleteSelectedProject : undefined}
              onDeletePromptActivity={
                selectedProject ? deleteSelectedPromptActivity : undefined
              }
              onDeleteSessionActivity={
                selectedProject ? deleteSelectedSessionActivity : undefined
              }
              onLoadMemoryArtifacts={
                activeProjectId
                  ? (limit) => loadProjectMemoryArtifacts(activeProjectId, limit)
                  : undefined
              }
              onOpenAllProjects={closeProjectDetail}
              onProjectSelect={selectedProject ? switchProjectDetail : undefined}
              onRepositoryFileSelect={activeProjectId ? selectRepositoryFile : undefined}
              onRetryRepositoryFiles={
                activeProjectId
                  ? () => {
                      void loadProjectGithubFiles(activeProjectId, undefined, true);
                    }
                  : undefined
              }
              onRetryTrackedFiles={
                activeProjectId
                  ? () => {
                      void loadProjectFiles(activeProjectId);
                    }
                  : undefined
              }
              onShareProject={
                selectedProject
                  ? () => {
                      void shareProject(selectedProject, activeDetailTab);
                    }
                  : undefined
              }
              onSaveProjectMetadata={selectedProject ? saveProjectMetadata : undefined}
              onSaveDescription={selectedProject ? saveProjectDescription : undefined}
              onTabChange={selectProjectDetailTab}
              onToggleBookmark={
                selectedProject
                  ? () => {
                      void toggleProjectBookmark(selectedProject);
                    }
                  : undefined
              }
              projectOptions={projectHeaderOptions}
              onRetry={
                activeProjectId && !unresolvedProjectRouteKey
                  ? () => {
                      void loadProjectDetail(activeProjectId, selectedProject);
                      void loadProjectFiles(activeProjectId);
                      void loadProjectGithubFiles(activeProjectId);
                      if (repositoryFileContentPath) {
                        void loadRepositoryFileContent(
                          activeProjectId,
                          repositoryFileContentPath,
                        );
                      }
                    }
                  : undefined
              }
            />
          </>
        ) : activeItem === "projects" ? (
          <ProjectsPage
            activeTitle={activeTitle}
            displayProjects={displayProjects}
            errorMessage={errorMessage}
            isEventsLoading={isWorkspaceDataPending}
            onClearSearch={() => setProjectSearchQuery("")}
            onOpenProject={openProjectDetail}
            onOpenReviewQueue={(projectId, returnFocusElement) =>
              openReviewQueue(returnFocusElement, projectId)
            }
            onOpenRepositoryConnector={() => openRepositoryConnectorOverlay(null)}
            onFirstEvent={(event) => {
              void openFirstCapturedEvent(event);
            }}
            onboardingPollingEnabled={!isRepositoryConnectorOpen}
            onRetry={loadWorkspace}
            onSearchChange={setProjectSearchQuery}
            onSortModeChange={setProjectSortMode}
            previewEmptyProjects={previewEmptyProjects}
            previewProjectLoading={previewProjectLoading}
            projectSearchQuery={projectSearchQuery}
            projectSortMode={projectSortMode}
            repositoryConnector={repositoryConnector}
            visibleProjects={visibleProjects}
          />
        ) : activeItem === "pinned" ? (
          <ProjectsPage
            activeTitle={activeTitle}
            displayProjects={bookmarkedProjects}
            errorMessage={errorMessage}
            isEventsLoading={isWorkspaceDataPending}
            onBrowseProjects={() => selectSidebarItem("projects")}
            onClearSearch={() => setProjectSearchQuery("")}
            onFirstEvent={(event) => {
              void openFirstCapturedEvent(event);
            }}
            onboardingPollingEnabled={false}
            onOpenProject={openProjectDetail}
            onOpenRepositoryConnector={() => openRepositoryConnectorOverlay(null)}
            onOpenReviewQueue={(projectId, returnFocusElement) =>
              openReviewQueue(returnFocusElement, projectId)
            }
            onRetry={loadWorkspace}
            onSearchChange={setProjectSearchQuery}
            onSortModeChange={setProjectSortMode}
            previewEmptyProjects={previewEmptyProjects}
            previewProjectLoading={previewProjectLoading}
            projectSearchQuery={projectSearchQuery}
            projectSortMode={projectSortMode}
            repositoryConnector={null}
            view="pinned"
            visibleProjects={visibleBookmarkedProjects}
          />
        ) : activeItem === "community" ? (
          <CommunityHubPage hideHeader={Boolean(selectedPublicProjectId || selectedPublicProfileId)}>
            <PublicProjectsPage
              embedded
              onSelectProject={(projectId, mode = "push") => {
                navigateWorkspace(
                  {
                    activeDetailTab: "overview",
                    activeItem: "community",
                    communityContent: "projects",
                    repositoryFileContentPath: null,
                    selectedCommunityFlowKey: null,
                    selectedProjectId: null,
                    selectedProjectRouteKey: null,
                    selectedPublicProfileId: null,
                    selectedPublicProjectId: projectId,
                  },
                  mode,
                );
              }}
              onSelectProfile={(profileId, mode = "push") => {
                navigateWorkspace(
                  {
                    activeDetailTab: "overview",
                    activeItem: "community",
                    communityContent: "projects",
                    repositoryFileContentPath: null,
                    selectedCommunityFlowKey: null,
                    selectedProjectId: null,
                    selectedProjectRouteKey: null,
                    selectedPublicProfileId: profileId,
                    selectedPublicProjectId: null,
                  },
                  mode,
                );
              }}
              onUnauthorized={handleUnauthorized}
              selectedProfileId={selectedPublicProfileId}
              selectedProjectId={selectedPublicProjectId}
            />
          </CommunityHubPage>
        ) : activeItem === "admin" && canUseAdmin ? (
          <>
            <header className="page-header">
              <div>
                <h1>{activeTitle}</h1>
              </div>
              <div className="page-actions">
                <span className="status-pill">{t("admin.only")}</span>
              </div>
            </header>

            <AdminDashboard
              errorMessage={adminError}
              isLoading={isAdminLoading}
              onOpenProject={openProjectDetail}
              onOpenActionItem={(item) => {
                const section = item.target?.split(":", 1)[0] || "overview";
                const href = `/admin?section=${encodeURIComponent(section)}`;
                if (!navigateToAppUrl(href)) {
                  window.location.assign(href);
                }
              }}
              onRefresh={() => {
                void loadAdminOverview();
              }}
              onUpdateActionItem={async (item, state) => {
                if (!item.key || !item.condition_hash) return;
                await updateAdminAlertState(item.key, item.condition_hash, state);
                await loadAdminOverview();
              }}
              overview={adminOverview}
            />
          </>
        ) : activeItem === "support" && currentUser ? (
          <SupportPage currentUser={currentUser} onUnauthorized={handleUnauthorized} />
        ) : (
          <AccountWorkspaceRoute
            account={accountSettings}
            activeItem={activeItem}
            activeTitle={activeTitle}
            apiUrl={API_URL}
            canUseAdmin={canUseAdmin}
            connectedRepositoryCount={connectedRepositoryCount}
            currentUser={currentUser}
            githubConnectUrl={githubRepositoryConnectUrl()}
            isEventsLoading={isWorkspaceDataPending}
            latestActivityLabel={latestProfileActivityLabel}
            latestMemoryLabel={latestMemoryLabel}
            memoryCount={memoryCount}
            onOpenRepositoryConnector={() => openRepositoryConnectorOverlay(null)}
            onOpenReviewQueue={(returnFocusElement) =>
              openReviewQueue(returnFocusElement)
            }
            onLogout={logout}
            onRefreshWorkspace={refreshWorkspaceAndAccount}
            pendingMemoryCount={pendingMemoryCount}
            projectCount={projectCatalog.length}
          />
        )}
      </main>
      {isReviewQueueOpen ? (
        <ReviewQueueDrawer
          onClose={() => {
            setIsReviewQueueOpen(false);
            setReviewQueueProjectId(null);
          }}
          onOpenProjectMemory={openProjectMemory}
          onProjectSummariesRefresh={replaceProjectSummaries}
          onUnauthorized={handleUnauthorized}
          projectFilterId={reviewQueueProjectId}
          projects={projectCatalog}
          returnFocusElement={reviewQueueReturnFocusRef.current}
          workspaceReady={hasLoadedWorkspaceData && !isWorkspaceLoading}
        />
      ) : null}
    </div>
  );
}
