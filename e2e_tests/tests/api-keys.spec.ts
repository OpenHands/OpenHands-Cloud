import { test, expect } from "@playwright/test";
import { HomePage } from "../pages";
import { runUser } from "../utils/config";

/**
 * API Keys specs.
 *
 * Ported from saas_deploy's `e2e_tests/tests/smoke.spec.ts` (the API key
 * creation + API access flow). As with the rest of the harness, each spec runs
 * once per user role (returning / new-user); the active role is read from
 * project metadata via `runUser(testInfo)`.
 */

const API_KEY_NAME = "Integration Test Key";

test.describe("api keys", () => {
  test("should be able to create API key and use it to access the API", async ({
    page,
    request,
    baseURL,
  }, testInfo) => {
    const homePage = new HomePage(page);
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    // Navigate to home and open the user menu to reach API Keys.
    await homePage.goto();
    await homePage.openUserMenu();

    const apiKeysLink = page.getByRole("link", { name: /api keys/i });
    await apiKeysLink.click();

    await page.waitForURL(/\/settings\/api-keys/, { timeout: 30_000 });
    console.log("Navigated to API Keys page");

    // The "Refresh API Key" button is only visible when the user has credits.
    const refreshApiKeyButton = page.getByRole("button", { name: /refresh/i });
    await expect(refreshApiKeyButton).toBeVisible({ timeout: 10_000 });
    console.log("Refresh API Key button is visible - user has credits");

    // Delete any existing key with the same name so creation is deterministic.
    const existingKeyRow = page.locator("tr", { hasText: API_KEY_NAME });
    if (await existingKeyRow.isVisible({ timeout: 2_000 }).catch(() => false)) {
      console.log(`Found existing "${API_KEY_NAME}", deleting it...`);
      const deleteButton = existingKeyRow.locator(
        'button[aria-label^="Delete"]',
      );
      await deleteButton.click();

      const deleteModal = page.getByTestId("delete-api-key-modal");
      await expect(deleteModal).toBeVisible({ timeout: 5_000 });
      // The confirm button is the first button in the modal's parent container.
      const confirmDeleteButton = deleteModal
        .locator("xpath=..")
        .getByRole("button")
        .first();
      await confirmDeleteButton.click();

      await expect(deleteModal).not.toBeVisible({ timeout: 5_000 });
      console.log(`Deleted existing "${API_KEY_NAME}"`);

      await page.waitForTimeout(1000);
    }

    // Create a new API key.
    const createApiKeyButton = page.getByRole("button", {
      name: /create api key/i,
    });
    await expect(createApiKeyButton).toBeVisible({ timeout: 10_000 });
    await createApiKeyButton.click();

    const createModal = page.getByTestId("create-api-key-modal");
    await expect(createModal).toBeVisible({ timeout: 5_000 });

    const nameInput = page.getByTestId("api-key-name-input");
    await nameInput.fill(API_KEY_NAME);

    const createButton = page.getByRole("button", { name: /^create$/i });
    await createButton.click();

    // The new key is shown in a modal; capture it before closing.
    const newKeyModal = page.getByTestId("new-api-key-modal");
    await expect(newKeyModal).toBeVisible({ timeout: 10_000 });

    const keyDisplay = newKeyModal.locator(".font-mono");
    const apiKey = await keyDisplay.textContent();
    expect(apiKey).toBeTruthy();
    console.log(`Created API key: ${apiKey?.substring(0, 20)}...`);

    const closeButton = page.getByRole("button", { name: /close/i });
    await closeButton.click();
    await expect(newKeyModal).not.toBeVisible({ timeout: 5_000 });

    await page.screenshot({
      path: "test-results/screenshots/api-keys-created.png",
    });

    // Exercise the API key against /api/v1/sandboxes/search. The currently
    // running conversation's sandbox should appear in the results.
    console.log("Testing API key with sandboxes search endpoint...");
    const response = await request.get(`${baseURL}api/v1/sandboxes/search`, {
      headers: {
        "X-Access-Token": apiKey!,
      },
    });

    expect(response.ok()).toBe(true);
    const responseBody = await response.json();
    console.log(
      `Sandboxes search response: ${JSON.stringify(responseBody).substring(0, 200)}...`,
    );

    // Response format: { items: [], next_page_id: string | null }
    console.log('Found Response Body', responseBody);
    expect(responseBody).toHaveProperty("items");
    expect(Array.isArray(responseBody.items)).toBe(true);
    console.log(
      `Found ${responseBody.items.length} sandbox(es) - API key works!`,
    );

    await page.screenshot({
      path: "test-results/screenshots/api-key-test-complete.png",
    });
  });
});
