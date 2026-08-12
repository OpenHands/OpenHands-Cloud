import { expect, test, type Page } from "@playwright/test";
import fs from "fs";
import path from "path";

const AUTH_STATE = path.resolve(import.meta.dirname, "../fixtures/auth.json");

test.use({ storageState: AUTH_STATE });

type Organization = {
  id: string;
  is_personal?: boolean;
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

test("organization admin can view recent running conversations", async ({
  page,
}) => {
  const organization = await requireAdminOrganization(page);
  const conversationsResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === `/api/organizations/${organization.id}/conversations` &&
      url.searchParams.get("execution_status") === "running"
    );
  });

  await page.goto("/settings/usage-monitoring");
  await expect(
    page.getByRole("heading", { name: "Usage & Monitoring" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Conversations/ }).click();

  const conversationsResponse = await conversationsResponsePromise;
  expect(conversationsResponse.ok()).toBe(true);
  const conversations = (await conversationsResponse.json()) as {
    items: { execution_status?: string | null }[];
  };
  expect(
    conversations.items.every(
      ({ execution_status: status }) => status?.toLowerCase() === "running",
    ),
  ).toBe(true);

  await expect(
    page.locator("select").filter({ hasText: "All statuses" }),
  ).toHaveValue("running");
  await expect(
    page.getByRole("columnheader", { name: "Last update" }),
  ).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Stop" })).toBeVisible();

  if (conversations.items.length === 0) {
    await expect(
      page.getByText("No conversations found for this time window."),
    ).toBeVisible();
  } else {
    await expect(
      page.getByRole("button", { name: "Stop" }).first(),
    ).toBeVisible();
  }
});
