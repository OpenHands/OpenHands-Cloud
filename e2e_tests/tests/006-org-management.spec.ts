import { test, expect } from "@playwright/test";

import { newUserEmail, runUser, superAdminApiKey } from "../utils/config";
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
 * (outside the browser) using a super-admin API key, then hands off to the UI
 * checks that follow. It runs **only for the "new-user" role**: the
 * returning-user role is skipped inside each test (via ``runUser``) so a run
 * without ``NEW_GITHUB_USERNAME`` is unaffected.
 *
 * Flow:
 *  1. ``POST /api/organizations`` (superadmin) — create an e2e test org and
 *     capture the returned ``org_id``.
 *  2. ``POST /api/organizations/provision-user`` (superadmin, ``X-Org-Id``)
 *     — provision the New User (by email) into the new org. Idempotent, so
 *     re-runs only ensure membership and return the existing API key.
 *
 * The created org name is timestamped so repeated runs do not collide with the
 * server's unique-name constraint (``409 OrgNameExistsError``).
 *
 * The super-admin API key (``SUPER_ADMIN_API_KEY``) must be unbound so the
 * server resolves the target org per-request from the ``X-Org-Id`` header —
 * the superadmin is not a member of the org it creates.
 */
test.describe("org management", () => {
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
    provisioned = await provisionUser(baseURL || "", apiKey, org.id, {
      email,
      role: "member",
    });

    console.log(
      `Provisioned "${provisioned.email}" into org ${org.id} ` +
        `(action=${provisioned.action}, created=${provisioned.created})`,
    );
    expect(provisioned.email).toBe(email);
    expect(provisioned.org_id).toBe(org.id);
    expect(provisioned.api_key).toBeTruthy();
  });
});
