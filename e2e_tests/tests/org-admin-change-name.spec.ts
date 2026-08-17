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

test("organization admin can change the organization name and restore it", async ({
  page,
}) => {
  const organization = await requireAdminOrganization(page);
  const originalName = organization.name;
  const temporaryName = `${originalName.slice(0, 42)} e2e ${Date.now()}`;
  const headers = { "X-Org-Id": organization.id };

  try {
    await page.goto("/settings/org");
    await expect(page.getByTestId("manage-org-screen")).toBeVisible();

    const nameSection = page.getByTestId("org-name").first();
    await nameSection.getByRole("button", { name: "Change" }).click();
    const updateForm = page.getByTestId("update-org-name-form");
    await updateForm.getByTestId("org-name").fill(temporaryName);
    await updateForm.getByRole("button", { name: "Save" }).click();
    await expect(nameSection).toContainText(temporaryName);

    await nameSection.getByRole("button", { name: "Change" }).click();
    await updateForm.getByTestId("org-name").fill(originalName);
    await updateForm.getByRole("button", { name: "Save" }).click();
    await expect(nameSection).toContainText(originalName);
  } finally {
    const restoreResponse = await page.request.patch(
      `/api/organizations/${organization.id}`,
      { data: { name: originalName }, headers },
    );
    expect
      .soft(
        restoreResponse.ok(),
        `Failed to restore organization name (HTTP ${restoreResponse.status()}).`,
      )
      .toBe(true);
  }
});
