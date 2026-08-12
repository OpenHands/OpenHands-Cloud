import { expect, test } from "@playwright/test";
import path from "path";

const authState = path.resolve(import.meta.dirname, "../fixtures/auth.json");

test.use({
  storageState: authState,
  screenshot: "off",
  trace: "off",
  video: "off",
});

test("member creates a no-LLM automation, runs it successfully, and deletes it", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const tarballUrl = process.env.TEST_NO_LLM_AUTOMATION_TARBALL_URL;
  if (!tarballUrl) {
    throw new Error(
      "TEST_NO_LLM_AUTOMATION_TARBALL_URL is required and must reference a deterministic tarball that reports COMPLETED without invoking an LLM.",
    );
  }
  if (!/^https?:\/\//.test(tarballUrl)) {
    throw new Error(
      "TEST_NO_LLM_AUTOMATION_TARBALL_URL must be an HTTP(S) URL accessible from the automation sandbox.",
    );
  }
  const entrypoint =
    process.env.TEST_NO_LLM_AUTOMATION_ENTRYPOINT ?? "python3 main.py";
  const automationName = `e2e-member-no-llm-${Date.now()}`;
  let automationId: string | null = null;

  try {
    const createResponse = await page.request.post("/api/automation/v1", {
      data: {
        name: automationName,
        trigger: {
          type: "event",
          source: "e2e-member-test",
          on: `manual.${Date.now()}`,
        },
        tarball_path: tarballUrl,
        entrypoint,
        timeout: 120,
        keep_alive: false,
      },
    });
    if (!createResponse.ok()) {
      throw new Error(
        `No-LLM automation creation failed with HTTP ${createResponse.status()}.`,
      );
    }
    const automation = (await createResponse.json()) as { id: string };
    automationId = automation.id;

    await page.goto(`/automations/${encodeURIComponent(automationId)}`);
    await expect(
      page.getByRole("heading", { name: automationName }),
    ).toBeVisible();

    const dispatchResponsePromise = page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(`/api/automation/v1/${automationId}/dispatch`) &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Run now" }).click();
    const dispatchResponse = await dispatchResponsePromise;
    expect(dispatchResponse.status()).toBe(201);
    const dispatchedRun = (await dispatchResponse.json()) as { id: string };

    await expect
      .poll(
        async () => {
          const runsResponse = await page.request.get(
            `/api/automation/v1/${encodeURIComponent(automationId!)}/runs`,
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
              `No-LLM automation run failed: ${run.error_detail ?? "no error detail"}`,
            );
          }
          return run?.status ?? "NOT_FOUND";
        },
        {
          message: "The no-LLM automation run did not complete successfully.",
          intervals: [1_000, 2_000, 5_000],
          timeout: 120_000,
        },
      )
      .toBe("COMPLETED");
  } finally {
    if (automationId) {
      const deleteResponse = await page.request.delete(
        `/api/automation/v1/${encodeURIComponent(automationId)}`,
      );
      expect([204, 404]).toContain(deleteResponse.status());
    }
  }
});
