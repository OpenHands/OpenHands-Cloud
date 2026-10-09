import { Page, Locator, expect } from "@playwright/test";

/**
 * Base page object class that provides common functionality
 * for all page objects in the test suite.
 */
export class BasePage {
  readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  /**
   * Navigate to a specific path
   */
  async goto(path: string = "/"): Promise<void> {
    await this.page.goto(path);
    await this.waitForPageLoad();
  }

  /**
   * Wait for the page to fully load
   */
  async waitForPageLoad(): Promise<void> {
    await this.page
      .waitForLoadState("networkidle", { timeout: 30_000 })
      .catch(() => {});
    await this.page.waitForLoadState("domcontentloaded");
  }

  /**
   * Wait for an element to be visible
   */
  async waitForElement(
    locator: Locator,
    timeout: number = 30_000,
  ): Promise<void> {
    await expect(locator).toBeVisible({ timeout });
  }

  /**
   * Wait for an element to be hidden
   */
  async waitForElementHidden(
    locator: Locator,
    timeout: number = 30_000,
  ): Promise<void> {
    await expect(locator).toBeHidden({ timeout });
  }

  /**
   * Take a screenshot with a descriptive name
   */
  async screenshot(name: string): Promise<void> {
    await this.page.screenshot({
      path: `test-results/screenshots/${name}-${Date.now()}.png`,
      fullPage: true,
    });
  }

  /**
   * Check if an error banner is visible
   */
  async hasError(): Promise<boolean> {
    const errorBanner = this.page.getByTestId("error-message-banner");
    return errorBanner.isVisible().catch(() => false);
  }

  /**
   * Get error message if error banner is present
   */
  async getErrorMessage(): Promise<string | null> {
    const errorBanner = this.page.getByTestId("error-message-banner");
    if (await errorBanner.isVisible().catch(() => false)) {
      return errorBanner.textContent();
    }
    return null;
  }

  /**
   * Wait for network to be idle
   */
  async waitForNetworkIdle(timeout: number = 10_000): Promise<void> {
    await this.page
      .waitForLoadState("networkidle", { timeout })
      .catch(() => {});
  }
}
