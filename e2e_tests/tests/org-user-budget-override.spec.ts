import { expect, test, type Page } from "@playwright/test";
import fs from "fs";
import path from "path";

const AUTH_STATE = path.resolve(import.meta.dirname, "../fixtures/auth.json");

test.use({ storageState: AUTH_STATE });

type Organization = {
  id: string;
  is_personal?: boolean;
};

type BudgetUser = {
  user_id: string;
  user_email: string | null;
  current_spend: number;
  monthly_limit: number | null;
  effective_monthly_limit: number | null;
  is_disabled: boolean;
  is_override: boolean;
};

type BudgetSettings = {
  users: BudgetUser[];
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

test("organization admin can override one user budget and restore the prior state", async ({
  page,
}) => {
  const organization = await requireAdminOrganization(page);
  const memberEmail = process.env.TEST_MEMBER_EMAIL;
  if (!memberEmail) {
    throw new Error(
      "TEST_MEMBER_EMAIL must identify an existing member whose budget override may be changed.",
    );
  }

  const headers = { "X-Org-Id": organization.id };
  const budgetUrl = `/api/organizations/${organization.id}/budgets`;
  const originalResponse = await page.request.get(budgetUrl, {
    headers,
    params: { users_search: memberEmail, users_per_page: 100 },
  });
  expect(originalResponse.ok()).toBe(true);
  const budget = (await originalResponse.json()) as BudgetSettings;
  const original = budget.users.find(
    ({ user_email }) => user_email === memberEmail,
  );
  if (!original) {
    throw new Error(
      `TEST_MEMBER_EMAIL ${memberEmail} is not a member of organization ${organization.id}.`,
    );
  }

  const overrideUrl = `${budgetUrl}/overrides/${original.user_id}`;
  const temporaryAmount = Math.ceil(
    Math.max(
      original.current_spend,
      original.effective_monthly_limit ?? 0,
      original.monthly_limit ?? 0,
    ) + 97,
  );

  try {
    await page.goto("/settings/budgets");
    await expect(
      page.getByRole("heading", { name: "Budget settings" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "User overrides" }).click();
    await page
      .getByPlaceholder("Search users by name or email...")
      .fill(memberEmail);

    const userRow = page.getByRole("row").filter({ hasText: memberEmail });
    await expect(userRow).toBeVisible();
    await userRow.getByRole("button", { name: "Edit" }).click();
    await userRow.locator('input[type="number"]').fill(String(temporaryAmount));

    const updateResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === overrideUrl &&
        response.request().method() === "PUT",
    );
    await userRow.getByRole("button", { name: "Save" }).click();
    const updateResponse = await updateResponsePromise;
    expect(updateResponse.ok()).toBe(true);
    const updated = (await updateResponse.json()) as BudgetUser;
    expect(updated.is_override).toBe(true);
    expect(updated.is_disabled).toBe(false);
    expect(updated.monthly_limit).toBe(temporaryAmount);
    await expect(userRow).toContainText("Override");
    await expect(userRow).toContainText(`$${temporaryAmount.toLocaleString()}`);

    if (!original.is_override) {
      const removeResponsePromise = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === overrideUrl &&
          response.request().method() === "DELETE",
      );
      await userRow
        .locator('button[aria-label^="Remove override for"]')
        .click();
      const removeResponse = await removeResponsePromise;
      expect(removeResponse.status()).toBe(204);
      await expect(userRow).not.toContainText("Override");
    } else {
      await userRow.getByRole("button", { name: "Edit" }).click();
      const disabledCheckbox = userRow.locator('input[type="checkbox"]');
      if (original.is_disabled) {
        await disabledCheckbox.check();
      } else {
        await disabledCheckbox.uncheck();
        await userRow
          .locator('input[type="number"]')
          .fill(String(original.monthly_limit));
      }

      const restoreResponsePromise = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === overrideUrl &&
          response.request().method() === "PUT",
      );
      await userRow.getByRole("button", { name: "Save" }).click();
      const restoreResponse = await restoreResponsePromise;
      expect(restoreResponse.ok()).toBe(true);
      const restored = (await restoreResponse.json()) as BudgetUser;
      expect(restored.is_disabled).toBe(original.is_disabled);
      expect(restored.monthly_limit).toBe(original.monthly_limit);
    }
  } finally {
    const cleanupResponse = original.is_override
      ? await page.request.put(overrideUrl, {
          headers,
          data: {
            monthly_limit: original.is_disabled ? null : original.monthly_limit,
            is_disabled: original.is_disabled,
          },
        })
      : await page.request.delete(overrideUrl, { headers });
    expect
      .soft(
        cleanupResponse.ok(),
        `Failed to restore user budget override (HTTP ${cleanupResponse.status()}).`,
      )
      .toBe(true);
  }
});
