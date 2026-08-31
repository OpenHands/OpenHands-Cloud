import { Page, Locator, expect } from "@playwright/test";
import { BasePage } from "./BasePage";

/**
 * Budget given to the error-banner watch inside the readiness/message races.
 *
 * Mirrors ConversationPage: on a genuinely failed conversation the error
 * banner appears quickly, so a short fixed budget is enough to fail fast while
 * leaving the full readiness timeout to the ready branch.
 */
const ERROR_BANNER_WAIT_MS = 5_000;

/**
 * Agent states observable in the canvas chat status indicator.
 *
 * The canvas UI surfaces status as localized text in the chat-status-indicator
 * rather than the long-form status strings the legacy UI used (e.g. "Waiting
 * for task", "Agent has finished the task"). The localized values are:
 *   WAITING_FOR_TASK -> "Ready"
 *   RUNNING_TASK     -> "Running"
 *   FINISHED         -> "Done"
 * (see OpenHands translation.json). Tests match on those short strings.
 */
const READY_STATUS = /ready/i;
const FINISHED_STATUS = /\bdone\b/i;

/**
 * Page object for the canvas conversation interface (OpenHands Agent Canvas
 * served at /canvas).
 *
 * The canvas frontend shares a handful of test IDs with the legacy UI
 * (chat-input, error-message-banner) but renders status via a dedicated
 * chat-status-indicator element and labels messages as `assistant-message` /
 * `user-message`. This page object encodes those differences so 005 can mirror
 * 003's flows without touching the legacy page objects.
 */
export class CanvasConversationPage extends BasePage {
  readonly chatInterface: Locator;

  readonly chatInput: Locator;

  readonly submitButton: Locator;

  readonly stopButton: Locator;

  readonly statusIndicator: Locator;

  readonly errorBanner: Locator;

  constructor(page: Page) {
    super(page);

    this.chatInterface = page.getByTestId("chat-interface");
    this.chatInput = page.getByTestId("chat-input");
    this.submitButton = page.getByTestId("submit-button");
    this.stopButton = page.getByTestId("stop-button");
    this.statusIndicator = page.getByTestId("chat-status-indicator");
    this.errorBanner = page.getByTestId("error-message-banner");
  }

  /**
   * Navigate to a specific canvas conversation.
   */
  async gotoConversation(conversationId: string): Promise<void> {
    await super.goto(`/canvas/conversations/${conversationId}`);
    await this.waitForConversationReady();
  }

  /**
   * Wait for the canvas conversation interface to be ready for input.
   *
   * The chat shell renders quickly, but the conversation only reaches the
   * ready ("Ready" status) state once the sandbox/runtime has finished
   * provisioning — a cold start that can exceed 30s. We use a short timeout
   * for the UI shell and a generous timeout for readiness, and race against
   * the error banner so a failed conversation fails fast with a clear message.
   */
  async waitForConversationReady(timeout: number = 120_000): Promise<void> {
    const shellTimeout = Math.min(timeout, 30_000);

    await expect(this.chatInterface).toBeVisible({ timeout: shellTimeout });
    await expect(this.chatInput).toBeVisible({ timeout: shellTimeout });

    const outcome = await Promise.race([
      this.statusIndicator
        .filter({ hasText: READY_STATUS })
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
      throw new Error(`Conversation did not become ready within ${timeout}ms`);
    }
  }

  /**
   * Wait for the agent to finish the task.
   *
   * Canvas surfaces the finished state as "Done" in the chat-status-indicator.
   */
  async waitForTaskCompleteMessage(timeout: number = 180_000): Promise<void> {
    const shellTimeout = Math.min(timeout, 30_000);

    await expect(this.chatInterface).toBeVisible({ timeout: shellTimeout });
    await expect(this.chatInput).toBeVisible({ timeout: shellTimeout });

    const outcome = await Promise.race([
      this.statusIndicator
        .filter({ hasText: FINISHED_STATUS })
        .waitFor({ state: "visible", timeout })
        .then(() => "finished" as const)
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
      throw new Error(`Agent error while waiting for completion: ${errorMsg}`);
    }

    if (outcome === "timeout") {
      throw new Error(`Agent did not finish within ${timeout}ms timeout`);
    }
  }

  /**
   * Check if the chat input is enabled (not disabled by a loading state).
   */
  async isChatInputEnabled(): Promise<boolean> {
    try {
      const isVisible = await this.chatInput.isVisible();
      if (!isVisible) return false;

      const classes = await this.chatInput.getAttribute("class");
      if (classes?.includes("disabled") || classes?.includes("loading")) {
        return false;
      }

      return true;
    } catch {
      return false;
    }
  }

  /**
   * Wait for the agent to be ready to receive input.
   */
  async waitForAgentReady(timeout: number = 90_000): Promise<void> {
    const startTime = Date.now();

    while (Date.now() - startTime < timeout) {
      if (await this.hasError()) {
        const errorMsg = await this.getErrorMessage();
        throw new Error(`Agent error: ${errorMsg}`);
      }

      if (await this.isChatInputEnabled()) {
        return;
      }

      await this.page.waitForTimeout(1000);
    }

    throw new Error(`Agent not ready within ${timeout}ms timeout`);
  }

  /**
   * Send a message to the agent.
   *
   * The canvas chat-input is a contentEditable div; typing then clicking the
   * submit-button (data-testid) is more reliable than relying on Enter alone.
   */
  async sendMessage(message: string): Promise<void> {
    await expect(this.chatInput).toBeVisible({ timeout: 30_000 });

    await this.chatInput.click();
    await this.page.keyboard.type(message);

    // Prefer the explicit submit button; fall back to Enter if it is not
    // interactable (e.g. hidden behind a compact layout).
    if (
      await this.submitButton.isVisible({ timeout: 2_000 }).catch(() => false)
    ) {
      await this.submitButton.click();
    } else {
      await this.page.keyboard.press("Enter");
    }

    await this.page.waitForTimeout(500);
  }

  /**
   * Get all visible assistant messages in the chat.
   */
  async getMessages(): Promise<string[]> {
    return this.page.getByTestId("assistant-message").allTextContents();
  }

  /**
   * Wait for an assistant message containing specific text to appear.
   *
   * Races against the error banner so we fail fast on agent errors. Accepts a
   * RegExp so callers can match flexibly (accent/casing variants).
   */
  async waitForMessageContaining(
    expectedText: string | RegExp,
    timeout: number = 120_000,
  ): Promise<string> {
    const target = this.page
      .getByTestId("assistant-message")
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
      const allMessages = await this.getMessages();
      throw new Error(
        `Timeout waiting for message containing "${expectedText}" after ${timeout}ms. ` +
          `Messages found: ${JSON.stringify(allMessages.slice(-5))}`,
      );
    }

    return (await target.textContent()) ?? "";
  }

  /**
   * Stop the currently running agent.
   */
  async stopAgent(): Promise<void> {
    if (
      await this.stopButton.isVisible({ timeout: 2_000 }).catch(() => false)
    ) {
      await this.stopButton.click();
      await this.page.waitForTimeout(1000);
    }
  }

  /**
   * Verify no error messages are displayed.
   */
  async verifyNoErrors(): Promise<void> {
    if (await this.hasError()) {
      const errorMsg = await this.getErrorMessage();
      throw new Error(`Unexpected error message: ${errorMsg}`);
    }
  }

  /**
   * Execute a complete conversation flow:
   * 1. Wait for agent to be ready
   * 2. Send message
   * 3. Wait for completion
   * 4. Verify no errors
   */
  async executePrompt(
    message: string,
    timeout: number = 120_000,
  ): Promise<void> {
    await this.waitForAgentReady(30_000);
    await this.sendMessage(message);
    await this.waitForTaskCompleteMessage(timeout);
    await this.verifyNoErrors();
  }
}
