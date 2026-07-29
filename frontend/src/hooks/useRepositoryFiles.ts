import { useRef, useState } from "react";
import { UnauthorizedError } from "../api/client";
import {
  fetchProjectGithubFiles,
  fetchRepositoryFileContent,
} from "../api/projects";
import type { RepositoryFileContent } from "../components/project-detail";
import {
  projectGithubFilesFromApi,
  repositoryFileContentFromApi,
} from "../workspace/projectDetailMappers";
import type { ProjectGithubFilesState } from "../workspace/types";

type UseRepositoryFilesOptions = {
  initialPath: string | null;
  onUnauthorized: () => void;
};

export const REPOSITORY_TREE_CACHE_TTL_MS = 60_000;

export function repositoryTreeCacheIsFresh(
  cachedProjectId: string | null,
  projectId: string,
  loadedAt: number,
  now = Date.now(),
) {
  return (
    cachedProjectId === projectId &&
    loadedAt > 0 &&
    now - loadedAt < REPOSITORY_TREE_CACHE_TTL_MS
  );
}

export function useRepositoryFiles({
  initialPath,
  onUnauthorized,
}: UseRepositoryFilesOptions) {
  const [projectGithubFiles, setProjectGithubFiles] =
    useState<ProjectGithubFilesState | null>(null);
  const [projectGithubFilesError, setProjectGithubFilesError] = useState<string | null>(
    null,
  );
  const [isProjectGithubFilesLoading, setIsProjectGithubFilesLoading] = useState(false);
  const [repositoryFileContent, setRepositoryFileContent] =
    useState<RepositoryFileContent | null>(null);
  const [repositoryFileContentError, setRepositoryFileContentError] =
    useState<string | null>(null);
  const [repositoryFileContentPath, setRepositoryFileContentPath] =
    useState<string | null>(initialPath);
  const [isRepositoryFileContentLoading, setIsRepositoryFileContentLoading] =
    useState(false);
  const projectGithubFilesCacheRef = useRef<{
    etag: string | null;
    loadedAt: number;
    projectId: string;
    treeKey: string | null;
    value: ProjectGithubFilesState;
  } | null>(null);
  const repositoryFilesGenerationRef = useRef(0);

  const clearRepositoryFileContent = (path: string | null = null) => {
    setRepositoryFileContent(null);
    setRepositoryFileContentError(null);
    setRepositoryFileContentPath(path);
    setIsRepositoryFileContentLoading(false);
  };

  const clearRepositoryFileContentState = () => {
    setRepositoryFileContent(null);
    setRepositoryFileContentError(null);
    setIsRepositoryFileContentLoading(false);
  };

  const clearRepositoryBrowserState = () => {
    repositoryFilesGenerationRef.current += 1;
    projectGithubFilesCacheRef.current = null;
    setProjectGithubFiles(null);
    setProjectGithubFilesError(null);
    setIsProjectGithubFilesLoading(false);
    clearRepositoryFileContentState();
  };

  const clearRepositoryFiles = () => {
    repositoryFilesGenerationRef.current += 1;
    projectGithubFilesCacheRef.current = null;
    setProjectGithubFiles(null);
    setProjectGithubFilesError(null);
    setIsProjectGithubFilesLoading(false);
    clearRepositoryFileContent(null);
  };

  const loadProjectGithubFiles = async (
    projectId: string,
    signal?: AbortSignal,
    force = false,
  ) => {
    const cached = projectGithubFilesCacheRef.current;
    if (
      !force &&
      cached &&
      repositoryTreeCacheIsFresh(
        cached.projectId,
        projectId,
        cached.loadedAt,
      )
    ) {
      return;
    }
    const requestGeneration = repositoryFilesGenerationRef.current;
    setIsProjectGithubFilesLoading(true);
    setProjectGithubFilesError(null);
    try {
      const result = await fetchProjectGithubFiles(
        projectId,
        signal,
        cached?.projectId === projectId ? cached.etag : null,
        cached?.projectId === projectId ? cached.treeKey : null,
      );
      if (
        signal?.aborted ||
        requestGeneration !== repositoryFilesGenerationRef.current
      ) {
        return;
      }
      const currentCache = projectGithubFilesCacheRef.current;
      if (result.notModified && currentCache?.projectId === projectId) {
        projectGithubFilesCacheRef.current = {
          ...currentCache,
          etag: result.etag ?? currentCache.etag,
          loadedAt: Date.now(),
          treeKey: result.treeKey ?? currentCache.treeKey,
        };
        setProjectGithubFiles(currentCache.value);
        return;
      }
      if (!result.payload) {
        throw new Error("GitHub files request returned no repository tree");
      }
      const value = projectGithubFilesFromApi(result.payload);
      setProjectGithubFiles(value);
      projectGithubFilesCacheRef.current = {
        etag: result.etag,
        loadedAt: Date.now(),
        projectId,
        treeKey: result.treeKey,
        value,
      };
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }
      if (requestGeneration !== repositoryFilesGenerationRef.current) {
        return;
      }
      if (error instanceof UnauthorizedError) {
        onUnauthorized();
        setProjectGithubFiles(null);
        return;
      }
      setProjectGithubFilesError(
        error instanceof Error ? error.message : "GitHub files request failed",
      );
    } finally {
      if (
        !signal?.aborted &&
        requestGeneration === repositoryFilesGenerationRef.current
      ) {
        setIsProjectGithubFilesLoading(false);
      }
    }
  };

  const loadRepositoryFileContent = async (
    projectId: string,
    path: string,
    signal?: AbortSignal,
  ) => {
    setRepositoryFileContentPath(path);
    setRepositoryFileContent(null);
    setRepositoryFileContentError(null);
    setIsRepositoryFileContentLoading(true);
    try {
      const payload = await fetchRepositoryFileContent(projectId, path, signal);
      setRepositoryFileContent(repositoryFileContentFromApi(payload));
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }
      if (error instanceof UnauthorizedError) {
        onUnauthorized();
        setRepositoryFileContent(null);
        return;
      }
      setRepositoryFileContentError(
        error instanceof Error ? error.message : "GitHub file request failed",
      );
    } finally {
      if (!signal?.aborted) {
        setIsRepositoryFileContentLoading(false);
      }
    }
  };

  return {
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
  };
}
