import { describe, expect, it } from "vitest";
import { buildPromptWorkGroups } from "./activityHelpers";
import type { PromptActivityItem } from "./types";


function prompt(
  id: string,
  sequence: number,
  overrides: Partial<PromptActivityItem> = {},
): PromptActivityItem {
  return {
    fileChanges: [],
    filesChanged: 0,
    id,
    model: "codex",
    prompt: id,
    sequence,
    sessionId: "session-1",
    submittedAt: `2026-07-25T00:0${sequence}:00+00:00`,
    ...overrides,
  };
}


describe("buildPromptWorkGroups", () => {
  it("keeps an interrupted prompt and its continuation in one chronological work item", () => {
    const groups = buildPromptWorkGroups([
      prompt("prompt-2", 2, {
        continuationOf: "prompt-1",
        rootPromptEventId: "prompt-1",
        submissionContext: "during_output",
      }),
      prompt("prompt-1", 1, { rootPromptEventId: "prompt-1" }),
      prompt("prompt-3", 3, { rootPromptEventId: "prompt-3" }),
    ]);

    expect(groups).toHaveLength(2);
    expect(groups[0].id).toBe("session-1:prompt-1");
    expect(groups[0].prompts.map((item) => item.id)).toEqual([
      "prompt-1",
      "prompt-2",
    ]);
    expect(groups[1].prompts.map((item) => item.id)).toEqual(["prompt-3"]);
  });
});
