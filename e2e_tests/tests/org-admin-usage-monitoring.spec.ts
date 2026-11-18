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
  name: string;
  is_personal?: boolean;
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

test("organization admin can view Usage & Monitoring", async ({ page }) => {
  const organization = await requireAdminOrganization(page);

  await page.goto("/settings/usage-monitoring");
  await expect(page).toHaveURL(/\/settings\/usage-monitoring$/);
  await expect(
    page.getByRole("heading", { name: "Usage & Monitoring" }),
  ).toBeVisible();
  await expect(
    page.getByText(
      `Monitor adoption, spend, and ROI across ${organization.name}.`,
    ),
  ).toBeVisible();
  await expect(page.getByText("Conversations With Usage")).toBeVisible();
  await expect(page.getByText("Active Conversations")).toBeVisible();
  await expect(page.getByText("Avg Cost / Conversation")).toBeVisible();
  await expect(page.getByText(/Total Spend/)).toBeVisible();
});
