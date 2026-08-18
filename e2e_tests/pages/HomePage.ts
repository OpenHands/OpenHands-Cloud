import { Page, Locator, expect } from "@playwright/test";
import { BasePage } from "./BasePage";

/**
 * Page object for the Home screen where users start new conversations
 * and view recent conversations.
 */
export class HomePage extends BasePage {
  // Main containers
  readonly homeScreen: Locator;

  readonly newConversationSection: Locator;

  readonly recentConversationsSection: Locator;

  // User avatar and menu
  readonly userAvatar: Locator;

  readonly accountSettingsMenu: Locator;

  // Repository selection
  readonly repoSelector: Locator;

  readonly repoSearchInput: Locator;

  constructor(page: Page) {
    super(page);

    this.homeScreen = page.getByTestId("home-screen");
    this.newConversationSection = page.getByTestId(
      "home-screen-new-conversation-section",
    );
    this.recentConversationsSection = page.getByTestId(
      "home-screen-recent-conversations-section",
    );
    this.userAvatar = page.getByTestId("user-avatar");
    this.accountSettingsMenu = page.getByTestId("user-context-menu");
    this.repoSelector = page.locator('[data-testid*="repo"]').first();
    this.repoSearchInput = page
      .locator('input[placeholder*="repository"], input[placeholder*="repo"]')
      .first();
  }

  /**
   * Navigate to the home page
   *
   * Bypasses BasePage.goto() (which waits on `networkidle` — a state that
   * never fires for this SPA due to persistent WebSocket/SSE connections)
   * and instead waits on `domcontentloaded` plus concrete page elements.
   */
  async goto(): Promise<void> {
    await this.page.goto("/");
    await this.page.waitForLoadState("domcontentloaded");
    await this.waitForHomeScreen();
  }

  /**
   * Wait for the home screen to be fully loaded
   *
   * Waits for the home-screen container and the launch button to be visible
   * and enabled, which signals the page has finished its data-loading phase.
   * Replaces the previous `networkidle` wait that never resolved.
   */
  async waitForHomeScreen(): Promise<void> {
    await expect(this.homeScreen).toBeVisible({ timeout: 30_000 });
    const launchButton = this.page.getByTestId(
      "launch-new-conversation-button",
    );
    await expect(launchButton).toBeVisible({ timeout: 30_000 });
    await expect(launchButton).toBeEnabled({ timeout: 30_000 });
  }

  /**
   * Check if user is logged in by verifying home screen is visible
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
   * Select a repository by searching for it
   * @param repoUrl - Full repository URL (e.g., https://github.com/OpenHands/deploy)
   */
  async selectRepository(repoUrl: string): Promise<void> {
    // Extract repo name from URL
    const repoName = repoUrl.split("/").slice(-2).join("/");

    // Look for repository selector/input and fill it in
    const repoInput = this.page.getByTestId("git-repo-dropdown");
    await repoInput.focus();
    await repoInput.fill(repoName);

    // Click the resulting option
    const roleOption = this.page.getByRole("option").filter({
      has: this.page.getByText(repoName, { exact: true }),
    });
    await expect(roleOption).toBeVisible({ timeout: 10_000 });
    await roleOption.click();
  }

  /**
   * Start a new conversation
   * @param buttonId - Optional test ID of the button to click (default: 'launch-new-conversation-button')
   */
  async startNewConversation(
    buttonId: string = "launch-new-conversation-button",
  ): Promise<void> {
    const startButton = this.page.getByTestId(buttonId);
    if (await startButton.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await startButton.click();
    }

    // Wait for conversation/chat interface to load
    await this.page
      .waitForURL(/conversation|chat|app/, { timeout: 30_000 })
      .catch(() => {});
  }

  /**
   * Open user settings menu
   *
   * Note: The menu is conditionally rendered based on async state (config loaded,
   * user authenticated, etc.). We need to wait for the menu element to be attached
   * to the DOM before we can interact with it. The menu appears on hover over the
   * user-actions container, or when clicking the avatar toggles state.
   */
  async openUserMenu(): Promise<void> {
    // First, wait for the user avatar to be visible
    await expect(this.userAvatar).toBeVisible({ timeout: 10_000 });

    // Wait for the menu to be attached to the DOM (may not be visible yet)
    // This ensures the async config/auth state has loaded
    await this.accountSettingsMenu.waitFor({
      state: "attached",
      timeout: 15_000,
    });

    // Now hover over the user-actions container to trigger the menu visibility
    // The menu uses CSS group-hover to show, so we need to hover the parent
    const userActionsContainer = this.page.getByTestId("user-actions");
    await userActionsContainer.hover();

    // Wait for the menu to become visible
    await expect(this.accountSettingsMenu).toBeVisible({ timeout: 5_000 });
  }

  /**
   * Get list of recent conversations
   */
  async getRecentConversations(): Promise<string[]> {
    await this.waitForElement(this.recentConversationsSection);
    const conversations = await this.recentConversationsSection
      .locator("a, button, [role='button']")
      .allTextContents();
    return conversations.filter((text) => text.trim().length > 0);
  }

  /**
   * Click on the first conversation in the recent conversations list
   * The conversations are displayed as links in the recent-conversations section
   */
  async clickFirstConversation(): Promise<void> {
    // Wait for recent conversations section to be visible
    const recentConversations = this.page.getByTestId("recent-conversations");
    await expect(recentConversations).toBeVisible({ timeout: 10_000 });

    // Find the first conversation link (they link to /conversations/{id})
    const firstConversationLink = recentConversations
      .locator('a[href^="/conversations/"]')
      .first();
    await expect(firstConversationLink).toBeVisible({ timeout: 10_000 });

    // Click the conversation
    await firstConversationLink.click();

    // Wait for navigation to conversation page
    await this.page.waitForURL(/\/conversations\//, { timeout: 30_000 });
  }
}
