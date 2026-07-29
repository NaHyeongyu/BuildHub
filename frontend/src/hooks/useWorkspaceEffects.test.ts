import { describe, expect, it } from "vitest";
import { adminOverviewPollingEnabled } from "./useWorkspaceEffects";

describe("admin overview polling", () => {
  it("runs only for an authenticated administrator viewing the admin screen", () => {
    expect(adminOverviewPollingEnabled("admin", "authenticated", true)).toBe(true);
    expect(adminOverviewPollingEnabled("projects", "authenticated", true)).toBe(false);
    expect(adminOverviewPollingEnabled("admin", "loading", true)).toBe(false);
    expect(adminOverviewPollingEnabled("admin", "authenticated", false)).toBe(false);
  });
});
