import { expect, test, type Page } from "@playwright/test";
import fs from "fs";
import path from "path";

const AUTH_STATE = path.resolve(import.meta.dirname, "../fixtures/auth.json");

test.use({ storageState: AUTH_STATE });

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
  current_spend: number;
  thresholds: {
    percentage: number;
    email_enabled: boolean;
    slack_enabled: boolean;
  }[];
};

async function requireAdminOrganization(page: Page): Promise<Organization> {
  if (!fs.existsSync(AUTH_STATE)) {
    throw new Error(
      `Missing admin authentication fixture: ${AUTH_STATE}. Create e2e_tests/fixtures/auth.json for an organization admin before running this test.`,
    );
  }

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

test("organization admin can set an organizational budget and restore it", async ({
  page,
}) => {
  const organization = await requireAdminOrganization(page);
  const headers = { "X-Org-Id": organization.id };
  const budgetUrl = `/api/organizations/${organization.id}/budgets`;
  const originalResponse = await page.request.get(budgetUrl, { headers });
  expect(originalResponse.ok()).toBe(true);
  const original = (await originalResponse.json()) as BudgetSettings;
  const temporaryLimit = Math.max(
    Math.ceil(original.current_spend) + 137,
    Math.ceil(original.monthly_limit ?? 0) + 137,
  );

  try {
    await page.goto("/settings/budgets");
    await expect(
      page.getByRole("heading", { name: "Budget settings" }),
    ).toBeVisible();
    const enabledSwitch = page.getByRole("switch", {
      name: "Enable organization budget",
    });
    if ((await enabledSwitch.getAttribute("aria-checked")) !== "true") {
      await enabledSwitch.click();
    }

    await page.locator("#org-monthly-limit").fill(String(temporaryLimit));
    const updateResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === budgetUrl &&
        response.request().method() === "PATCH",
    );
    await page.getByRole("button", { name: "Save changes" }).click();
    const updateResponse = await updateResponsePromise;
    expect(updateResponse.ok()).toBe(true);
    const updated = (await updateResponse.json()) as BudgetSettings;
    expect(updated.enabled).toBe(true);
    expect(updated.monthly_limit).toBe(temporaryLimit);

    await page
      .locator("#org-monthly-limit")
      .fill(
        original.monthly_limit === null ? "" : String(original.monthly_limit),
      );
    await page
      .locator("#org-billing-cycle")
      .selectOption(original.reset_day === 15 ? "15th" : "1st");
    if (
      (await enabledSwitch.getAttribute("aria-checked")) !==
      String(original.enabled)
    ) {
      await enabledSwitch.click();
    }

    const restoreResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === budgetUrl &&
        response.request().method() === "PATCH",
    );
    await page.getByRole("button", { name: "Save changes" }).click();
    const restoreResponse = await restoreResponsePromise;
    expect(restoreResponse.ok()).toBe(true);
    const restored = (await restoreResponse.json()) as BudgetSettings;
    expect(restored.enabled).toBe(original.enabled);
    expect(restored.monthly_limit).toBe(original.monthly_limit);
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
