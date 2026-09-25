import { createHash, randomUUID } from "node:crypto";

import { test, expect } from "@playwright/test";
import { HomePage, ConversationPage } from "../pages";
import { env, runUser } from "../utils/config";

/**
 * Conversation controls specs.
 *
 * Ported from saas_deploy's `e2e_tests/tests/smoke.spec.ts` (the conversation
 * launch, repository/VSCode, navigation, and external-tool flows). Originally
 * written against the enterprise "legacy" launcher (a separate button that
 * navigated to a launch route); Canvas replaced that with an atomic composer
 * — `home-chat-launcher` creates the conversation *and* sends the first user
 * message in one gesture — so these specs pass the prompt directly to
 * `HomePage.startNewConversation()` instead of sending it through the chat
 * input after navigation.
 *
 * The suite runs serially within a role because several tests depend on a
 * conversation created by an earlier one (e.g. "navigate to a running
 * conversation" and the tool-use test both click the first recent conversation,
 * which only exists once a launch has happened). As with the rest of the
 * harness, each spec runs once per user role (returning / new-user); the
 * active role is read from project metadata via `runUser(testInfo)`.
 *
 * Tool-use coverage is deliberately generic (sandbox `bash` / `sha256sum`)
 * rather than tied to a single SaaS-only backend such as Tavily — see
 * PLTF-3662 — so the same assertion holds on OpenHands Cloud and on
 * OpenHands Enterprise self-hosted installs.
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

    // Canvas' `home-chat-launcher` sends the first user message atomically
    // with the create-conversation call, so pass the prompt to
    // startNewConversation() rather than sending it separately once we've
    // navigated to the conversation page.
    const prompt = "Reverse the word 'hello'";
    console.log(`Sending prompt: "${prompt}"`);
    await homePage.startNewConversation(prompt);

    conversationPage = new ConversationPage(page);
    await conversationPage.waitForConversationReady();

    await page.screenshot({
      path: "test-results/screenshots/conversation-ready.png",
    });

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
    // TODO(canvas): the enterprise VSCode-iframe integration (the
    // `conversation-tab-vscode` tab, the `file-diff-viewer-outer` +
    // `changes-refresh-button` git-changes panel) has no equivalent yet in
    // the Canvas conversation view — its Files panel is a separate story
    // (`use-canvas-extensions-*` module) that renders a filesystem tree
    // without the diff/editor iframe this test asserts on. Skip until Canvas
    // ships an equivalent surface (or an explicit "open in VSCode Web" tab)
    // that we can drive; the "reverse a string" and "Tavily search" tests
    // still exercise the launch → sandbox → agent-reply path end to end.
    test.skip(
      true,
      "Canvas conversation view has no VSCode-tab / diff-viewer equivalent yet",
    );
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    // Cloning the repo and editing the README is multi-minute work, so lift
    // the timeout to give waitForTaskCompleteMessage's budget room to apply.
    test.setTimeout(240_000);

    // Use the shared fixture repo (env.testRepoUrl, default
    // OpenHands/deploy ~12 MiB) rather than hardcoding OpenHands/OpenHands
    // (~407 MiB). The clone is the dominant cost in this test; the smaller
    // repo keeps it well under the timeout budget while still exercising the
    // full clone → edit → diff-viewer → VSCode flow.
    const TEST_REPO_URL = env.testRepoUrl;

    await homePage.goto();

    await homePage.selectRepository(TEST_REPO_URL);
    console.log(`Selected repository: ${TEST_REPO_URL}`);

    // Canvas' launcher sends the first user message atomically with the
    // create-conversation call, so seed the README-edit prompt here rather
    // than sending it separately once we've navigated to the conversation.
    const prompt =
      "Append the phrase 'Terms and Conditions May Apply!' to the end of README.md in the current working directory (the repo root) — actually edit the file and save it.";
    console.log(`Sending prompt: "${prompt}"`);
    await homePage.startNewConversation(prompt);

    conversationPage = new ConversationPage(page);
    await conversationPage.waitForConversationReady();

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

    // VS Code's explorer shows the cloned repo's top-level folder, which is
    // named after the repo (e.g. "deploy" for OpenHands/deploy) — not a fixed
    // string — so derive the expected name from the configured repo URL.
    const repoFolderName = TEST_REPO_URL.split("/").pop() ?? "";
    const vsCodeFrame = vsCodeFrameLocator.contentFrame();
    const explorerViewlet = vsCodeFrame
      .locator("a")
      .filter({ hasText: repoFolderName });
    await expect(explorerViewlet).toBeVisible({ timeout: remainingTimeout });
    console.log(`VSCode loaded with repository folder: ${repoFolderName}`);

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

  test("should be able to invoke a sandbox tool and return the exact output", async ({
    page,
  }, testInfo) => {
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    // Sandbox cold-start plus one bash invocation outlast the 120s default cap.
    test.setTimeout(240_000);

    // Replaces the earlier "Tavily search" spec (PLTF-3662): Tavily is a
    // SaaS-only integration, so its "no TAVILY_API_KEY" failure mode had
    // nothing to do with the agent stack itself and did not reproduce on
    // OpenHands Enterprise. Exercise generic sandbox tool use instead —
    // available on every deployment target.
    //
    // Design: give the agent a nonce and its expected SHA-256 hex. Ask it
    // to compute the hash *in the sandbox* (bash `sha256sum` is universal on
    // Linux runtimes) and quote the digest. Assertion is the *hex*, not the
    // nonce — the nonce alone would appear verbatim in the prompt and any
    // echoing reply, so matching it would prove nothing. The expected hash
    // does not appear anywhere in the prompt, LLMs reliably fail to compute
    // SHA-256 in-head, and if the tool loop is broken the agent surfaces
    // that instead ("I encountered an error running the command") — which
    // fails the assertion, exactly the signal we want.
    //
    // `randomUUID()` (not a timestamp) guarantees a fresh nonce per test run
    // so cached model responses or interleaved parallel runs cannot satisfy
    // the assertion by accident.
    const nonce = `oh-e2e-${randomUUID()}`;
    const expectedHash = createHash("sha256").update(nonce).digest("hex");

    await homePage.goto();

    // See the "reverse a string" test above — Canvas' launcher sends the
    // first user message with the create-conversation call, so pass the
    // prompt directly to startNewConversation().
    const prompt =
      `In the sandbox, please run this exact bash command and quote the ` +
      `SHA-256 hex digest it prints (the 64-character hex string before ` +
      `the trailing dash):\n\n` +
      `    printf '%s' '${nonce}' | sha256sum\n\n` +
      `Report only the hex digest, verbatim, in your reply.`;
    console.log(`Sending prompt with nonce ${nonce}`);
    console.log(`Expecting SHA-256 hex ${expectedHash} in the reply`);
    await homePage.startNewConversation(prompt);

    conversationPage = new ConversationPage(page);
    await conversationPage.waitForConversationReady();

    // Match the full 64-character digest so a partial-match false positive
    // (e.g. the model hallucinating a similar-looking prefix) is impossible.
    const message = await conversationPage.waitForMessageContaining(
      expectedHash,
      180_000,
    );
    console.log(
      `Found expected SHA-256 digest in reply: "${message.substring(0, 100)}..."`,
    );

    await page.screenshot({
      path: "test-results/screenshots/tool-use-response.png",
    });

    console.log(
      "Generic tool-use test passed: agent invoked bash/sha256sum and returned the correct digest",
    );
  });
});
