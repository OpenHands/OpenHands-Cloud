import { Page, Locator, expect } from "@playwright/test";
import { BasePage } from "./BasePage";

/**
 * Budget given to the error-banner watch inside the readiness/message races.
 *
 * On a genuinely failed conversation the error banner appears quickly, so a
 * short fixed budget is enough to fail fast. Keeping this small also stops the
 * losing branch of Promise.race from polling for the full readiness timeout
 * (which otherwise leaves a ~60s dangling wait in the trace even on success).
 */
const ERROR_BANNER_WAIT_MS = 5_000;

/**
 * Agent states that can be observed during conversation
 */
export enum AgentState {
  LOADING = "loading",
  RUNNING = "running",
  AWAITING_USER_INPUT = "awaiting_user_input",
  AWAITING_USER_CONFIRMATION = "awaiting_user_confirmation",
  FINISHED = "finished",
  ERROR = "error",
  PAUSED = "paused",
  STOPPED = "stopped",
  INIT = "init",
}

/**
 * Page object for the Conversation/Chat interface where users
 * interact with the OpenHands agent.
 */
export class ConversationPage extends BasePage {
  // Main containers
  readonly appRoute: Locator;

  readonly chatBox: Locator;

  // Chat input elements
  readonly chatInput: Locator;

  readonly stopButton: Locator;

  // Message elements
  readonly errorBanner: Locator;

  readonly waitingMessage: Locator;

  // Status indicators
  readonly statusIndicator: Locator;

  constructor(page: Page) {
    super(page);

    this.appRoute = page.getByTestId("app-route");
    this.chatBox = page.getByTestId("interactive-chat-box");
    this.chatInput = page.getByTestId("chat-input");
    // `stop-button` is icon-only in Canvas — chat-stop-button.tsx renders
    // a `<PauseIcon />` inside a `<button>` with no aria-label, title, or
    // visible text, so `getByRole("button", { name: /stop/i })` returns
    // zero matches. testId is the only stable handle until that component
    // grows an accessible name (worth a follow-up in the OpenHands
    // frontend).
    this.stopButton = page.getByTestId("stop-button");
    this.errorBanner = page.getByTestId("error-message-banner");
    this.waitingMessage = page.locator('[data-testid*="waiting"]').first();
    // Canvas renamed `status-icon` to `chat-status-indicator`; the enterprise
    // conversation view still ships `status-icon`. Match either so tests work
    // against both shells during the transition.
    this.statusIndicator = page
      .getByTestId("chat-status-indicator")
      .or(page.getByTestId("status-icon"));
  }

  /**
   * Navigate to a specific conversation
   */
  async gotoConversation(conversationId: string): Promise<void> {
    await super.goto(`/conversation/${conversationId}`);
    await this.waitForConversationReady();
  }

  /**
   * Wait for conversation interface to be ready for input.
   *
   * The chat shell renders almost immediately, but the conversation only
   * reaches the ready ("Waiting for task") state once the sandbox/runtime has
   * finished provisioning — a cold start that can exceed 30s on a busy staging
   * environment. We therefore use a short timeout for the UI shell and a
   * generous timeout for readiness, match the status text with a
   * case-insensitive regex (tolerant of casing/whitespace drift), and race
   * against the error banner so a genuinely failed conversation fails fast with
   * a clear message rather than waiting out the full timeout.
   */
  async waitForConversationReady(timeout: number = 120_000): Promise<void> {
    const shellTimeout = Math.min(timeout, 30_000);

    // Wait for the chat interface to appear
    await expect(this.chatBox).toBeVisible({ timeout: shellTimeout });

    // Wait for the chat input to be visible
    await expect(this.chatInput).toBeVisible({ timeout: shellTimeout });

    // Wait for agent to be ready. The old enterprise UI surfaced the string
    // "Waiting for task"; the Canvas conversation view instead exposes the
    // status through `chat-status-indicator` (a stable data-testid) and the
    // active-conversation status pill (`conversation-status-active` /
    // `conversation-status-working`). We accept any of these signals so the
    // helper works against both shells during the Canvas migration.
    //
    // The error-banner branch is a sentinel: it only resolves when an error
    // actually appears. On timeout it stays pending (never resolves), so it
    // cannot short-circuit the race — the full timeout budget is left to the
    // ready branch. Previously both branches resolved on timeout, so the
    // 5s error-budget branch always won the race after 5s and the ready
    // branch was abandoned with ~115s of budget unused.
    const readyText = this.page
      .getByText(/waiting for task/i)
      .or(this.page.getByTestId("chat-status-indicator"))
      .or(this.page.getByTestId("conversation-status-active"))
      .or(this.page.getByTestId("conversation-status-working"))
      .first();
    const outcome = await Promise.race([
      readyText
        .waitFor({ state: "visible", timeout })
        .then(() => "ready" as const)
        .catch(() => "timeout" as const),
      new Promise<"error">((resolve) => {
        this.errorBanner
          .waitFor({ state: "visible", timeout: ERROR_BANNER_WAIT_MS })
          .then(() => resolve("error"))
          .catch(() => {});
      }),
    ]);

    if (outcome === "error") {
      const errorMsg = await this.getErrorMessage();
      throw new Error(`Conversation failed to become ready: ${errorMsg}`);
    }

    if (outcome === "timeout") {
      // Surface a still-provisioning sandbox to make diagnosis obvious: this is
      // the dominant cause of readiness timeouts (see issue #4556).
      const sandboxProvisioning = await this.page
        .getByText(/waiting for sandbox/i)
        .isVisible()
        .catch(() => false);
      throw new Error(
        `Conversation did not become ready within ${timeout}ms${
          sandboxProvisioning
            ? " — sandbox still provisioning ('Waiting for sandbox')"
            : ""
        }`,
      );
    }
  }

  /**
   * Wait for the agent to finish the task.
   *
   * The chat shell appears almost immediately, but the task itself (repo
   * clone, file edits, web search, etc.) can take minutes. We therefore use a
   * short timeout for the UI shell and a generous timeout for the completion
   * status, and match the status text with a case-insensitive regex so minor
   * wording/casing changes don't break the test.
   */
  async waitForTaskCompleteMessage(timeout: number = 180_000): Promise<void> {
    const shellTimeout = Math.min(timeout, 30_000);

    // Wait for the chat interface to appear
    await expect(this.chatBox).toBeVisible({ timeout: shellTimeout });

    // Wait for the chat input to be visible
    await expect(this.chatInput).toBeVisible({ timeout: shellTimeout });

    // Wait for agent to finish. The enterprise UI surfaces the string
    // "Agent has finished the task"; Canvas exposes completion via the
    // `conversation-status-check` testid (rendered by the status pill when
    // the agent reaches the `finished` state). Accept either signal so the
    // helper works across both shells during the Canvas migration.
    const finishedTask = this.page
      .getByText(/agent has finished the task/i)
      .or(this.page.getByTestId("conversation-status-check"))
      .first();
    await expect(finishedTask).toBeVisible({ timeout });
  }

  /**
   * Get all visible messages in the chat
   */
  async getMessages(): Promise<string[]> {
    const messageElements = this.page.locator(
      '[data-testid*="message"], [class*="message"]',
    );
    return messageElements.allTextContents();
  }

  /**
   * Get the last message from the agent
   */
  async getLastAgentMessage(): Promise<string | null> {
    const messages = await this.getMessages();
    // Return the last message that's likely from the agent
    return messages.length > 0 ? messages[messages.length - 1] : null;
  }

  /**
   * Wait for a message containing specific text to appear.
   *
   * Uses Playwright's auto-retrying locator instead of a manual polling loop,
   * which removes fixed-interval waits and reacts the instant the message
   * renders. `expectedText` accepts a RegExp so callers can match flexibly
   * (e.g. accent/casing variants of a name) rather than relying on an exact
   * substring. Races against the error banner so we fail fast on agent errors
   * instead of waiting out the full timeout.
   *
   * @param expectedText - The text (or pattern) to search for in messages
   * @param timeout - Maximum time to wait in milliseconds
   * @returns The text content of the message containing the expected text
   */
  async waitForMessageContaining(
    expectedText: string | RegExp,
    timeout: number = 120_000,
  ): Promise<string> {
    const target = this.page
      .locator('[data-testid*="message"], [class*="message"]')
      .filter({ hasText: expectedText })
      .first();

    const outcome = await Promise.race([
      target
        .waitFor({ state: "visible", timeout })
        .then(() => "match" as const)
        .catch(() => "timeout" as const),
      new Promise<"error">((resolve) => {
        this.errorBanner
          .waitFor({ state: "visible", timeout: ERROR_BANNER_WAIT_MS })
          .then(() => resolve("error"))
          .catch(() => {});
      }),
    ]);

    if (outcome === "error") {
      const errorMsg = await this.getErrorMessage();
      throw new Error(`Agent error while waiting for message: ${errorMsg}`);
    }

    if (outcome === "timeout") {
      // Surface the most recent messages to make diagnosis easier on failure.
      const allMessages = await this.getMessages();
      throw new Error(
        `Timeout waiting for message containing "${expectedText}" after ${timeout}ms. ` +
          `Messages found: ${JSON.stringify(allMessages.slice(-5))}`,
      );
    }

    return (await target.textContent()) ?? "";
  }

  /**
   * Stop the currently running agent
   */
  async stopAgent(): Promise<void> {
    if (
      await this.stopButton.isVisible({ timeout: 2_000 }).catch(() => false)
    ) {
      await this.stopButton.click();
      await this.page.waitForTimeout(1000);
    }
  }
}
