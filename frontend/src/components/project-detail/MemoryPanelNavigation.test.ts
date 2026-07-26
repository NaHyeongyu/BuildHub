import { describe, expect, it } from "vitest";
import memoryPanelSource from "./MemoryPanel.tsx?raw";
import {
  MEMORY_PANEL_VIEWS,
  nextMemoryPanelView,
} from "./MemoryPanel";

describe("MemoryPanel navigation", () => {
  it("keeps history and current memory in the keyboard-operable tab cycle", () => {
    expect(MEMORY_PANEL_VIEWS).toEqual(["history", "current"]);
    expect(nextMemoryPanelView("history", "ArrowRight")).toBe("current");
    expect(nextMemoryPanelView("current", "ArrowRight")).toBe("history");
    expect(nextMemoryPanelView("history", "ArrowLeft")).toBe("current");
    expect(nextMemoryPanelView("current", "Home")).toBe("history");
    expect(nextMemoryPanelView("history", "End")).toBe("current");
  });

  it("renders the two memory views", () => {
    expect(memoryPanelSource).toContain('data-memory-view="history"');
    expect(memoryPanelSource).toContain('data-memory-view="current"');
  });

  it("uses deletion, not inclusion toggles, in the generation review", () => {
    expect(memoryPanelSource).toContain('setReviewBrowseMode("prompts")');
    expect(memoryPanelSource).toContain("onDeletePromptActivity");
    expect(memoryPanelSource).toContain("onDeleteSessionActivity");
    expect(memoryPanelSource).toContain("onGenerateProjectMemory(reviewToken, [])");
    expect(memoryPanelSource).toContain("toggleReviewSession");
    expect(memoryPanelSource).toContain("<ExpandableReviewText");
    expect(memoryPanelSource).not.toContain("setPromptIncluded");
    expect(memoryPanelSource).not.toContain("setSessionIncluded");
    expect(memoryPanelSource).not.toContain("setAllPromptsIncluded");
  });
});
