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

  readonly sendButton: Locator;

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
    this.sendButton = page
      .locator(
        'button[type="submit"], button:has-text("Send"), [data-testid*="send"]',
      )
      .first();
    this.stopButton = page
      .locator('button:has-text("Stop"), [data-testid*="stop"]')
      .first();
    this.errorBanner = page.getByTestId("error-message-banner");
    this.waitingMessage = page.locator('[data-testid*="waiting"]').first();
    this.statusIndicator = page.getByTestId("status-icon");
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

    // Wait for agent to be ready by checking for "Waiting for task" text.
    // Note: using text search since data-testid is not yet deployed to staging.
    const readyText = this.page.getByText(/waiting for task/i);
    const outcome = await Promise.race([
      readyText
        .waitFor({ state: "visible", timeout })
        .then(() => "ready" as const)
        .catch(() => "timeout" as const),
      this.errorBanner
        .waitFor({ state: "visible", timeout: ERROR_BANNER_WAIT_MS })
        .then(() => "error" as const)
        .catch(() => "timeout" as const),
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

    // Wait for agent to finish by checking for the "Agent has finished the
    // task" status. Note: using text search since data-testid is not yet
    // deployed to staging. Regex keeps it tolerant of casing/whitespace.
    const finishedTaskText = this.page.getByText(
      /agent has finished the task/i,
    );
    await expect(finishedTaskText).toBeVisible({ timeout });
  }

  /**
   * Wait for the agent to be ready to receive input
   */
  async waitForAgentReady(timeout: number = 90_000): Promise<void> {
    const startTime = Date.now();

    while (Date.now() - startTime < timeout) {
      // Check if there's an error
      if (await this.hasError()) {
        const errorMsg = await this.getErrorMessage();
        throw new Error(`Agent error: ${errorMsg}`);
      }

      // Check if input is enabled (agent is ready)
      const isInputEnabled = await this.isChatInputEnabled();
      if (isInputEnabled) {
        return;
      }

      // Wait a bit before checking again
      await this.page.waitForTimeout(1000);
    }

    throw new Error(`Agent not ready within ${timeout}ms timeout`);
  }

  /**
   * Check if the chat input is enabled
   */
  async isChatInputEnabled(): Promise<boolean> {
    try {
      // contentEditable divs don't have a disabled state, check for pointer-events or class
      const isVisible = await this.chatInput.isVisible();
      if (!isVisible) return false;

      // Check if there's a loading state or disabled class
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
   * Send a message to the agent
   */
  async sendMessage(message: string): Promise<void> {
    // Wait for input to be ready
    await expect(this.chatInput).toBeVisible({ timeout: 30_000 });

    // Clear any existing content and type the message
    await this.chatInput.click();
    await this.chatInput.fill("");
    await this.page.keyboard.type(message);

    // Submit the message
    await this.page.keyboard.press("Enter");

    // Small delay to ensure message is sent
    await this.page.waitForTimeout(500);
  }

  /**
   * Wait for agent to respond (agent starts processing)
   */
  async waitForAgentProcessing(timeout: number = 10_000): Promise<void> {
    const startTime = Date.now();

    while (Date.now() - startTime < timeout) {
      // Check if agent is processing (input disabled or loading indicator visible)
      const isProcessing = await this.isAgentProcessing();
      if (isProcessing) {
        return;
      }

      await this.page.waitForTimeout(500);
    }

    // It's okay if we don't see processing state - agent might have already finished
  }

  /**
   * Check if agent is currently processing
   */
  async isAgentProcessing(): Promise<boolean> {
    // Check for loading indicators or disabled input
    const loadingIndicator = this.page
      .locator(
        '[data-testid*="loading"], [class*="loading"], [class*="spinner"]',
      )
      .first();
    if (
      await loadingIndicator.isVisible({ timeout: 1_000 }).catch(() => false)
    ) {
      return true;
    }

    // Check if input is disabled (indicates processing)
    const isInputEnabled = await this.isChatInputEnabled();
    return !isInputEnabled;
  }

  /**
   * Wait for agent to complete processing and return to ready state
   */
  async waitForAgentComplete(timeout: number = 120_000): Promise<void> {
    const startTime = Date.now();

    // First, wait for processing to start
    await this.waitForAgentProcessing(10_000).catch(() => {});

    // Then wait for processing to complete
    while (Date.now() - startTime < timeout) {
      // Check for errors
      if (await this.hasError()) {
        const errorMsg = await this.getErrorMessage();
        throw new Error(`Agent error during processing: ${errorMsg}`);
      }

      // Check if agent is back to ready state
      const isInputEnabled = await this.isChatInputEnabled();
      if (isInputEnabled) {
        return;
      }

      await this.page.waitForTimeout(1000);
    }

    throw new Error(`Agent did not complete within ${timeout}ms timeout`);
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
      this.errorBanner
        .waitFor({ state: "visible", timeout: ERROR_BANNER_WAIT_MS })
        .then(() => "error" as const)
        .catch(() => "timeout" as const),
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

  /**
   * Verify no error messages are displayed
   */
  async verifyNoErrors(): Promise<void> {
    const hasError = await this.hasError();
    if (hasError) {
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
    // Ensure agent is ready
    await this.waitForAgentReady(30_000);

    // Send the message
    await this.sendMessage(message);

    // Wait for completion
    await this.waitForAgentComplete(timeout);

    // Verify no errors
    await this.verifyNoErrors();
  }
}
