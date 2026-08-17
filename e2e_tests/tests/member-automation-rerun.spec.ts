import { expect, test } from "@playwright/test";
import { runUser } from "../utils/config";

test.beforeEach(({ page: _page }, testInfo) => {
  test.skip(
    runUser(testInfo) !== "returning",
    "Requires the stable returning-user fixture.",
  );
});

test.use({
  screenshot: "off",
  trace: "off",
  video: "off",
});

test("member reruns a configured existing automation to successful completion", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const automationId = process.env.TEST_AUTOMATION_ID;
  if (!automationId) {
    throw new Error(
      "TEST_AUTOMATION_ID is required and must identify an enabled deterministic no-LLM automation with at least one prior run.",
    );
  }
  const encodedAutomationId = encodeURIComponent(automationId);

  const automationResponse = await page.request.get(
    `/api/automation/v1/${encodedAutomationId}`,
  );
  if (!automationResponse.ok()) {
    throw new Error(
      `TEST_AUTOMATION_ID is not accessible to the authenticated member (HTTP ${automationResponse.status()}).`,
    );
  }
  const automation = (await automationResponse.json()) as {
    name: string;
    enabled: boolean;
  };
  if (!automation.enabled) {
    throw new Error("TEST_AUTOMATION_ID must identify an enabled automation.");
  }

  const priorRunsResponse = await page.request.get(
    `/api/automation/v1/${encodedAutomationId}/runs`,
  );
  expect(priorRunsResponse.ok()).toBe(true);
  const priorRuns = (await priorRunsResponse.json()) as {
    runs: Array<{ id: string }>;
  };
  if (priorRuns.runs.length === 0) {
    throw new Error(
      "TEST_AUTOMATION_ID must have at least one prior run so this test exercises rerun behavior.",
    );
  }
  const priorRunIds = new Set(priorRuns.runs.map(({ id }) => id));

  await page.goto(`/automations/${encodedAutomationId}`);
  await expect(
    page.getByRole("heading", { name: automation.name }),
  ).toBeVisible();

  const dispatchResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/automation/v1/${automationId}/dispatch`) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Run now" }).click();
  const dispatchResponse = await dispatchResponsePromise;
  expect(dispatchResponse.status()).toBe(201);
  const dispatchedRun = (await dispatchResponse.json()) as { id: string };
  expect(priorRunIds.has(dispatchedRun.id)).toBe(false);

  await expect
    .poll(
      async () => {
        const runsResponse = await page.request.get(
          `/api/automation/v1/${encodedAutomationId}/runs`,
        );
        if (!runsResponse.ok()) {
          return `HTTP_${runsResponse.status()}`;
        }
        const body = (await runsResponse.json()) as {
          runs: Array<{
            id: string;
            status: string;
            error_detail: string | null;
          }>;
        };
        const run = body.runs.find(({ id }) => id === dispatchedRun.id);
        if (run?.status === "FAILED") {
          throw new Error(
            `Configured automation rerun failed: ${run.error_detail ?? "no error detail"}`,
          );
        }
        return run?.status ?? "NOT_FOUND";
      },
      {
        message:
          "The configured automation rerun did not complete successfully.",
        intervals: [1_000, 2_000, 5_000],
        timeout: 120_000,
      },
    )
    .toBe("COMPLETED");
});
