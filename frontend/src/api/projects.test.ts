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

  it("loads a bounded context graph with a trimmed search query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        edges: [],
        facets: {},
        nodes: [],
        query: "authentication flow",
        safety_notice: "Only captured context is shown.",
        truncated: false,
      }), {
        headers: { "Content-Type": "application/json" },
        status: 200,
      }),
    );
    const controller = new AbortController();
    vi.stubGlobal("fetch", fetchMock);
    const { fetchProjectContextGraph } = await import("./projects");

    await fetchProjectContextGraph("project/id", {
      limit: 80,
      query: "  authentication flow  ",
      signal: controller.signal,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/projects/project%2Fid/context-graph?limit=40&view=knowledge&q=authentication+flow",
      expect.objectContaining({
        credentials: "include",
        signal: controller.signal,
      }),
    );
  });

  it("updates a project-scoped knowledge node review", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          node_id: "knowledge:artifact:candidate",
          review_state: "confirmed",
          reviewed_at: "2026-07-24T01:00:00Z",
        }),
        {
          headers: { "Content-Type": "application/json" },
          status: 200,
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { reviewProjectContextGraphNode } = await import("./projects");

    await reviewProjectContextGraphNode(
      "project/id",
      "knowledge:artifact:candidate",
      "confirm",
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8011/api/projects/project%2Fid/context-graph/nodes/knowledge%3Aartifact%3Acandidate/review",
      expect.objectContaining({
        body: JSON.stringify({ action: "confirm" }),
        credentials: "include",
        method: "PATCH",
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
