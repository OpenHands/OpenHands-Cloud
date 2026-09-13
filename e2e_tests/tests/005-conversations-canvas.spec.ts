import { test } from "@playwright/test";
import { CanvasHomePage, CanvasConversationPage } from "../pages";
import { runUser } from "../utils/config";

/**
 * Canvas conversation controls specs.
 *
 * Mirrors `003-conversations-legacy.spec.ts` but exercises the new Agent
 * Canvas UI (hosted at `/canvas`), sourced from the OpenHands/OpenHands
 * frontend bundle. The canvas UI shares a few test IDs with the legacy UI
 * (home-screen, chat-input, error-message-banner) but differs in:
 *
 *  - Conversation launch: there is no dedicated "launch new conversation"
 *    button. Conversations are started by typing into the home chat input and
 *    submitting, which creates the conversation and navigates to it.
 *  - Status: the canvas does NOT surface a persistent readiness/completion
 *    status the way the legacy UI does. The `chat-status-indicator` is only
 *    mounted while the agent is in the starting (LOADING/INIT) state and is
 *    unmounted once ready, so readiness is detected from the chat shell
 *    (chat-interface/chat-input/submit-button) being visible. Completion is
 *    detected from the agent leaving the running state — the in-chat
 *    stop-button (visible only while RUNNING) disappearing.
 *  - Messages: rendered as `agent-message` / `user-message` elements.
 *  - Recent conversations: a `conversation-panel` of `conversation-card`
 *    items, not a `recent-conversations` link list.
 *
 * The suite runs serially within a role because several tests depend on a
 * conversation created by an earlier one (navigate-to-running and Tavily both
 * click the first recent conversation, which only exists once a launch has
 * happened). As with the rest of the harness, each spec runs once per user
 * role (returning / new-user); the active role is read from project metadata
 * via `runUser(testInfo)`.
 *
 * NOTE: The VSCode integration test from 003 is omitted here. The canvas UI
 * does not expose a `conversation-tab-vscode` test ID; VSCode is reached via
 * a `drawer-vscode-link` whose iframe structure differs from the legacy
 * editor tab. That flow should be validated against a live canvas DOM via
 * codegen before being ported, to avoid asserting on selectors that have not
 * been confirmed against a running deployment.
 */
test.describe("canvas conversations @conversations-canvas", () => {
  test.describe.configure({ mode: "serial" });

  let homePage: CanvasHomePage;

  let conversationPage: CanvasConversationPage;

  test.beforeEach(async ({ page }) => {
    homePage = new CanvasHomePage(page);
    conversationPage = new CanvasConversationPage(page);
  });

  test("should be able to start a conversation via chat input and reverse a string", async ({
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

    // Start a new conversation by sending an initial prompt from the home
    // chat launcher (canvas has no dedicated launch button).
    await homePage.startNewConversation("Reverse the word 'hello'");

    // Allow navigation to complete.
    await page.waitForTimeout(2000);
    conversationPage = new CanvasConversationPage(page);

    await conversationPage.waitForConversationReady();

    await page.screenshot({
      path: "test-results/screenshots/canvas-conversation-ready.png",
    });

    // The initial prompt may have been consumed as the conversation's first
    // message. Re-send to ensure a clean prompt/response cycle we can assert
    // on, since the home launcher's message handling can vary by backend.
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
      path: "test-results/screenshots/canvas-agent-response.png",
    });

    console.log(
      "Canvas conversation launch test passed: agent reversed the word",
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

    // Click on the first conversation in the conversation panel.
    await homePage.clickFirstConversation();

    conversationPage = new CanvasConversationPage(page);

    // The conversation should still be in a finished state from the prior test.
    await conversationPage.waitForTaskCompleteMessage();

    await page.screenshot({
      path: "test-results/screenshots/canvas-navigated-conversation.png",
    });

    console.log("Successfully navigated to running canvas conversation");
  });

  test("should be able to use Tavily search and get accurate response", async ({
    page,
  }, testInfo) => {
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    // External search can be slow, so lift the timeout to give the 180s waits
    // room to apply.
    test.setTimeout(240_000);

    await homePage.goto();

    await homePage.clickFirstConversation();

    conversationPage = new CanvasConversationPage(page);

    await conversationPage.waitForTaskCompleteMessage();

    const prompt =
      "Using Tavily search, please tell me who is the prime minister of Ireland. Use the default search parameters — do not set a topic/category field (Tavily only accepts 'general', and other values are rejected).";
    console.log(`Sending prompt: "${prompt}"`);
    await conversationPage.executePrompt(prompt, 180_000);

    // Match the name with a regex so accent ("Micheál" vs "Micheal") and casing
    // variants in the agent's response don't cause spurious failures.
    const message = await conversationPage.waitForMessageContaining(
      /miche[aá]l martin/i,
      180_000,
    );
    console.log(
      `Found expected response containing 'Micheál Martin': "${message.substring(0, 100)}..."`,
    );

    await page.screenshot({
      path: "test-results/screenshots/canvas-tavily-search-response.png",
    });

    console.log(
      "Canvas Tavily search test passed: agent correctly identified the Prime Minister of Ireland",
    );
  });
});
