import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import fs from "fs";
import { authNewUserFile, runUser } from "../utils/config";

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

type BudgetUser = {
  user_id: string;
  user_email: string | null;
  monthly_limit: number | null;
  effective_monthly_limit: number | null;
  is_disabled: boolean;
  is_override: boolean;
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

test("admin overrides one member budget and restores the prior state", async ({
  browser,
  page,
  baseURL,
}) => {
  const organization = await requireAdminOrganization(page);
  const memberEmail = process.env.TEST_MEMBER_EMAIL;
  const secondaryAuthState =
    process.env.SECONDARY_AUTH_STATE || authNewUserFile;
  if (!memberEmail) {
    throw new Error("TEST_MEMBER_EMAIL must identify an existing member.");
  }
  if (!secondaryAuthState) {
    throw new Error(
      "SECONDARY_AUTH_STATE must point to a Playwright storage-state file for the member.",
    );
  }
  if (!fs.existsSync(secondaryAuthState)) {
    throw new Error(
      `SECONDARY_AUTH_STATE does not exist: ${secondaryAuthState}`,
    );
  }
  if (!baseURL) {
    throw new Error("Playwright baseURL is required for the member context.");
  }

  const headers = { "X-Org-Id": organization.id };
  let secondaryContext: BrowserContext | undefined;
  try {
    secondaryContext = await browser.newContext({
      baseURL,
      storageState: secondaryAuthState,
    });
    const secondaryOrganizationsResponse =
      await secondaryContext.request.get("/api/organizations");
    expect(secondaryOrganizationsResponse.ok()).toBe(true);
    const secondaryOrganizations =
      (await secondaryOrganizationsResponse.json()) as {
        current_org_id: string | null;
      };
    if (secondaryOrganizations.current_org_id !== organization.id) {
      throw new Error(
        `SECONDARY_AUTH_STATE must have organization ${organization.id} selected; received ${secondaryOrganizations.current_org_id ?? "none"}.`,
      );
    }
    const secondaryMeResponse = await secondaryContext.request.get(
      `/api/organizations/${organization.id}/me`,
      { headers },
    );
    expect(secondaryMeResponse.ok()).toBe(true);
    const secondaryMe = (await secondaryMeResponse.json()) as {
      email?: string;
      role?: string;
    };
    if (secondaryMe.email !== memberEmail || secondaryMe.role !== "member") {
      throw new Error(
        `Secondary fixture must be member ${memberEmail}; received ${secondaryMe.email ?? "unknown"} with role ${secondaryMe.role ?? "unknown"}.`,
      );
    }
  } finally {
    await secondaryContext?.close();
  }

  const budgetUrl = `/api/organizations/${organization.id}/budgets`;
  const memberResponse = await page.request.get(budgetUrl, {
    headers,
    params: {
      users_search: memberEmail,
      users_per_page: 1000,
    },
  });
  expect(memberResponse.ok()).toBe(true);
  const memberBudgetData = (await memberResponse.json()) as {
    users: BudgetUser[];
  };
  const matchingMembers = memberBudgetData.users.filter(
    ({ user_email: email }) => email === memberEmail,
  );
  if (matchingMembers.length !== 1) {
    throw new Error(
      `TEST_MEMBER_EMAIL must match exactly one organization budget row; found ${matchingMembers.length}.`,
    );
  }
  const [original] = matchingMembers;
  const overrideUrl = `${budgetUrl}/overrides/${original.user_id}`;
  let testLimit = 20 + (Date.now() % 700_000) / 10_000;
  if (testLimit === original.monthly_limit) {
    testLimit += 0.0001;
  }

  try {
    await page.goto("/settings/budgets");
    await expect(
      page.getByRole("heading", { name: "Budget settings" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "User overrides" }).click();
    await expect(
      page.getByRole("heading", { name: "User budget overrides" }),
    ).toBeVisible();
    await page
      .getByPlaceholder("Search users by name or email...")
      .fill(memberEmail);

    const row = page.getByRole("row").filter({ hasText: memberEmail });
    await expect(row).toHaveCount(1);
    await row.getByRole("button", { name: "Edit" }).click();
    await row.locator('input[type="number"]').fill(String(testLimit));

    const updateResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === overrideUrl &&
        response.request().method() === "PUT",
    );
    await row.getByRole("button", { name: "Save" }).click();
    const updateResponse = await updateResponsePromise;
    expect(updateResponse.ok()).toBe(true);
    const updated = (await updateResponse.json()) as BudgetUser;
    expect(updated.user_email).toBe(memberEmail);
    expect(updated.monthly_limit).toBe(testLimit);
    expect(updated.effective_monthly_limit).toBe(testLimit);
    expect(updated.is_override).toBe(true);
    expect(updated.is_disabled).toBe(false);
    await expect(row).toContainText("Override");
  } finally {
    const cleanupResponse = original.is_override
      ? await page.request.put(overrideUrl, {
          headers,
          data: {
            monthly_limit: original.monthly_limit,
            is_disabled: original.is_disabled,
          },
        })
      : await page.request.delete(overrideUrl, { headers });
    expect
      .soft(
        cleanupResponse.ok(),
        `Failed to restore the member budget override (HTTP ${cleanupResponse.status()}).`,
      )
      .toBe(true);
  }
});
