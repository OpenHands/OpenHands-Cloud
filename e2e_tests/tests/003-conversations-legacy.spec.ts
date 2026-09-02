import { test, expect } from "@playwright/test";
import { HomePage, ConversationPage } from "../pages";
import { runUser } from "../utils/config";

/**
 * Legacy conversation controls specs.
 *
 * Ported from saas_deploy's `e2e_tests/tests/smoke.spec.ts` (the conversation
 * launch, repository/VSCode, navigation, and Tavily search flows). These
 * exercise the legacy chat controls — launch button, repo selector, recent
 * conversations list, and the agent's prompt/response loop — rather than the
 * newer canvas UI.
 *
 * The suite runs serially within a role because several tests depend on a
 * conversation created by an earlier one (e.g. "navigate to a running
 * conversation" and "Tavily search" both click the first recent conversation,
 * which only exists once a launch has happened). As with the rest of the
 * harness, each spec runs once per user role (returning / new-user); the
 * active role is read from project metadata via `runUser(testInfo)`.
 */

test.describe("legacy conversations @conversations", () => {
  test.describe.configure({ mode: "serial" });

  let homePage: HomePage;

  let conversationPage: ConversationPage;

  test.beforeEach(async ({ page }) => {
    homePage = new HomePage(page);
    conversationPage = new ConversationPage(page);
  });

  test("should be able to start a conversation via launch button and reverse a string", async ({
    page,
  }, testInfo) => {
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    // Sandbox cold-start can push conversation readiness past the global
    // per-test cap, so lift the timeout to give waitForConversationReady's
    // budget room to apply. See issue #4556.
    test.setTimeout(240_000);

    await homePage.goto();

    // Start a new conversation using the launch button.
    await homePage.startNewConversation("launch-new-conversation-button");

    // Allow navigation to complete.
    await page.waitForTimeout(2000);
    conversationPage = new ConversationPage(page);

    await conversationPage.waitForConversationReady();

    await page.screenshot({
      path: "test-results/screenshots/conversation-ready.png",
    });

    const prompt = "Reverse the word 'hello'";
    console.log(`Sending prompt: "${prompt}"`);
    await conversationPage.executePrompt(prompt, 120_000);

    const message = await conversationPage.waitForMessageContaining(
      "olleh",
      120_000,
    );
    console.log(
      `Found expected response containing 'olleh': "${message.substring(0, 100)}..."`,
    );

    await page.screenshot({
      path: "test-results/screenshots/agent-response.png",
    });

    console.log(
      "Legacy conversation launch test passed: agent reversed the word",
    );
  });

  test("should be able to select repository and use VSCode integration", async ({
    page,
  }, testInfo) => {
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    // Cloning the repo and editing the README is multi-minute work, so lift
    // the timeout to give waitForTaskCompleteMessage's budget room to apply.
    test.setTimeout(240_000);

    const TEST_REPO_URL = "https://github.com/OpenHands/OpenHands";

    await homePage.goto();

    await homePage.selectRepository(TEST_REPO_URL);
    console.log(`Selected repository: ${TEST_REPO_URL}`);

    // Start a new conversation with the repo launch button.
    await homePage.startNewConversation("repo-launch-button");

    await page.waitForTimeout(2000);
    conversationPage = new ConversationPage(page);

    await conversationPage.waitForConversationReady();

    const prompt =
      "Append the phrase 'Terms and Conditions May Apply!' to the end of README.md — actually edit the file and save it.";
    console.log(`Sending prompt: "${prompt}"`);
    await conversationPage.sendMessage(prompt);

    // Wait for the task to start.
    const waitingForTaskText = conversationPage.page.getByText("Running task");
    await expect(waitingForTaskText).toBeVisible({ timeout: 30_000 });

    await conversationPage.waitForTaskCompleteMessage();
    console.log(
      "Status is 'Agent has finished the task' - README append task completed",
    );

    await page.screenshot({
      path: "test-results/screenshots/conversation-with-repo.png",
    });

    // The README should have been updated and appear in the diff viewer.
    const readMe = conversationPage.page
      .getByTestId("file-diff-viewer-outer")
      .locator("div")
      .filter({ hasText: /^README\.md$/ });

    // The changes panel is repopulated by WebSocket ActionEvents invalidating
    // the `file_changes` cache; there is no timer and no refresh on the
    // FINISHED status (see use-unified-get-git-changes.ts). If the final
    // file-edit event was missed or its refetch transiently failed (retry:
    // false), the panel stays empty and no amount of waiting will repopulate
    // it. As a safety net, if the README hasn't appeared within the initial
    // budget, click the editor tab's refresh button (which calls refetch())
    // once and re-wait.
    const fileChangesRefreshButton = conversationPage.page.getByTestId(
      "changes-refresh-button",
    );

    await expect(readMe)
      .toBeVisible({ timeout: 10_000 })
      .catch(async () => {
        console.log(
          "Changes panel empty after task completion; clicking refresh to refetch git changes",
        );
        await fileChangesRefreshButton.click().catch(() => {});
        await page.waitForTimeout(1_000);
      });

    await expect(readMe).toBeVisible();
    await readMe.click();

    // Appending a phrase should have inserted at least one new line.
    await expect(page.locator(".cdr.line-insert").first()).toBeVisible({
      timeout: 10_000,
    });

    // Open the VSCode tab.
    const vscodeTabButton = page.getByTestId("conversation-tab-vscode");
    await vscodeTabButton.click();
    console.log("Clicked VSCode tab button");

    // Wait for the VS Code iframe and the explorer viewlet (combined 30s).
    // The iframe may not be visible initially, and the explorer viewlet can
    // take additional time to appear after the iframe loads.
    const vsCodeFrameLocator = page.locator('iframe[title="VS Code"]');
    const startTime = Date.now();
    const totalTimeout = 30_000;

    await expect(vsCodeFrameLocator).toBeVisible({ timeout: totalTimeout });

    const elapsed = Date.now() - startTime;
    const remainingTimeout = Math.max(totalTimeout - elapsed, 0);

    const vsCodeFrame = vsCodeFrameLocator.contentFrame();
    const explorerViewlet = vsCodeFrame
      .locator("a")
      .filter({ hasText: "OpenHands" });
    await expect(explorerViewlet).toBeVisible({ timeout: remainingTimeout });
    console.log("VSCode loaded with OpenHands repository");

    await page.screenshot({
      path: "test-results/screenshots/vscode-openhands.png",
    });

    console.log(
      "VSCode integration test passed: repository loaded successfully",
    );
  });

  test("should be able to navigate to a running conversation", async ({
    page,
  }, testInfo) => {
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    await homePage.goto();

    // Click on the first conversation in the recent conversations list.
    await homePage.clickFirstConversation();

    conversationPage = new ConversationPage(page);

    // The conversation should still be in a finished state from the prior test.
    await conversationPage.waitForTaskCompleteMessage();

    await page.screenshot({
      path: "test-results/screenshots/navigated-conversation.png",
    });

    console.log("Successfully navigated to running conversation");
  });

  test("should be able to use Tavily search and get accurate response", async ({
    page,
  }, testInfo) => {
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    // Sandbox cold-start plus an external search outlast the 120s default cap.
    test.setTimeout(240_000);

    await homePage.goto();

    await homePage.startNewConversation("launch-new-conversation-button");

    await page.waitForTimeout(2000);
    conversationPage = new ConversationPage(page);

    await conversationPage.waitForConversationReady();

    const prompt =
      "Using Tavily search, please tell me who is the prime minister of Ireland.";
    console.log(`Sending prompt: "${prompt}"`);
    await conversationPage.executePrompt(prompt, 120_000);

    // Match the name with a regex so accent ("Micheál" vs "Micheal") and casing
    // variants in the agent's response don't cause spurious failures.
    const message = await conversationPage.waitForMessageContaining(
      /miche[aá]l martin/i,
      120_000,
    );
    console.log(
      `Found expected response containing 'Micheál Martin': "${message.substring(0, 100)}..."`,
    );

    await page.screenshot({
      path: "test-results/screenshots/tavily-search-response.png",
    });

    console.log(
      "Tavily search test passed: agent correctly identified the Prime Minister of Ireland",
    );
  });
});
