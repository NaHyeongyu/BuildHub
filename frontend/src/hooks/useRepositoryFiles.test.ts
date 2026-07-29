import { describe, expect, it } from "vitest";
import {
  REPOSITORY_TREE_CACHE_TTL_MS,
  repositoryTreeCacheIsFresh,
} from "./useRepositoryFiles";

describe("repository tree cache", () => {
  it("reuses a recent tree only for the same project", () => {
    const loadedAt = 10_000;

    expect(repositoryTreeCacheIsFresh("project-1", "project-1", loadedAt, 10_500))
      .toBe(true);
    expect(repositoryTreeCacheIsFresh("project-1", "project-2", loadedAt, 10_500))
      .toBe(false);
  });

  it("expires the tree after the bounded cache window", () => {
    const loadedAt = 10_000;

    expect(
      repositoryTreeCacheIsFresh(
        "project-1",
        "project-1",
        loadedAt,
        loadedAt + REPOSITORY_TREE_CACHE_TTL_MS,
      ),
    ).toBe(false);
  });
});
