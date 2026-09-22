import { Page, Locator, expect } from "@playwright/test";
import { BasePage } from "./BasePage";

/**
 * Page object for the Home screen.
 *
 * The home experience is served by the Agent Canvas SPA (mounted at `/canvas`
 * off the enterprise root). Canvas composes conversations from the
 * `home-chat-launcher` — a chat composer (`chat-input` + `submit-button`) that
 * creates a conversation and navigates directly into it, replacing the older
 * "launch button + repo selector" grid used by the enterprise home. The
 * conversation sidebar (`conversation-panel`) still lists recent conversations
 * as `conversation-card` entries.
 *
 * Enterprise-specific screens (Settings, Billing, API Keys, Budgets, Org
 * management) are unchanged and remain reachable at their existing URLs
 * (`/settings`, `/settings/api-keys`, `/settings/billing`, …). The
 * `openUserMenu`/`logout` helpers therefore navigate to `/settings` — which is
 * where the account nav and Logout button live — rather than hovering an
 * avatar dropdown on the (Canvas) home page.
 */
export class HomePage extends BasePage {
  // Canvas home containers
  readonly homeScreen: Locator;

  readonly chatLauncher: Locator;

  readonly chatInput: Locator;

  readonly submitButton: Locator;

  readonly openRepositoryButton: Locator;

  // Sidebar (Canvas conversation panel)
  readonly conversationPanel: Locator;

  readonly newChatButton: Locator;

  readonly conversationCards: Locator;

  // Enterprise settings shell (reached via /settings)
  readonly settingsScreen: Locator;

  readonly settingsNavUserMenu: Locator;

  /**
   * @deprecated Legacy alias kept so callers that reference `userAvatar` or
   * `accountSettingsMenu` still compile after the Canvas rewrite. Both point
   * at the enterprise Settings screen, which is where account controls live
   * now. Prefer `settingsScreen` in new code.
   */
  readonly userAvatar: Locator;

  readonly accountSettingsMenu: Locator;

  constructor(page: Page) {
    super(page);

    this.homeScreen = page.getByTestId("home-screen");
    this.chatLauncher = page.getByTestId("home-chat-launcher");
    this.chatInput = page.getByTestId("chat-input");
    this.submitButton = page.getByTestId("submit-button");
    this.openRepositoryButton = page.getByTestId("open-repository-button");

    this.conversationPanel = page.getByTestId("conversation-panel");
    this.newChatButton = page.getByTestId(
      "conversation-panel-new-thread-picker",
    );
    this.conversationCards = page.getByTestId("conversation-card");

    this.settingsScreen = page.getByTestId("settings-screen");
    this.settingsNavUserMenu = page.getByTestId("settings-nav-user-menu");
    this.userAvatar = this.settingsNavUserMenu;
    this.accountSettingsMenu = this.settingsScreen;
  }

  /**
   * Navigate to the home page.
   *
   * Bypasses BasePage.goto() (which waits on `networkidle` — a state that
   * never fires for this SPA due to persistent WebSocket/SSE connections)
   * and instead waits on `domcontentloaded` plus concrete page elements.
   * Because the enterprise root redirects to `/canvas`, the observable
   * readiness signal is the Canvas home-screen, not any enterprise element.
   */
  async goto(): Promise<void> {
    await this.page.goto("/");
    await this.page.waitForLoadState("domcontentloaded");
    await this.waitForHomeScreen();
  }

  /**
   * Wait for the Canvas home to be fully loaded.
   *
   * Asserts on `home-screen` (the outer container) *and* `home-chat-launcher`
   * (the composer that replaced the launch-button grid). We deliberately do
   * NOT require `submit-button` to be enabled — it's disabled until an LLM
   * profile is configured, and gating the readiness check on that would
   * couple every home-page test to LLM-configuration state.
   */
  async waitForHomeScreen(): Promise<void> {
    await expect(this.homeScreen).toBeVisible({ timeout: 30_000 });
    await expect(this.chatLauncher).toBeVisible({ timeout: 30_000 });
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
   * Select a repository via the "Open Repository" dialog on the Canvas home.
   *
   * Clicks the `open-repository-button` (which pops the `OpenRepositoryDialog`,
   * rendered with `data-testid="open-repository-dialog-body"`), types the repo
   * name into the dialog's search field, and picks the matching option. The
   * chat composer stays visible with the selection pinned via
   * `home-git-control-bar-preview` — that's the observable side-effect a
   * follow-up `startNewConversation()` builds on.
   *
   * @param repoUrl - Full repository URL (e.g., https://github.com/OpenHands/deploy)
   */
  async selectRepository(repoUrl: string): Promise<void> {
    const repoName = repoUrl.split("/").slice(-2).join("/");

    await this.openRepositoryButton.click();

    const dialog = this.page.getByTestId("open-repository-dialog-body");
    await expect(dialog).toBeVisible({ timeout: 10_000 });

    const search = dialog.getByTestId("cloud-repo-search-input");
    await search.click();
    await search.fill(repoName);

    // The dialog renders options as buttons/rows containing the full name.
    // Prefer role=option (matches the SDK's ARIA combobox output); fall back
    // to a text match inside the dialog for older revisions.
    const option = dialog
      .getByRole("option", { name: new RegExp(repoName, "i") })
      .first();
    const fallback = dialog.getByText(repoName, { exact: false }).first();
    const target = (await option.count()) > 0 ? option : fallback;
    await expect(target).toBeVisible({ timeout: 10_000 });
    await target.click();

    // Confirm the selection so it pins into the launcher's git-control bar.
    const confirm = dialog.getByRole("button", {
      name: /^(open|select|confirm)$/i,
    });
    if (await confirm.isVisible({ timeout: 2_000 }).catch(() => false)) {
      await confirm.click();
    }

    await expect(
      this.page.getByTestId("home-git-control-bar-preview"),
    ).toBeVisible({
      timeout: 10_000,
    });
  }

  /**
   * Start a new conversation from the Canvas home launcher.
   *
   * Canvas doesn't ship a "launch" button that navigates to a launch route;
   * the composer creates the conversation *and* sends the first user message
   * atomically (see `home-chat-launcher.tsx::handleSubmit`). Callers pass the
   * text of the first message (or leave it to a default) — a placeholder is
   * needed because `submit-button` is disabled while `chat-input` is empty.
   *
   * The legacy positional `buttonId` argument (e.g. "launch-new-conversation-button")
   * is retained for source-compatibility with older specs; it is now used only
   * as a hint that a repo/plugin was already picked (`repo-launch-button`) —
   * the click target is always `submit-button` in Canvas.
   *
   * @param prompt - First user message. Falls back to a benign default.
   */
  async startNewConversation(prompt: string = "hello"): Promise<void> {
    await expect(this.chatLauncher).toBeVisible({ timeout: 10_000 });
    await expect(this.chatInput).toBeVisible({ timeout: 10_000 });

    // The chat input is a contentEditable div, not a real input. `.fill()`
    // is unreliable against contentEditables, so set text programmatically
    // and dispatch an InputEvent — same pattern the SDK's own e2e helpers
    // use (see `setChatInput` in OpenHands/OpenHands mock-llm helpers).
    await this.page.evaluate((text) => {
      const el = document.querySelector('[data-testid="chat-input"]');
      if (!(el instanceof HTMLElement)) {
        throw new Error("chat-input not found");
      }
      el.focus();
      el.textContent = text;
      el.dispatchEvent(
        new InputEvent("input", {
          bubbles: true,
          data: text,
          inputType: "insertText",
        }),
      );
    }, prompt);

    await expect(this.submitButton).toBeEnabled({ timeout: 15_000 });
    await this.submitButton.click();

    // The launcher navigates to `/conversations/{id}` once the create-conversation
    // mutation resolves. Match on the SDK path shape.
    await this.page
      .waitForURL(/\/conversations\/[^/]+/, { timeout: 60_000 })
      .catch(() => {});
  }

  /**
   * Open the enterprise account settings.
   *
   * The Canvas home has no user avatar or hover-triggered account dropdown —
   * account controls (org selector, API keys, billing, logout) live in the
   * enterprise Settings shell at `/settings`. Navigating there is the
   * closest equivalent to the old "open user menu" gesture.
   */
  async openUserMenu(): Promise<void> {
    if (!this.page.url().includes("/settings")) {
      await this.page.goto("/settings");
      await this.page.waitForLoadState("domcontentloaded");
    }
    await expect(this.settingsScreen).toBeVisible({ timeout: 15_000 });
  }

  /**
   * Log out via the Settings screen and wait for the login page.
   *
   * Canvas moved the Logout entry into the Settings sidebar's user footer
   * (see the /settings screenshot). We navigate there, click the "Logout"
   * button (matched by accessible name — the button doesn't have a stable
   * data-testid), and wait for Keycloak to redirect us back to /login.
   */
  async logout(): Promise<void> {
    await this.openUserMenu();

    const logoutButton = this.page.getByRole("button", { name: /^logout$/i });
    await expect(logoutButton).toBeVisible({ timeout: 10_000 });
    await logoutButton.click();

    await this.page.waitForURL(/\/login/, { timeout: 30_000 });
  }

  /**
   * List titles/labels of the recent conversations shown in the Canvas sidebar.
   */
  async getRecentConversations(): Promise<string[]> {
    await expect(this.conversationPanel).toBeVisible({ timeout: 10_000 });
    const titles = await this.conversationCards.allTextContents();
    return titles.map((t) => t.trim()).filter((t) => t.length > 0);
  }

  /**
   * Click the first conversation card in the Canvas sidebar.
   *
   * Cards render as anchors inside `conversation-panel` linking to
   * `/conversations/{id}` — clicking one navigates the app into that
   * conversation. We wait for the URL to reflect the navigation so
   * callers can chain assertions on the conversation page.
   */
  async clickFirstConversation(): Promise<void> {
    await expect(this.conversationPanel).toBeVisible({ timeout: 10_000 });
    const firstCard = this.conversationCards.first();
    await expect(firstCard).toBeVisible({ timeout: 10_000 });

    // Prefer clicking the inner link so we get a real navigation instead of
    // relying on the card's onClick — the anchor is guaranteed present, the
    // click handler can lag React hydration by a moment.
    const link = firstCard.locator('a[href^="/conversations/"]').first();
    const target = (await link.count()) > 0 ? link : firstCard;
    await target.click();

    await this.page.waitForURL(/\/conversations\//, { timeout: 30_000 });
  }
}
