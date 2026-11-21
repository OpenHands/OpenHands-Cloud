import { expect, test, type Page, type TestInfo } from "@playwright/test";

import { deleteAppConversation } from "../utils/app-conversations";
import { failedRunScript, singleFileTarGz } from "../utils/automation-fixtures";
import {
  createScriptAutomation,
  deleteAutomation,
  deleteUpload,
  isAutomationServiceReachable,
  listRuns,
  uploadTarball,
  waitForRunFailed,
} from "../utils/automations";
import { Cleanup } from "../utils/cleanup";
import { newUserEmail, runUser, superAdminApiKey } from "../utils/config";
import {
  createOrg,
  deleteOrg,
  e2eOrgName,
  e2eTimestamp,
  provisionUser,
} from "../utils/org-management";

/**
 * Automation journey: a run that fails on purpose must end FAILED and say why.
 *
 * The automation is a deterministic script (no LLM, repository or credential
 * of the target) that raises a known, run-scoped error and reports ``FAILED``
 * with it through the completion callback. This is the failure-path
 * counterpart of ``015-automation-completed-journey``: here ``FAILED`` is the
 * expected end result and ``COMPLETED`` (or ``CANCELLED``, ``SKIPPED``, or a
 * callback that never arrives) fails the test. It passes only when
 *
 *  - the run reaches ``FAILED`` within the bounded wait, with the expected
 *    error as its ``error_detail``,
 *  - the run page shows the run as failed, with the error in the run row,
 *  - the Logs dialog states ``FAILED`` and the same error as the system issue,
 *    and
 *  - the run links no conversation, in the API and in the page, because the
 *    script fails before it starts one.
 *
 * Setup uses the REST API (new-user role only, like ``011-automations``): a
 * fresh org with the New User as owner, an uploaded script and an automation
 * on a cron schedule that never ticks. ``afterAll`` deletes all of it, newest
 * first.
 */

const RUN_FAILED_TIMEOUT_MS = 4 * 60 * 1000;

async function attachScreenshot(
  page: Page,
  testInfo: TestInfo,
  name: string,
): Promise<void> {
  await testInfo.attach(name, {
    body: await page.screenshot(),
    contentType: "image/png",
  });
}

/**
 * Make the sidebar's organization switcher show ``orgName``. The actor owns the
 * automation through the new org, so the page can only show it from there; a
 * session that already is in that org is left alone.
 */
async function selectOrganization(page: Page, orgName: string): Promise<void> {
  const switcher = page.getByRole("combobox").first();
  await expect(switcher).toBeVisible();
  if ((await switcher.getAttribute("title")) !== orgName) {
    await switcher.click();
    await page.getByRole("option", { name: orgName }).click();
  }
  await expect(switcher).toHaveAttribute("title", orgName);
}

test.describe("automation journey: FAILED terminal status", () => {
  const cleanup = new Cleanup();

  test.afterAll(async () => {
    await cleanup.run();
  });

  test("a dispatched deterministic automation ends FAILED with its expected diagnostic and no conversation", async ({
    baseURL,
    page,
  }, testInfo) => {
    test.skip(runUser(testInfo) !== "new-user", "new-user role only");
    test.setTimeout(RUN_FAILED_TIMEOUT_MS + 120_000);

    const base = baseURL || "";
    const adminKey = superAdminApiKey();
    test.skip(
      !(await isAutomationServiceReachable(base, adminKey)),
      "Automation service not reachable on this cluster",
    );

    const timestamp = e2eTimestamp();
    const suffix = `qa-${timestamp}`;
    const expectedError =
      `QA_EXPECTED_FAILURE ${suffix}: simulated upstream returned HTTP 503; ` +
      "check the upstream service status and run the automation again";

    const orgName = e2eOrgName("automation failed", timestamp);
    const automationName = `qa-failed-${suffix}`;
    const { actorKey, automationId } =
      await test.step("Create the isolated org, actor and automation", async () => {
        const org = await createOrg(base, adminKey, {
          name: orgName,
          contact_name: "Testy Tester",
          contact_email: "e2e_test@openhands.org",
        });
        cleanup.defer(`delete org ${org.id}`, () =>
          deleteOrg(base, adminKey, org.id),
        );
        const actor = await provisionUser(base, adminKey, org.id, {
          email: newUserEmail(),
          role: "owner",
        });

        const tarballPath = await uploadTarball(
          base,
          actor.api_key,
          `qa-failed-${suffix}`,
          singleFileTarGz("main.py", failedRunScript(expectedError)),
        );
        cleanup.defer(`delete upload ${tarballPath}`, () =>
          deleteUpload(base, actor.api_key, tarballPath),
        );
        const automation = await createScriptAutomation(base, actor.api_key, {
          name: automationName,
          // 3 AM on 29 February: the schedule never ticks before cleanup.
          trigger: { type: "cron", schedule: "0 3 29 2 *", timezone: "UTC" },
          tarball_path: tarballPath,
          entrypoint: "python3 main.py",
          timeout: 180,
        });
        cleanup.defer(`delete automation ${automation.id}`, () =>
          deleteAutomation(base, actor.api_key, automation.id),
        );
        expect(automation.enabled).toBe(true);
        return { actorKey: actor.api_key, automationId: automation.id };
      });

    await test.step("Dispatch the automation with Run now", async () => {
      await page.goto("canvas/automations");
      await selectOrganization(page, orgName);
      await page.getByRole("link", { name: automationName }).first().click();
      await expect(page).toHaveURL(new RegExp(`/automations/${automationId}`));
      await expect(page.getByTestId("automation-detail-header")).toContainText(
        automationName,
      );
      await expect(page.getByRole("button", { name: "Run now" })).toBeVisible();
      await attachScreenshot(page, testInfo, "automation-before-dispatch");
      await page.getByRole("button", { name: "Run now" }).click();
      await expect(page.getByText("Automation dispatched")).toBeVisible();
      await attachScreenshot(page, testInfo, "automation-dispatched");
      await expect
        .poll(async () => (await listRuns(base, actorKey, automationId)).length)
        .toBe(1);
    });

    const run =
      await test.step("Wait for the expected FAILED terminal status", () =>
        waitForRunFailed(base, actorKey, automationId, RUN_FAILED_TIMEOUT_MS));

    await test.step("The run record carries the expected diagnostic and no conversation", () => {
      const linkedId = run.conversation_id;
      if (linkedId) {
        cleanup.defer(`delete conversation ${linkedId}`, () =>
          deleteAppConversation(base, actorKey, linkedId),
        );
      }
      expect(run.status).toBe("FAILED");
      expect(run.trigger_source).toBe("manual");
      expect(run.error_detail).toBe(expectedError);
      expect(
        run.conversation_id ?? null,
        "the script fails before it starts a conversation, so the run must link none",
      ).toBeNull();
    });

    await test.step("The run page shows the run as failed with its diagnostic", async () => {
      await page.reload();
      await expect(page.getByTestId("run-status-icon-failed")).toHaveCount(1);
      await expect(page.getByTestId("run-status-icon-completed")).toHaveCount(
        0,
      );
      const diagnostic = page
        .getByTestId("automation-activity-log")
        .getByText(expectedError);
      await expect(diagnostic).toBeVisible();
      await diagnostic.scrollIntoViewIfNeeded();
      await expect(
        page.getByRole("link", { name: /View conversation for run/ }),
      ).toHaveCount(0);
      await attachScreenshot(page, testInfo, "run-failed-with-diagnostic");
    });

    await test.step("The Logs dialog states FAILED and the same error as the system issue", async () => {
      await page.getByRole("button", { name: /logs/i }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toContainText("FAILED");
      await expect(dialog).toContainText(`Error: ${expectedError}`);
      await expect(dialog).not.toContainText("No system issues reported.");
      await attachScreenshot(page, testInfo, "run-failed-logs-dialog");
    });
  });
});
