import { beforeEach, describe, expect, it, vi } from "vitest";


describe("project metadata API", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
  });

  it("sends a project name update in the metadata patch", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "project-id", name: "Renamed project" }), {
        headers: { "Content-Type": "application/json" },
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { updateProjectMetadata } = await import("./projects");

    await updateProjectMetadata("project-id", { name: "Renamed project" });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/projects/project-id/metadata",
      expect.objectContaining({
        body: JSON.stringify({ name: "Renamed project" }),
        credentials: "include",
        method: "PATCH",
      }),
    );
  });

  it("loads project detail, pending memory, and batch status in one request", async () => {
    const payload = {
      activities: [],
      files: [],
      memory: {
        drafts: [],
        latest_artifact_at: null,
        latest_batch: null,
        pending_ranges: [],
        recent_artifacts: [],
        total_artifacts: 0,
      },
      metrics: {},
      project: { id: "project-id" },
      prompt_activities: [],
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        headers: { "Content-Type": "application/json" },
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { fetchProjectDetailResources } = await import("./projects");

    await expect(fetchProjectDetailResources("project/id")).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/projects/project%2Fid/workspace?pending_limit=100",
      expect.objectContaining({
        credentials: "include",
      }),
    );
  });

  it("returns the GitHub tree validator with a fresh response", async () => {
    const payload = {
      available: true,
      files: [],
      message: null,
      repository: "acme/demo",
      status: "ok",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        headers: {
          "Content-Type": "application/json",
          ETag: '"tree-v1"',
          "X-Promty-Repository-Tree-Key": "repository-key",
        },
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { fetchProjectGithubFiles } = await import("./projects");

    await expect(fetchProjectGithubFiles("project-id")).resolves.toEqual({
      etag: '"tree-v1"',
      notModified: false,
      payload,
      treeKey: "repository-key",
    });
  });

  it("sends the cached GitHub tree validator and accepts a 304 response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(null, {
        headers: {
          ETag: '"tree-v1"',
          "X-Promty-Repository-Tree-Key": "repository-key",
        },
        status: 304,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { fetchProjectGithubFiles } = await import("./projects");

    await expect(
      fetchProjectGithubFiles(
        "project-id",
        undefined,
        '"tree-v1"',
        "repository-key",
      ),
    ).resolves.toEqual({
      etag: '"tree-v1"',
      notModified: true,
      payload: null,
      treeKey: "repository-key",
    });
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers).get("If-None-Match")).toBe('"tree-v1"');
    expect(
      new Headers(init.headers).get("X-Promty-Repository-Tree-Key"),
    ).toBe("repository-key");
  });

  it("backs off project memory status polling to a ten-second ceiling", async () => {
    const { projectMemoryPollDelayMs } = await import("./projects");

    expect([0, 1, 2, 3, 4, 20].map(projectMemoryPollDelayMs)).toEqual([
      2_000,
      4_000,
      8_000,
      10_000,
      10_000,
      10_000,
    ]);
  });

  it("sends the signed review and non-destructive prompt exclusions", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        batch_id: "batch-id",
        message: "No pending work",
        status: "no_pending",
      }), {
        headers: { "Content-Type": "application/json" },
        status: 202,
      }),
    );
    const storage = new Map<string, string>();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "11111111-1111-4111-8111-111111111111" });
    vi.stubGlobal("window", {
      clearTimeout,
      sessionStorage: {
        getItem: (key: string) => storage.get(key) ?? null,
        removeItem: (key: string) => storage.delete(key),
        setItem: (key: string, value: string) => storage.set(key, value),
      },
      setTimeout,
    });
    const { generateProjectMemory } = await import("./projects");

    await generateProjectMemory("project-id", "signed-review", ["prompt-private"]);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/projects/project-id/memory/generate",
      expect.objectContaining({
        body: JSON.stringify({
          excluded_prompt_event_ids: ["prompt-private"],
          idempotency_key: "11111111-1111-4111-8111-111111111111",
          review_token: "signed-review",
        }),
        method: "POST",
      }),
    );
  });

  it("deletes one prompt activity through its project-scoped endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const { deleteProjectPromptActivity } = await import("./projects");

    await deleteProjectPromptActivity("project/id", "prompt/id");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/projects/project%2Fid/prompt-activities/prompt%2Fid",
      expect.objectContaining({
        credentials: "include",
        method: "DELETE",
      }),
    );
  });

  it("deletes an entire session through its project-scoped endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const { deleteProjectSessionActivity } = await import("./projects");

    await deleteProjectSessionActivity("project/id", "session/id");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/projects/project%2Fid/sessions/session%2Fid",
      expect.objectContaining({
        credentials: "include",
        method: "DELETE",
      }),
    );
  });
});
