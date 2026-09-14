import { test, expect } from "@playwright/test";

import { newUserEmail, runUser, superAdminApiKey } from "../utils/config";
import { HomePage } from "../pages";
import {
  createOrg,
  provisionUser,
  type OrgResponse,
  type ProvisionUserResponse,
} from "../utils/org-management";

/**
 * Org management specs.
 *
 * This suite exercises the organization-management REST surface directly
 * (outside the browser) using a super-admin API key, then verifies the UI
 * reflects the newly provisioned org. It runs **only for the "new-user" role**:
 * the returning-user role is skipped inside each test (via ``runUser``) so a
 * run without ``NEW_GITHUB_USERNAME`` is unaffected.
 *
 * Flow:
 *  1. ``POST /api/organizations`` (superadmin) — create an e2e test org and
 *     capture the returned ``org_id``.
 *  2. ``POST /api/organizations/provision-user`` (superadmin, ``X-Org-Id``)
 *     — provision the New User (by email) into the new org. Idempotent, so
 *     re-runs only ensure membership and return the existing API key.
 *  3. Open the user-context-menu and verify the ``org-selector`` dropdown lists
 *     exactly two options: the user's "Personal Workspace" and the newly
 *     created org.
 *
 * The created org name is timestamped so repeated runs do not collide with the
 * server's unique-name constraint (``409 OrgNameExistsError``). The name is
 * shared across the tests via module scope; ``test.describe.serial`` keeps the
 * REST setup before the UI check within a single worker.
 *
 * The super-admin API key (``SUPER_ADMIN_API_KEY``) must be unbound so the
 * server resolves the target org per-request from the ``X-Org-Id`` header —
 * the superadmin is not a member of the org it creates.
 */
test.describe.serial("org management", () => {
  // Runs only for the "new-user" role; skipped for "returning". The active
  // role is read from Playwright project metadata inside each test (see
  // runUser), so the skip happens at run time rather than at describe time.
  let org: OrgResponse;
  let provisioned: ProvisionUserResponse;

  test("create an org and provision the new user into it", async ({
    baseURL,
  }, testInfo) => {
    test.skip(runUser(testInfo) !== "new-user", "new-user role only");

    const apiKey = superAdminApiKey();
    const email = newUserEmail();
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");

    // 1. Create the organization.
    org = await createOrg(baseURL || "", apiKey, {
      name: `e2e test org ${timestamp}`,
      contact_name: "Testy Tester",
      contact_email: "e2e_test@openhands.org",
    });

    console.log(`Created org "${org.name}" (id=${org.id})`);
    expect(org.id).toBeTruthy();
    expect(org.name).toContain("e2e test org");
    expect(org.contact_name).toBe("Testy Tester");
    expect(org.contact_email).toBe("e2e_test@openhands.org");

    // 2. Provision the New User into the new org. The endpoint is idempotent:
    //    if the user already exists (e.g. from a prior GitHub login), this
    //    only ensures org membership and returns the existing API key.
    // Admin role so the new user can exercise org-management UI (e.g. the
    // org-rename spec 008). The endpoint is idempotent: if the user already
    // exists, this only ensures org membership and returns the existing API
    // key. The role is only set on first add; an existing member's role is
    // left untouched, so the org must be freshly created by the prior step.
    provisioned = await provisionUser(baseURL || "", apiKey, org.id, {
      email,
      role: "admin",
    });

    console.log(
      `Provisioned "${provisioned.email}" into org ${org.id} ` +
        `(action=${provisioned.action}, created=${provisioned.created})`,
    );
    expect(provisioned.email).toBe(email);
    expect(provisioned.org_id).toBe(org.id);
    expect(provisioned.api_key).toBeTruthy();
  });

  test("user-context-menu org-selector lists the personal workspace and new org", async ({
    page,
  }, testInfo) => {
    test.skip(runUser(testInfo) !== "new-user", "new-user role only");
    // Depends on the prior REST test having created the org. ``serial`` skips
    // this test if that one failed, but guard explicitly for clarity.
    test.skip(!org, "org must be created by the prior test");

    const homePage = new HomePage(page);
    await homePage.goto();
    await homePage.openUserMenu();

    // The org-selector (see enterprise frontend org-selector.tsx) is a Dropdown
    // rendered inside the user-context-menu. Open it via its toggle button and
    // read the option list.
    const orgSelector =
      homePage.accountSettingsMenu.getByTestId("org-selector");
    await expect(orgSelector).toBeVisible({ timeout: 10_000 });

    const trigger = orgSelector.getByTestId("dropdown-trigger");
    await expect(trigger).toBeEnabled({ timeout: 10_000 });
    await trigger.click();

    const listbox = orgSelector.getByRole("listbox");
    await expect(listbox).toBeVisible({ timeout: 10_000 });
    const options = listbox.getByRole("option");
    await expect(options).toHaveCount(2, { timeout: 10_000 });

    const optionTexts = await options.allTextContents();
    console.log(`org-selector options: ${JSON.stringify(optionTexts)}`);
    expect(optionTexts).toContain("Personal Workspace");
    expect(optionTexts).toContain(org.name);
  });
});
