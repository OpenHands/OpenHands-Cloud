import { expect, test, type Page, type TestInfo } from "@playwright/test";

import {
  deleteAppConversation,
  getAppConversation,
  listFinishMessages,
} from "../utils/app-conversations";
import {
  completedRunScript,
  singleFileTarGz,
} from "../utils/automation-fixtures";
import {
  createScriptAutomation,
  deleteAutomation,
  deleteUpload,
  isAutomationServiceReachable,
  listRuns,
  uploadTarball,
  waitForRunCompleted,
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
 * Automation journey: a successful run must end COMPLETED.
 *
 * The automation is a deterministic script (no LLM, repository or credential
 * of the target): it drives one conversation against a loopback stub LLM that
 * replies with a single ``finish`` call carrying a run-scoped sentinel, then
 * reports ``COMPLETED`` with that conversation's id. The test dispatches it
 * with the automation page's "Run now" button and passes only when
 *
 *  - the run reaches ``COMPLETED`` within the bounded wait (``FAILED``,
 *    ``CANCELLED``, ``SKIPPED`` and a callback that never arrives all fail,
 *    each reporting the lifecycle it observed),
 *  - the run page shows the run as completed with the sentinel as its task
 *    outcome, and
 *  - the conversation the run links to exists and ended with that sentinel.
 *
 * Setup uses the REST API (new-user role only, like ``011-automations``): a
 * fresh org with the New User as owner, an uploaded script and an automation
 * on a cron schedule that never ticks. ``afterAll`` deletes all of it, newest
 * first.
 */

const RUN_COMPLETED_TIMEOUT_MS = 4 * 60 * 1000;

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

test.describe("automation journey: COMPLETED terminal status", () => {
  const cleanup = new Cleanup();

  test.afterAll(async () => {
    await cleanup.run();
  });

  test("a dispatched deterministic automation ends COMPLETED with its output and linked conversation", async ({
    baseURL,
    page,
  }, testInfo) => {
    test.skip(runUser(testInfo) !== "new-user", "new-user role only");
    test.setTimeout(RUN_COMPLETED_TIMEOUT_MS + 120_000);

    const base = baseURL || "";
    const adminKey = superAdminApiKey();
    test.skip(
      !(await isAutomationServiceReachable(base, adminKey)),
      "Automation service not reachable on this cluster",
    );

    const timestamp = e2eTimestamp();
    const suffix = `qa-${timestamp}`;
    const expectedOutput = `automation-output-${suffix}`;
    const orgName = e2eOrgName("automation completed", timestamp);
    const automationName = `qa-completed-${suffix}`;

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
          `qa-completed-${suffix}`,
          singleFileTarGz("main.py", completedRunScript(expectedOutput)),
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

    const run = await test.step("Wait for the COMPLETED terminal status", () =>
      waitForRunCompleted(
        base,
        actorKey,
        automationId,
        RUN_COMPLETED_TIMEOUT_MS,
      ));
    expect(run.status).toBe("COMPLETED");
    expect(run.trigger_source).toBe("manual");
    expect(run.error_detail ?? null).toBeNull();

    await test.step("The run page shows the completed run and its output", async () => {
      await page.reload();
      await expect(page.getByTestId("run-status-icon-completed")).toHaveCount(
        1,
      );
      await expect(page.getByTestId("run-status-icon-failed")).toHaveCount(0);

      await page.getByRole("button", { name: /logs/i }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toContainText("COMPLETED");
      await expect(
        dialog.getByTestId("automation-task-metadata"),
      ).toContainText(expectedOutput);
      await attachScreenshot(page, testInfo, "run-completed-with-output");
    });

    await test.step("The run carries the expected output", () => {
      expect(run.run_metadata?.finish_tool_response).toEqual({
        message: expectedOutput,
      });
    });

    await test.step("The linked conversation exists and ended with the expected output", async () => {
      const conversationId = run.conversation_id;
      expect(
        conversationId,
        "a COMPLETED run must link its conversation",
      ).toBeTruthy();
      const linkedId = conversationId as string;
      cleanup.defer(`delete conversation ${linkedId}`, () =>
        deleteAppConversation(base, actorKey, linkedId),
      );

      const conversation = await getAppConversation(base, actorKey, linkedId);
      expect(
        conversation,
        `the app server has no conversation ${linkedId}`,
      ).not.toBeNull();
      expect(conversation?.id).toBe(linkedId.replaceAll("-", ""));
      expect(await listFinishMessages(base, actorKey, linkedId)).toEqual([
        expectedOutput,
      ]);
    });
  });
});
