import { expect, test, type Page } from "@playwright/test";
import { runUser } from "../utils/config";

test.beforeEach(({ page: _page }, testInfo) => {
  test.skip(
    runUser(testInfo) !== "returning",
    "Requires the stable returning-user fixture.",
  );
});

type Organization = {
  id: string;
  is_personal?: boolean;
};

type BudgetSettings = {
  default_user_monthly_limit: number | null;
};

async function requireAdminOrganization(page: Page): Promise<Organization> {
  const organizationsResponse = await page.request.get("/api/organizations");
  expect(organizationsResponse.ok()).toBe(true);
  const organizations = (await organizationsResponse.json()) as {
    items: Organization[];
    current_org_id: string | null;
  };
  const organization = organizations.items.find(
    ({ id }) => id === organizations.current_org_id,
  );
  if (!organization || organization.is_personal) {
    throw new Error(
      "The admin auth fixture must select a non-personal organization.",
    );
  }

  const meResponse = await page.request.get(
    `/api/organizations/${organization.id}/me`,
    { headers: { "X-Org-Id": organization.id } },
  );
  expect(meResponse.ok()).toBe(true);
  const me = (await meResponse.json()) as { role?: string };
  if (me.role !== "admin" && me.role !== "owner") {
    throw new Error(
      `This test requires an organization admin or owner; received role "${me.role ?? "unknown"}".`,
    );
  }

  return organization;
}

test("admin sets the default budget for new users and restores it", async ({
  page,
}) => {
  const organization = await requireAdminOrganization(page);
  const headers = { "X-Org-Id": organization.id };
  const budgetUrl = `/api/organizations/${organization.id}/budgets`;
  const originalResponse = await page.request.get(budgetUrl, { headers });
  expect(originalResponse.ok()).toBe(true);
  const original = (await originalResponse.json()) as BudgetSettings;

  let testLimit = 10 + (Date.now() % 900_000) / 10_000;
  if (testLimit === original.default_user_monthly_limit) {
    testLimit += 0.0001;
  }

  try {
    await page.goto("/settings/budgets");
    await expect(
      page.getByRole("heading", { name: "Budget settings" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Default budgets" }).click();
    await expect(
      page.getByRole("heading", { name: "Default budget for new users" }),
    ).toBeVisible();
    await page.locator("#default-budget-amount").fill(String(testLimit));

    const updateResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === budgetUrl &&
        response.request().method() === "PATCH",
    );
    await page.getByRole("button", { name: "Save default" }).click();
    const updateResponse = await updateResponsePromise;
    expect(updateResponse.ok()).toBe(true);
    const updated = (await updateResponse.json()) as BudgetSettings;
    expect(updated.default_user_monthly_limit).toBe(testLimit);

    const persistedResponse = await page.request.get(budgetUrl, { headers });
    expect(persistedResponse.ok()).toBe(true);
    const persisted = (await persistedResponse.json()) as BudgetSettings;
    expect(persisted.default_user_monthly_limit).toBe(testLimit);
  } finally {
    const cleanupResponse = await page.request.patch(budgetUrl, {
      headers,
      data: {
        default_user_monthly_limit: original.default_user_monthly_limit,
      },
    });
    expect
      .soft(
        cleanupResponse.ok(),
        `Failed to restore the default user budget (HTTP ${cleanupResponse.status()}).`,
      )
      .toBe(true);
    if (cleanupResponse.ok()) {
      const restored = (await cleanupResponse.json()) as BudgetSettings;
      expect
        .soft(restored.default_user_monthly_limit)
        .toBe(original.default_user_monthly_limit);
    }
  }
});
