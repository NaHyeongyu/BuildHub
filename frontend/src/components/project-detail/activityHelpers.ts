import type { PromptActivityItem, PromptWorkGroup } from "./types";

export type WorkType = "brainstorming" | "work";
export type WorkTypeFilter = "all" | WorkType;

export const workTypeFilterOptions: Array<{ id: WorkTypeFilter; label: string }> = [
  { id: "all", label: "All" },
  { id: "brainstorming", label: "Brainstorming" },
  { id: "work", label: "Work" },
];

export function promptTitle(prompt: string) {
  return prompt.split(/\r?\n/)[0]?.trim().replace(/\s+/g, " ") || "Prompt flow";
}

function promptSubmittedTime(prompt: PromptActivityItem) {
  const submittedTime = Date.parse(prompt.submittedAt);
  return Number.isNaN(submittedTime) ? null : submittedTime;
}

export function sortPromptsForFlow(
  first: PromptActivityItem,
  second: PromptActivityItem,
) {
  const firstTime = promptSubmittedTime(first);
  const secondTime = promptSubmittedTime(second);

  if (firstTime !== null && secondTime !== null && firstTime !== secondTime) {
    return firstTime - secondTime;
  }

  return first.sequence - second.sequence;
}

export function sortPromptsForSelection(
  first: PromptActivityItem,
  second: PromptActivityItem,
) {
  const firstTime = promptSubmittedTime(first);
  const secondTime = promptSubmittedTime(second);

  if (firstTime !== null && secondTime !== null && firstTime !== secondTime) {
    return secondTime - firstTime;
  }

  return second.sequence - first.sequence;
}

export function workTypeForFiles(filesChanged: number): WorkType {
  return filesChanged > 0 ? "work" : "brainstorming";
}

export function workTypeLabel(workType: WorkType) {
  return workType === "work" ? "Work" : "Brainstorming";
}

export function workTypeCounts<T extends { filesChanged: number }>(
  items: T[],
): Record<WorkTypeFilter, number> {
  const counts: Record<WorkTypeFilter, number> = {
    all: items.length,
    brainstorming: 0,
    work: 0,
  };

  for (const item of items) {
    counts[workTypeForFiles(item.filesChanged)] += 1;
  }

  return counts;
}

export function buildPromptWorkGroups(
  prompts: PromptActivityItem[],
): PromptWorkGroup[] {
  const groups = new Map<string, PromptActivityItem[]>();
  for (const prompt of prompts) {
    const rootId = prompt.rootPromptEventId ?? prompt.id;
    const groupId = `${prompt.sessionId}:${rootId}`;
    const groupPrompts = groups.get(groupId) ?? [];
    groupPrompts.push(prompt);
    groups.set(groupId, groupPrompts);
  }

  return [...groups.entries()].map(([id, groupPrompts]) => ({
    id,
    prompts: groupPrompts.sort(sortPromptsForFlow),
  }));
}
