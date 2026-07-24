import { expect, test } from "@playwright/test";


const API_ORIGIN = "http://127.0.0.1:8011";

test("memory generation resumes from server state after a page reload", async ({
  page,
}) => {
  const repositoryName = `memory-resume-${Date.now()}`;
  await page.goto("/app");
  const project = await page.evaluate(async ({ repositoryName }) => {
    const response = await fetch("http://127.0.0.1:8011/api/projects", {
      body: JSON.stringify({
        github_url: `https://github.com/promty/${repositoryName}`,
      }),
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      method: "POST",
    });
    if (!response.ok) {
      throw new Error(`Project creation failed: ${response.status}`);
    }
    return response.json() as Promise<{ id: string }>;
  }, { repositoryName });

  let latestBatchRequestCount = 0;
  let generationCompleted = false;
  await page.route(
    `**/api/projects/${project.id}/memory/batches/latest`,
    async (route) => {
      latestBatchRequestCount += 1;
      await route.fulfill({
        json: {
          batch_id: "4b4b7e66-370f-4b9f-b378-64c186e57a67",
          error: null,
          message: generationCompleted
            ? "Project Memory was updated from the captured project work."
            : "Project Memory is being updated.",
          retryable: false,
          status: generationCompleted
            ? "memory_generated"
            : "generation_in_progress",
        },
        status: 200,
      });
    },
  );
  await page.route(
    `**/api/projects/${project.id}/memory/pending**`,
    (route) => route.fulfill({ json: [], status: 200 }),
  );

  try {
    await page.goto(`/app?project=${project.id}&tab=memory`);
    const updatingButton = page.getByRole("button", {
      name: "Updating project memory",
    });
    await expect(updatingButton).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Creating project memory").first()).toBeVisible();

    await page.reload();
    await expect(updatingButton).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Creating project memory").first()).toBeVisible();

    const requestCountBeforeCompletion = latestBatchRequestCount;
    generationCompleted = true;
    await expect
      .poll(() => latestBatchRequestCount, { timeout: 10_000 })
      .toBeGreaterThan(requestCountBeforeCompletion);
    await expect(updatingButton).toHaveCount(0, { timeout: 10_000 });
    await expect(page.getByText("No project memory yet.")).toBeVisible();
  } finally {
    await page.request.delete(`${API_ORIGIN}/api/projects/${project.id}`);
  }
});
