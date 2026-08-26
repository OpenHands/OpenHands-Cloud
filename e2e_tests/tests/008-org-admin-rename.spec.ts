import { expect, test, type Page } from "@playwright/test";
import { runUser } from "../utils/config";
import { HomePage } from "../pages";

/**
 * Org admin rename spec.
 *
 * This suite proves an organization admin can rename a non-personal
 * organization and restore the original name. It runs **only for the
 * "new-user" role**: the returning-user role is skipped inside each test
 * (via ``runUser``) so a run without ``NEW_GITHUB_USERNAME`` is unaffected.
 *
 * The new user is provisioned as an ``admin`` of a fresh, non-personal
 * organization by the org-management spec (``006-org-management``), which
 * leaves the org in place as a fixture for subsequent specs. This spec
 * re-derives that org at runtime via ``GET /api/organizations`` and selects
 * it through the user-context-menu org-selector if it is not already active,
 * so it does not depend on cross-file state or a pre-selected org.
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

type OrganizationsResponse = {
  items: Organization[];
  current_org_id: string | null;
};

/**
 * Resolve and select the non-personal organization the new user was
 * provisioned into. If it is not already the active org, switch to it via
 * the user-context-menu org-selector so the org-settings page reflects it.
 * Then verify the caller is an admin/owner of that org.
 */
async function selectAdminOrganization(page: Page): Promise<Organization> {
  const organizationsResponse = await page.request.get("/api/organizations");
  expect(organizationsResponse.ok()).toBe(true);
  const organizations =
    (await organizationsResponse.json()) as OrganizationsResponse;

  const target = organizations.items.find((org) => !org.is_personal);
  if (!target) {
    throw new Error(
      "No non-personal organization found for the new-user fixture. " +
        "Ensure 006-org-management runs before this spec.",
    );
  }

  // Switch to the target org via the UI org-selector if it is not already
  // active. The org-settings page reflects the currently selected org, so
  // we must be on the target org before navigating there.
  if (organizations.current_org_id !== target.id) {
    const homePage = new HomePage(page);
    await homePage.goto();
    await homePage.openUserMenu();

    const orgSelector =
      homePage.accountSettingsMenu.getByTestId("org-selector");
    await expect(orgSelector).toBeVisible({ timeout: 10_000 });

    const trigger = orgSelector.getByTestId("dropdown-trigger");
    await expect(trigger).toBeEnabled({ timeout: 10_000 });
    await trigger.click();

    const listbox = orgSelector.getByRole("listbox");
    await expect(listbox).toBeVisible({ timeout: 10_000 });
    const option = listbox.getByRole("option", { name: target.name });
    await expect(option).toBeVisible({ timeout: 10_000 });
    await option.click();

    // Wait for the selection to propagate so subsequent API/UI calls see
    // the updated current_org_id.
    await expect
      .poll(
        async () => {
          const res = await page.request.get("/api/organizations");
          const body = (await res.json()) as OrganizationsResponse;
          return body.current_org_id;
        },
        { timeout: 10_000 },
      )
      .toBe(target.id);
  }

  // Verify admin/owner role within the target org.
  const meResponse = await page.request.get(
    `/api/organizations/${target.id}/me`,
    { headers: { "X-Org-Id": target.id } },
  );
  expect(meResponse.ok()).toBe(true);
  const me = (await meResponse.json()) as { role?: string };
  if (me.role !== "admin" && me.role !== "owner") {
    throw new Error(
      `This test requires an organization admin or owner; received role "${me.role ?? "unknown"}".`,
    );
  }

  return target;
}

test("organization admin can change the organization name and restore it", async ({
  page,
}) => {
  const organization = await selectAdminOrganization(page);
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
