import type { Page } from "@playwright/test";

import { test, expect } from "../utils/billing";
import { HomePage } from "../pages";
import { runUser, type RunUser } from "../utils/config";

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

type Organization = {
  id: string;
  is_personal?: boolean;
  credits: number | null;
  credits_available?: boolean;
};

type CreditState = {
  credits: number | null;
};

async function currentOrganization(page: Page): Promise<Organization> {
  const response = await page.request.get("/api/organizations");
  expect(response.ok()).toBe(true);
  const result = (await response.json()) as {
    items: Organization[];
    current_org_id: string | null;
  };
  const organization = result.items.find(
    ({ id }) => id === result.current_org_id,
  );
  if (!organization) {
    throw new Error("The active user has no current organization.");
  }
  return organization;
}

async function billingCredits(page: Page): Promise<CreditState> {
  const response = await page.request.get("/api/billing/credits");
  expect(response.ok()).toBe(true);
  const result = (await response.json()) as { credits: string | number | null };
  return {
    credits: result.credits === null ? null : Number(result.credits),
  };
}

async function verifyPersonalGovernanceExclusion(
  page: Page,
  user: RunUser,
  organization: Organization,
): Promise<void> {
  if (user !== "new-user") {
    return;
  }

  expect(
    organization.is_personal,
    "A freshly provisioned user should start in a personal organization.",
  ).toBe(true);
  const response = await page.request.get(
    `/api/organizations/${organization.id}/budgets`,
    { headers: { "X-Org-Id": organization.id } },
  );
  expect(response.status()).toBe(400);
  await expect(response.json()).resolves.toMatchObject({
    detail: "Organization budgets are not available for personal workspaces",
  });
}

test.describe("Billing @billing", () => {
  test.describe.configure({ mode: "serial" });

  let homePage: HomePage;

  test.beforeEach(async ({ page }) => {
    homePage = new HomePage(page);
  });

  test("should be able to purchase $10 credits via Stripe", async ({
    page,
  }, testInfo) => {
    const user = runUser(testInfo);
    test.info().annotations.push({ type: "user", description: user });

    const initialOrganization = await currentOrganization(page);
    await verifyPersonalGovernanceExclusion(page, user, initialOrganization);
    const initialCredits = await billingCredits(page);
    expect(initialOrganization.credits_available).toBe(true);
    expect(initialOrganization.credits).toBe(initialCredits.credits);

    await homePage.goto();
    await homePage.openUserMenu();

    const billingLink = page.getByRole("link", { name: /billing/i });
    await billingLink.click();

    await page.mouse.move(0, 0);
    await page.waitForURL(/\/settings\/billing/, { timeout: 30_000 });
    await expect(page.getByTestId("billing-settings")).toBeVisible({
      timeout: 10_000,
    });

    const balanceElement = page.getByTestId("user-balance");
    await expect(balanceElement).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("$NaN")).toHaveCount(0);
    if (initialCredits.credits === null) {
      await expect(balanceElement).toHaveText(/no budget limit/i);
    } else {
      await expect(balanceElement).toHaveText(
        `$${initialCredits.credits.toFixed(2)}`,
      );
    }

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

    const expectedCredits = (initialCredits.credits ?? 0) + TOP_UP_AMOUNT;
    await expect
      .poll(async () => (await billingCredits(page)).credits, {
        timeout: 30_000,
        intervals: [500, 1000, 2000],
      })
      .toBeCloseTo(expectedCredits, 2);

    await expect(balanceElement).toHaveText(`$${expectedCredits.toFixed(2)}`, {
      timeout: 10_000,
    });
    await expect(page.getByText("$NaN")).toHaveCount(0);

    const updatedOrganization = await currentOrganization(page);
    expect(updatedOrganization.id).toBe(initialOrganization.id);
    expect(updatedOrganization.credits_available).toBe(true);
    expect(updatedOrganization.credits).toBeCloseTo(expectedCredits, 2);

    await page.screenshot({
      path: "test-results/screenshots/billing-after-payment.png",
    });
  });
});
