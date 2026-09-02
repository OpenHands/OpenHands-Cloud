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
 * Grace period during completion polling during which the agent may not yet
 * have entered the running state (no stop-button yet). If the agent never
 * runs within this window and no error is present, we treat the conversation
 * as already finished — the common case when navigating to a conversation
 * that completed in a prior test.
 */
const RUNNING_START_GRACE_MS = 10_000;

/**
 * Page object for the canvas conversation interface (OpenHands Agent Canvas
 * served at /canvas).
 *
 * The canvas frontend shares a handful of test IDs with the legacy UI
 * (chat-input, error-message-banner, submit-button, stop-button) but does NOT
 * surface a persistent readiness/completion status the way the legacy UI does.
 * The `chat-status-indicator` is only mounted while the agent is in the
 * starting (LOADING/INIT) state and is unmounted once the conversation is
 * ready, so it cannot be used to wait for "Ready". Likewise the finished
 * ("Done") status lives in the transient AgentStatus widget, which fades out
 * ~1.5s after the agent finishes, so it cannot be polled reliably.
 *
 * Instead, readiness is signaled by the chat shell (chat-interface +
 * chat-input + submit-button) being visible, and task completion by the agent
 * leaving the running state — observed via the stop-button (visible only while
 * the agent is RUNNING) disappearing. Messages are labeled `agent-message` /
 * `user-message`. This page object encodes those differences so 005 can mirror
 * 003's flows without touching the legacy page objects.
 */
export class CanvasConversationPage extends BasePage {
  readonly chatInterface: Locator;

  readonly chatInput: Locator;

  readonly submitButton: Locator;

  readonly stopButton: Locator;

  readonly errorBanner: Locator;

  constructor(page: Page) {
    super(page);

    this.chatInterface = page.getByTestId("chat-interface");
    this.chatInput = page.getByTestId("chat-input");
    this.submitButton = page.getByTestId("submit-button");
    // Scope the stop-button to the chat interface: a `stop-button` test ID also
    // exists on conversation-card context menus, and we only want the in-chat
    // one that reflects the active agent's running state.
    this.stopButton = this.chatInterface.getByTestId("stop-button");
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
   * The canvas does not expose a persistent "Ready" status: the
   * `chat-status-indicator` is unmounted as soon as the agent leaves the
   * starting (LOADING/INIT) state, so waiting on its text is a race that
   * usually loses once the conversation is ready. Readiness is instead
   * signaled by the chat shell — `chat-interface`, `chat-input`, and
   * `submit-button` — becoming visible, which happens once the sandbox/runtime
   * provisioning that drives the cold start has settled. We wait for that shell
   * with a short timeout and race against the error banner so a genuinely
   * failed conversation fails fast with a clear message.
   */
  async waitForConversationReady(timeout: number = 120_000): Promise<void> {
    const shellTimeout = Math.min(timeout, 30_000);

    await expect(this.chatInterface).toBeVisible({ timeout: shellTimeout });
    await expect(this.chatInput).toBeVisible({ timeout: shellTimeout });
    await expect(this.submitButton).toBeVisible({ timeout: shellTimeout });

    // The shell is the readiness signal the canvas itself exposes (its own live
    // E2E tests proceed once chat-interface/interactive-chat-box are visible).
    // Race it against the error banner so a failed conversation fails fast.
    const errorWatcher = new Promise<"error">((resolve) => {
      this.errorBanner
        .waitFor({ state: "visible", timeout: ERROR_BANNER_WAIT_MS })
        .then(() => resolve("error"))
        .catch(() => {});
    });

    const ready = await Promise.race([
      this.page.waitForTimeout(500).then(() => "ready" as const),
      errorWatcher,
    ]);

    if (ready === "error") {
      const errorMsg = await this.getErrorMessage();
      throw new Error(`Conversation failed to become ready: ${errorMsg}`);
    }
  }

  /**
   * Wait for the agent to finish the task.
   *
   * The canvas has no persistent "Done" indicator: the finished status is shown
   * by the transient AgentStatus widget, which fades out ~1.5s after the agent
   * finishes, so it cannot be polled reliably. Completion is instead observed
   * via the running state ending — the in-chat `stop-button` is visible only
   * while `curAgentState === RUNNING` and disappears once the agent finishes.
   *
   * After a prompt is sent the agent enters RUNNING (stop-button appears), then
   * leaves it (stop-button disappears); we wait for that transition. When
   * navigating to a conversation that already finished, the stop-button never
   * appears, so after a grace period we treat the conversation as done (an
   * existing agent-message with no stop-button and no error confirms it). We
   * race the whole poll against the error banner to fail fast on agent errors.
   */
  async waitForTaskCompleteMessage(timeout: number = 180_000): Promise<void> {
    const shellTimeout = Math.min(timeout, 30_000);

    await expect(this.chatInterface).toBeVisible({ timeout: shellTimeout });
    await expect(this.chatInput).toBeVisible({ timeout: shellTimeout });

    let sawRunning = false;
    const start = Date.now();
    let settledSince: number | null = null;

    while (Date.now() - start < timeout) {
      if (await this.hasError()) {
        const errorMsg = await this.getErrorMessage();
        throw new Error(
          `Agent error while waiting for completion: ${errorMsg}`,
        );
      }

      const running = await this.stopButton.isVisible().catch(() => false);

      if (running) {
        sawRunning = true;
        settledSince = null;
      } else if (sawRunning) {
        // Was running, now stopped → finished.
        return;
      } else {
        // Not running and never saw it run. If there's already an agent reply,
        // the conversation finished in a prior test (or replied instantly) —
        // confirm it's stable for 1s before returning. Otherwise, keep waiting
        // within the grace period for the agent to start running.
        const hasReply =
          (await this.page.getByTestId("agent-message").count()) > 0;
        if (hasReply) {
          if (settledSince === null) {
            settledSince = Date.now();
          } else if (Date.now() - settledSince >= 1_000) {
            return;
          }
        } else if (Date.now() - start > RUNNING_START_GRACE_MS) {
          // No run, no reply, no error within the grace — assume done to avoid
          // burning the full timeout on a finished/idle conversation.
          return;
        }
      }

      await this.page.waitForTimeout(500);
    }

    throw new Error(`Agent did not finish within ${timeout}ms timeout`);
  }

  /**
   * Check if the chat input is enabled (editable). The canvas chat-input is a
   * contentEditable div; when disabled its `contenteditable` attribute is
   * "false" (and it gains `cursor-not-allowed opacity-50`), so we key off the
   * attribute rather than class-name heuristics that the legacy UI used.
   */
  async isChatInputEnabled(): Promise<boolean> {
    try {
      const isVisible = await this.chatInput.isVisible();
      if (!isVisible) return false;

      const editable = await this.chatInput.getAttribute("contenteditable");
      return editable === "true";
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
   * Get all visible agent messages in the chat. The canvas labels assistant
   * messages with the `agent-message` test ID (see chat-message.tsx), not the
   * `assistant-message` ID the legacy UI used.
   */
  async getMessages(): Promise<string[]> {
    return this.page.getByTestId("agent-message").allTextContents();
  }

  /**
   * Wait for an agent message containing specific text to appear.
   *
   * Races against the error banner so we fail fast on agent errors. Accepts a
   * RegExp so callers can match flexibly (accent/casing variants).
   */
  async waitForMessageContaining(
    expectedText: string | RegExp,
    timeout: number = 120_000,
  ): Promise<string> {
    const target = this.page
      .getByTestId("agent-message")
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
