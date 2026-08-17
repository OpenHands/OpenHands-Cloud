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
  enabled: boolean;
  monthly_limit: number | null;
  reset_day: number;
  default_user_monthly_limit: number | null;
  slack_channel: string | null;
  slack_team_id: string | null;
  thresholds: {
    percentage: number;
    email_enabled: boolean;
    slack_enabled: boolean;
  }[];
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

test("organization admin can set the default budget for new users and restore it", async ({
  page,
}) => {
  const organization = await requireAdminOrganization(page);
  const headers = { "X-Org-Id": organization.id };
  const budgetUrl = `/api/organizations/${organization.id}/budgets`;
  const originalResponse = await page.request.get(budgetUrl, { headers });
  expect(originalResponse.ok()).toBe(true);
  const original = (await originalResponse.json()) as BudgetSettings;
  const temporaryDefault = Math.ceil(
    (original.default_user_monthly_limit ?? 0) + 113,
  );

  try {
    await page.goto("/settings/budgets");
    await expect(
      page.getByRole("heading", { name: "Budget settings" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Default budgets" }).click();
    await expect(
      page.getByRole("heading", { name: "Default budget for new users" }),
    ).toBeVisible();

    const defaultInput = page.locator("#default-budget-amount");
    await defaultInput.fill(String(temporaryDefault));
    await expect(
      page.getByText(
        `New users get up to $${temporaryDefault.toLocaleString()} per month before requiring an increase.`,
      ),
    ).toBeVisible();

    const updateResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === budgetUrl &&
        response.request().method() === "PATCH",
    );
    await page.getByRole("button", { name: "Save default" }).click();
    const updateResponse = await updateResponsePromise;
    expect(updateResponse.ok()).toBe(true);
    const updated = (await updateResponse.json()) as BudgetSettings;
    expect(updated.default_user_monthly_limit).toBe(temporaryDefault);

    await defaultInput.fill(
      original.default_user_monthly_limit === null
        ? ""
        : String(original.default_user_monthly_limit),
    );
    const restoreResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === budgetUrl &&
        response.request().method() === "PATCH",
    );
    await page.getByRole("button", { name: "Save default" }).click();
    const restoreResponse = await restoreResponsePromise;
    expect(restoreResponse.ok()).toBe(true);
    const restored = (await restoreResponse.json()) as BudgetSettings;
    expect(restored.default_user_monthly_limit).toBe(
      original.default_user_monthly_limit,
    );
  } finally {
    const cleanupResponse = await page.request.patch(budgetUrl, {
      headers,
      data: {
        enabled: original.enabled,
        monthly_limit: original.monthly_limit,
        reset_day: original.reset_day,
        default_user_monthly_limit: original.default_user_monthly_limit,
        slack_channel: original.slack_channel,
        slack_team_id: original.slack_team_id,
        thresholds: original.thresholds,
      },
    });
    expect
      .soft(
        cleanupResponse.ok(),
        `Failed to restore budget settings (HTTP ${cleanupResponse.status()}).`,
      )
      .toBe(true);
  }
});
