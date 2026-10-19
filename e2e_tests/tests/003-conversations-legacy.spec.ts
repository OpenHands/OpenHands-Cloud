import { createHash, randomUUID } from "node:crypto";

import { test } from "@playwright/test";
import { HomePage, ConversationPage } from "../pages";
import { runUser } from "../utils/config";

/**
 * Conversation controls specs.
 *
 * Ported from saas_deploy's `e2e_tests/tests/smoke.spec.ts` (the conversation
 * launch, navigation, and external-tool flows). Originally
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
