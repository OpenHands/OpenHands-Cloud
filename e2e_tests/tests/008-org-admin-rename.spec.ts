import { expect, test, type Page } from "@playwright/test";
import { runUser } from "../utils/config";

/**
 * Org admin rename spec.
 *
 * This suite proves an organization admin can rename the selected
 * non-personal organization and restore the original name. It runs **only
 * for the "new-user" role**: the returning-user role is skipped inside
 * each test (via ``runUser``) so a run without ``NEW_GITHUB_USERNAME`` is
 * unaffected.
 *
 * The new user is provisioned as an ``admin`` of a fresh, non-personal
 * organization by the org-management spec (``006-org-management``), which
 * leaves the org in place as a fixture for subsequent specs. This spec
 * re-derives that org at runtime via ``GET /api/organizations`` so it does
 * not depend on cross-file state.
 *
 * Cleanup: restores the original organization name in the UI and again
 * through the API in ``finally``.
 *
 * Risk: medium — temporarily changes the organization name. The org is
 * dedicated to e2e runs, so avoid concurrent organization-setting tests.
 */
test.beforeEach(({ page: _page }, testInfo) => {
  test.skip(
    runUser(testInfo) !== "new-user",
    "Requires the new-user role provisioned as an org admin.",
  );
});

test.use({
  screenshot: "off",
  trace: "off",
  video: "off",
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
      "The new-user fixture must select a non-personal organization.",
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
