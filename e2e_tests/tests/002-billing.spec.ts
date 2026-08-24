import { test, expect } from "../utils/billing";
import { HomePage } from "../pages";
import { runUser } from "../utils/config";

/**
 * Billing specs.
 *
 * Ported from saas_deploy's `e2e_tests/tests/smoke.spec.ts` (Stripe credit
 * purchase flow). Every spec here is gated on `feature_flags.enable_billing`
 * — see `utils/billing.ts`. When that flag is falsy the whole suite is
 * skipped automatically, so these specs never run against a deployment that
 * doesn't expose billing.
 *
 * As with the rest of the harness, each spec runs once per user role
 * (returning / new-user); the active role is read from project metadata via
 * `runUser(testInfo)`.
 */

const STRIPE_TEST_CARD = {
  number: "5105105105105100",
  expiry: "12/35",
  cvc: "123",
  name: "Testy Tester",
  country: "US",
  postalCode: "12345",
} as const;

const TOP_UP_AMOUNT = 10;

test.describe("Billing @billing", () => {
  test.describe.configure({ mode: "serial" });

  let homePage: HomePage;

  test.beforeEach(async ({ page }) => {
    homePage = new HomePage(page);
  });

  test("should be able to purchase $10 credits via Stripe", async ({
    page,
  }, testInfo) => {
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    // Navigate to home and open the user menu to reach Billing.
    await homePage.goto();
    await homePage.openUserMenu();

    const billingLink = page.getByRole("link", { name: /billing/i });
    await billingLink.click();

    await page.mouse.move(0, 0);
    await page.waitForURL(/\/settings\/billing/, { timeout: 30_000 });
    await expect(page.getByTestId("billing-settings")).toBeVisible({
      timeout: 10_000,
    });

    // Capture the balance before purchasing so we can assert it grew by $10.
    const balanceElement = page.getByTestId("user-balance");
    await expect(balanceElement).toBeVisible({ timeout: 10_000 });
    const initialBalanceText = await balanceElement.textContent();
    const initialBalance = parseFloat(
      initialBalanceText?.replace("$", "") || "0",
    );
    console.log(`Initial balance: $${initialBalance.toFixed(2)}`);

    // Enter the top-up amount and submit.
    const topUpInput = page.getByTestId("top-up-input");
    await topUpInput.fill(String(TOP_UP_AMOUNT));

    const addCreditButton = page.getByRole("button", { name: /add credit/i });
    await expect(addCreditButton).toBeEnabled({ timeout: 5_000 });
    await addCreditButton.click();

    // Stripe Checkout is an external redirect; wait for it to load.
    await page.waitForURL(/checkout\.stripe\.com/, { timeout: 30_000 });
    console.log("Redirected to Stripe checkout");

    const payButton = page.locator(".SubmitButton");
    await payButton.waitFor({ state: "attached", timeout: 30_000 });
    console.log("Stripe checkout form loaded");

    // Fill the test card details.
    await page.locator("#cardNumber").fill(STRIPE_TEST_CARD.number);
    await page.locator("#cardExpiry").fill(STRIPE_TEST_CARD.expiry);
    await page.locator("#cardCvc").fill(STRIPE_TEST_CARD.cvc);
    await page.locator("#billingName").fill(STRIPE_TEST_CARD.name);
    await page
      .locator("#billingCountry")
      .selectOption(STRIPE_TEST_CARD.country);
    await page.locator("#billingPostalCode").fill(STRIPE_TEST_CARD.postalCode);

    await page.screenshot({
      path: "test-results/screenshots/stripe-checkout-filled.png",
    });

    await payButton.click();

    // Wait for the redirect back to the billing page after payment.
    await page.waitForURL(/\/settings\/billing/, { timeout: 60_000 });
    console.log("Returned to billing page after payment");

    // The balance refresh is async; give it a moment before re-reading.
    await page.waitForTimeout(2000);

    await expect(balanceElement).toBeVisible({ timeout: 10_000 });
    const newBalanceText = await balanceElement.textContent();
    const newBalance = parseFloat(newBalanceText?.replace("$", "") || "0");
    console.log(`New balance: $${newBalance.toFixed(2)}`);

    expect(newBalance).toBeCloseTo(initialBalance + TOP_UP_AMOUNT, 2);
    console.log(
      `Balance increased by $${TOP_UP_AMOUNT}: $${initialBalance.toFixed(2)} -> $${newBalance.toFixed(2)}`,
    );

    await page.screenshot({
      path: "test-results/screenshots/billing-after-payment.png",
    });
  });

  test("should show the LLM API key refresh button once credits exist", async ({
    page,
  }, testInfo) => {
    test.info().annotations.push({
      type: "user",
      description: runUser(testInfo),
    });

    await homePage.goto();
    await homePage.openUserMenu();

    const apiKeysLink = page.getByRole("link", { name: /api keys/i });
    await apiKeysLink.click();

    await page.waitForURL(/\/settings\/api-keys/, { timeout: 30_000 });

    // The button appears only once `GET /api/keys/llm/byor` stops answering
    // 402, which needs a non-zero balance.
    const refreshApiKeyButton = page.getByRole("button", { name: /refresh/i });
    await expect(refreshApiKeyButton).toBeVisible({ timeout: 10_000 });
  });
});
