import { Page, Locator, expect } from "@playwright/test";
import { BasePage } from "./BasePage";

/**
 * Page object for the canvas home screen (OpenHands Agent Canvas served at
 * /canvas), where users start new conversations and view recent conversations.
 *
 * The canvas home screen shares the `home-screen` test ID with the legacy UI
 * but differs in how conversations are started: there is no dedicated
 * "launch new conversation" button. Instead, the user types into the chat
 * input (`chat-input`) and submits (clicking `submit-button` or pressing
 * Enter), which creates a conversation and navigates to
 * `/canvas/conversations/:id`.
 *
 * Recent conversations live in the `conversation-panel` as `conversation-card`
 * elements (there is no `recent-conversations` section like the legacy UI).
 */
export class CanvasHomePage extends BasePage {
  readonly homeScreen: Locator;

  readonly chatInput: Locator;

  readonly submitButton: Locator;

  readonly conversationPanel: Locator;

  readonly repoDropdown: Locator;

  readonly repoLaunchButton: Locator;

  constructor(page: Page) {
    super(page);

    this.homeScreen = page.getByTestId("home-screen");
    this.chatInput = page.getByTestId("chat-input");
    this.submitButton = page.getByTestId("submit-button");
    this.conversationPanel = page.getByTestId("conversation-panel");
    this.repoDropdown = page.getByTestId("git-repo-dropdown");
    this.repoLaunchButton = page.getByTestId("repo-launch-button");
  }

  /**
   * Navigate to the canvas home page.
   *
   * Bypasses BasePage.goto() (which waits on `networkidle` — a state that
   * never fires for this SPA due to persistent WebSocket connections) and
   * instead waits on `domcontentloaded` plus the home-screen container.
   */
  async goto(): Promise<void> {
    await this.page.goto("/canvas");
    await this.page.waitForLoadState("domcontentloaded");
    await this.waitForHomeScreen();
  }

  /**
   * Wait for the canvas home screen to be fully loaded.
   */
  async waitForHomeScreen(): Promise<void> {
    await expect(this.homeScreen).toBeVisible({ timeout: 30_000 });
    await expect(this.chatInput).toBeVisible({ timeout: 30_000 });
  }

  /**
   * Check if the user is logged in by verifying the home screen is visible.
   */
  async isLoggedIn(): Promise<boolean> {
    try {
      await expect(this.homeScreen).toBeVisible({ timeout: 10_000 });
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Start a new conversation by typing a prompt and submitting.
   *
   * Unlike the legacy UI's dedicated launch button, the canvas home starts a
   * conversation by sending the first message directly from the home chat
   * launcher, which creates the conversation and navigates to it.
   *
   * @param prompt - The initial prompt to send (defaults to a simple greeting)
   */
  async startNewConversation(prompt: string = "Hello!"): Promise<void> {
    await expect(this.chatInput).toBeVisible({ timeout: 30_000 });

    await this.chatInput.click();
    await this.page.keyboard.type(prompt);

    if (
      await this.submitButton.isVisible({ timeout: 2_000 }).catch(() => false)
    ) {
      await this.submitButton.click();
    } else {
      await this.page.keyboard.press("Enter");
    }

    // Wait for navigation to the conversation page.
    await this.page
      .waitForURL(/\/canvas\/conversations\//, { timeout: 30_000 })
      .catch(() => {});
  }

  /**
   * Select a repository by searching for it in the repo dropdown.
   *
   * @param repoUrl - Full repository URL (e.g., https://github.com/OpenHands/OpenHands)
   */
  async selectRepository(repoUrl: string): Promise<void> {
    const repoName = repoUrl.split("/").slice(-2).join("/");

    const repoInput = this.repoDropdown;
    await repoInput.focus();
    await repoInput.fill(repoName);

    const roleOption = this.page.getByRole("option").filter({
      has: this.page.getByText(repoName, { exact: true }),
    });
    await expect(roleOption).toBeVisible({ timeout: 10_000 });
    await roleOption.click();
  }

  /**
   * Click on the first conversation in the conversation panel list.
   *
   * Canvas renders recent conversations as `conversation-card` elements inside
   * the `conversation-panel`, each navigating to `/canvas/conversations/:id`.
   */
  async clickFirstConversation(): Promise<void> {
    await expect(this.conversationPanel).toBeVisible({ timeout: 10_000 });

    const firstCard = this.conversationPanel
      .getByTestId("conversation-card")
      .first();
    await expect(firstCard).toBeVisible({ timeout: 10_000 });

    await firstCard.click();

    await this.page.waitForURL(/\/canvas\/conversations\//, {
      timeout: 30_000,
    });
  }
}
