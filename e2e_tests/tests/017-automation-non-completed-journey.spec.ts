import { expect, test } from "@playwright/test";

import {
  missingCallbackRunScript,
  singleFileTarGz,
  stalledRunScript,
} from "../utils/automation-fixtures";
import {
  attachScreenshot,
  dispatchWithRunNow,
  selectOrganization,
} from "../utils/automation-ui";
import {
  cancelActiveRuns,
  captureSuccessAssertionFailure,
  createScriptAutomation,
  deleteAutomation,
  deleteUpload,
  isAutomationServiceReachable,
  listRuns,
  uploadTarball,
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
 * Automation journey: a run that does not end COMPLETED must not pass the
 * success path.
 *
 * Each outcome comes from a deterministic script automation (no LLM,
 * repository or credential of the target), dispatched with the automation
 * page's "Run now" button:
 *
 *  - timeout: the script stalls, so the run is still ``RUNNING`` when the
 *    bounded wait ends,
 *  - cancellation: the same stall, cancelled from the Home page's "Cancel
 *    run" menu item, and
 *  - missing callback: the script exits without calling the completion
 *    callback, so the service never hears an outcome.
 *
 * For each, the success assertion (``waitForRunCompleted``) must fail within
 * its bound and name the final status or the timeout together with the
 * lifecycle it observed, and the run page must not show the run as completed.
 * A run the test abandons while it is active is cancelled, so nothing is left
 * running.
 *
 * Setup uses the REST API (new-user role only, like ``011-automations``): one
 * fresh org with the New User as owner, then an uploaded script and an
 * automation on a cron schedule that never ticks for each outcome. ``afterAll``
 * deletes all of it, newest first.
 */

const SUCCESS_WAIT_BOUND_MS = 30_000;
// The bound plus one poll interval and the request in flight when it expires.
const SUCCESS_WAIT_SLACK_MS = 15_000;
const STALL_SECONDS = 120;
const AUTOMATION_TIMEOUT_S = 150;
// A run is RUNNING while its sandbox starts; the script starts after that.
const SCRIPT_START_WAIT_MS = 150_000;

test.describe("automation journey: non-COMPLETED outcomes", () => {
  const cleanup = new Cleanup();
  const suffix = `qa-${e2eTimestamp()}`;
  const orgName = e2eOrgName("automation non-completed");
  let actorKey = "";

  test.beforeAll(async ({ baseURL }, testInfo) => {
    if (runUser(testInfo) !== "new-user") {
      return;
    }
    const base = baseURL || "";
    const adminKey = superAdminApiKey();
    if (!(await isAutomationServiceReachable(base, adminKey))) {
      return;
    }
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
    actorKey = actor.api_key;
  });

  test.afterAll(async () => {
    await cleanup.run();
  });

  test.beforeEach(async ({ baseURL }, testInfo) => {
    test.skip(runUser(testInfo) !== "new-user", "new-user role only");
    test.skip(
      !(await isAutomationServiceReachable(baseURL || "", superAdminApiKey())),
      "Automation service not reachable on this cluster",
    );
  });

  /** Upload `script`, create a never-ticking script automation for it and register cleanup. */
  async function createAutomation(
    base: string,
    label: string,
    script: string,
  ): Promise<{ id: string; name: string }> {
    const name = `qa-${label}-${suffix}`;
    const tarballPath = await uploadTarball(
      base,
      actorKey,
      name,
      singleFileTarGz("main.py", script),
    );
    cleanup.defer(`delete upload ${tarballPath}`, () =>
      deleteUpload(base, actorKey, tarballPath),
    );
    const automation = await createScriptAutomation(base, actorKey, {
      name,
      // 3 AM on 29 February: the schedule never ticks before cleanup.
      trigger: { type: "cron", schedule: "0 3 29 2 *", timezone: "UTC" },
      tarball_path: tarballPath,
      entrypoint: "python3 main.py",
      timeout: AUTOMATION_TIMEOUT_S,
    });
    cleanup.defer(`delete automation ${automation.id}`, () =>
      deleteAutomation(base, actorKey, automation.id),
    );
    cleanup.defer(`cancel active runs of ${automation.id}`, () =>
      cancelActiveRuns(base, actorKey, automation.id),
    );
    expect(automation.enabled).toBe(true);
    return { id: automation.id, name };
  }

  /** Wait until the service has started the run's script, so the wait below observes the script and not the sandbox starting. */
  async function waitForScriptStart(
    base: string,
    automationId: string,
  ): Promise<void> {
    await expect
      .poll(
        async () =>
          (await listRuns(base, actorKey, automationId))[0]?.bash_command_id ??
          null,
        {
          message: "the run's script must start",
          timeout: SCRIPT_START_WAIT_MS,
        },
      )
      .not.toBeNull();
  }

  test("a stalled run times out the success wait and reports where it stopped", async ({
    baseURL,
    page,
  }, testInfo) => {
    test.setTimeout(SCRIPT_START_WAIT_MS + SUCCESS_WAIT_BOUND_MS + 90_000);
    const base = baseURL || "";
    const automation = await test.step("Create the stalled automation", () =>
      createAutomation(base, "timeout", stalledRunScript(STALL_SECONDS)));

    await test.step("Dispatch it with Run now and wait until its script runs", async () => {
      await dispatchWithRunNow(page, testInfo, orgName, automation);
      await waitForScriptStart(base, automation.id);
    });

    const failure =
      await test.step("The success wait gives up within its bound", () =>
        captureSuccessAssertionFailure(
          base,
          actorKey,
          automation.id,
          SUCCESS_WAIT_BOUND_MS,
        ));

    await test.step("The failure reports the timeout and the lifecycle observed", () => {
      expect(failure.message).toContain(
        `did not reach a terminal status within ${SUCCESS_WAIT_BOUND_MS}ms`,
      );
      expect(failure.message).toMatch(/\(lifecycle: (PENDING -> )?RUNNING\)$/);
      expect(failure.elapsedMs).toBeGreaterThanOrEqual(SUCCESS_WAIT_BOUND_MS);
      expect(failure.elapsedMs).toBeLessThan(
        SUCCESS_WAIT_BOUND_MS + SUCCESS_WAIT_SLACK_MS,
      );
    });

    await test.step("The run page shows the run still running, not completed", async () => {
      await page.reload();
      await expect(page.getByTestId("run-status-icon-running")).toHaveCount(1);
      await expect(page.getByTestId("run-status-icon-completed")).toHaveCount(
        0,
      );
      await page
        .getByTestId("automation-activity-log")
        .locator("time")
        .first()
        .scrollIntoViewIfNeeded();
      await attachScreenshot(page, testInfo, "timeout-run-still-running");
    });

    await test.step("Cancel the abandoned run so nothing stays active", async () => {
      expect(
        await cancelActiveRuns(base, actorKey, automation.id),
      ).toHaveLength(1);
      const runs = await listRuns(base, actorKey, automation.id);
      expect(runs.map((run) => run.status)).toEqual(["CANCELLED"]);
    });
  });

  test("a cancelled run fails the success wait with its CANCELLED history", async ({
    baseURL,
    page,
  }, testInfo) => {
    test.setTimeout(SCRIPT_START_WAIT_MS + SUCCESS_WAIT_BOUND_MS + 90_000);
    const base = baseURL || "";
    const automation = await test.step("Create the stalled automation", () =>
      createAutomation(base, "cancel", stalledRunScript(STALL_SECONDS)));

    await test.step("Dispatch it with Run now and wait until its script runs", async () => {
      await dispatchWithRunNow(page, testInfo, orgName, automation);
      await waitForScriptStart(base, automation.id);
    });

    // The success wait is already polling when the user cancels the run.
    const waiting = captureSuccessAssertionFailure(
      base,
      actorKey,
      automation.id,
      SUCCESS_WAIT_BOUND_MS,
    );

    await test.step("Cancel the run from the Home page", async () => {
      await page.goto("canvas/");
      await selectOrganization(page, orgName);
      const row = page
        .locator('[data-testid^="running-automation-row-"]')
        .filter({ hasText: automation.name });
      await expect(row).toBeVisible();
      await row.locator('[data-testid^="running-automation-menu-"]').click();
      await attachScreenshot(page, testInfo, "cancel-menu");
      await page.locator('[data-testid^="running-automation-cancel-"]').click();
      await expect(page.getByText("Automation run cancelled")).toBeVisible();
    });

    const failure =
      await test.step("The success wait fails promptly on the cancelled run", () =>
        waiting);

    await test.step("The failure names CANCELLED, who cancelled it and the lifecycle observed", () => {
      expect(failure.message).toContain(
        "ended CANCELLED, expected COMPLETED (error_detail: Cancelled by user;",
      );
      expect(failure.message).toMatch(
        /lifecycle: (PENDING -> )?(RUNNING -> )?CANCELLED\)$/,
      );
      expect(failure.elapsedMs).toBeLessThan(
        SUCCESS_WAIT_BOUND_MS + SUCCESS_WAIT_SLACK_MS,
      );
    });

    await test.step("The run page shows the run as cancelled, not completed", async () => {
      await page.goto(`canvas/automations/${automation.id}`);
      const log = page.getByTestId("automation-activity-log");
      await expect(log).toContainText("Cancelled by user");
      await expect(log.getByText("Cancelled", { exact: true })).toHaveCount(1);
      await expect(log.getByTestId("run-status-icon-completed")).toHaveCount(0);
      await log
        .getByText("Cancelled", { exact: true })
        .scrollIntoViewIfNeeded();
      await attachScreenshot(page, testInfo, "cancelled-run");
    });
  });

  test("a run that never calls back cannot pass the success wait", async ({
    baseURL,
    page,
  }, testInfo) => {
    test.setTimeout(SCRIPT_START_WAIT_MS + SUCCESS_WAIT_BOUND_MS + 90_000);
    const base = baseURL || "";
    const automation = await test.step("Create the silent automation", () =>
      createAutomation(base, "no-callback", missingCallbackRunScript()));

    await test.step("Dispatch it with Run now and wait until its script runs", async () => {
      await dispatchWithRunNow(page, testInfo, orgName, automation);
      await waitForScriptStart(base, automation.id);
    });

    const failure =
      await test.step("The success wait gives up within its bound", () =>
        captureSuccessAssertionFailure(
          base,
          actorKey,
          automation.id,
          SUCCESS_WAIT_BOUND_MS,
        ));

    await test.step("The failure reports that no terminal status arrived and the lifecycle observed", () => {
      // The service learns of a script that exits silently only at the run's
      // own deadline, which is later than this bound; a service that detects
      // it sooner ends the run FAILED, which also fails the success wait.
      expect(failure.message).toMatch(
        /did not reach a terminal status within 30000ms \(lifecycle: (PENDING -> )?RUNNING\)$|ended FAILED, expected COMPLETED \(error_detail: .+; lifecycle: (PENDING -> )?(RUNNING -> )?FAILED\)$/,
      );
      expect(failure.message).not.toContain("ended COMPLETED");
      expect(failure.elapsedMs).toBeLessThan(
        SUCCESS_WAIT_BOUND_MS + SUCCESS_WAIT_SLACK_MS,
      );
    });

    await test.step("The run record has no completion and links no conversation", async () => {
      const [run] = await listRuns(base, actorKey, automation.id);
      expect(run.status).not.toBe("COMPLETED");
      expect(run.completed_at ?? null).toBeNull();
      expect(run.conversation_id ?? null).toBeNull();
    });

    await test.step("The run page does not show the run as completed", async () => {
      await page.reload();
      const log = page.getByTestId("automation-activity-log");
      await expect(log.getByTestId("run-status-icon-completed")).toHaveCount(0);
      await log.locator("time").first().scrollIntoViewIfNeeded();
      await attachScreenshot(page, testInfo, "no-callback-run-not-completed");
    });

    await test.step("Cancel the abandoned run so nothing stays active", async () => {
      await cancelActiveRuns(base, actorKey, automation.id);
      const runs = await listRuns(base, actorKey, automation.id);
      expect(runs.every((run) => run.status !== "RUNNING")).toBe(true);
    });
  });
});
