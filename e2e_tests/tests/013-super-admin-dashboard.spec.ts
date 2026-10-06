import {
  test,
  expect,
  type APIResponse,
  type Locator,
  type Page,
} from "@playwright/test";

import { fetchWebClientConfig } from "../utils/billing";

/**
 * Super Admin dashboard.
 *
 * Drives the dashboard as an instance Super Admin, from its entry in the
 * Settings account menu through the instance-level actions, on a throwaway
 * user and two throwaway organizations:
 *  - create an organization with the throwaway user as its owner;
 *  - suspend and resume that organization;
 *  - grant and revoke Super Admin;
 *  - change a membership role, remove the membership and add it back;
 *  - suspend and re-activate the account;
 *  - delete the organization, then the user.
 *
 * The throwaway user and the organization it starts in are created over REST
 * with the signed-in session. It runs under whichever role is a Super Admin and
 * skips when the dashboard is disabled (`feature_flags.enable_super_admin`),
 * when user provisioning is off, or when the signed-in user is not a Super
 * Admin. Everything it creates is deleted afterwards, also when a step fails.
 *
 * Emails use `example.com`: the server rejects the reserved `.test` domain.
 */

interface Organization {
  id: string;
}

interface ProvisionedUser {
  user_id: string;
}

interface AdminUsers {
  users: {
    user_id: string;
    memberships: { org_id: string; role: string }[];
  }[];
}

const CONTACT_NAME = "Super Admin E2E";
const CONTACT_EMAIL = "sa-e2e@example.com";

// What the test created, so afterEach can delete it when a step fails.
// Organizations go first: the server refuses to delete a user who is the last
// owner of one.
let createdOrgIds: string[] = [];
let createdUserId: string | undefined;

async function json<T>(response: APIResponse, operation: string): Promise<T> {
  if (!response.ok()) {
    throw new Error(
      `${operation} failed (${response.status()} ${response.statusText()}): ${await response.text()}`,
    );
  }
  return (await response.json()) as T;
}

/** Open a dashboard page from the desktop sidebar and wait for it. */
async function openDashboardPage(
  page: Page,
  path: string,
  pageTestId: string,
): Promise<void> {
  await page
    .getByTestId("super-admin-navbar")
    .getByTestId(`sidebar-settings-${path}`)
    .click();
  await expect(page.getByTestId(pageTestId)).toBeVisible();
}

/** Open the Manage user modal for one user from the Users page. */
async function openManageUser(
  page: Page,
  email: string,
  userId: string,
): Promise<Locator> {
  await openDashboardPage(page, "/super-admin/users", "super-admin-users");
  await page.getByTestId("super-admin-user-search").fill(email);
  await page.getByTestId(`super-admin-user-actions-${userId}`).click();
  await page.getByTestId(`super-admin-manage-user-${userId}`).click();
  const modal = page.getByTestId("super-admin-groups-modal");
  await expect(modal).toBeVisible();
  return modal;
}

// The Manage user modal slides between its panels; reduced motion turns that off.
test.use({ contextOptions: { reducedMotion: "reduce" } });

test.describe("Super Admin dashboard @super-admin", () => {
  test.beforeEach(async ({ page }) => {
    const config = await fetchWebClientConfig(page.request);
    test.skip(
      !config.feature_flags?.enable_super_admin,
      "the Super Admin dashboard is disabled (feature_flags.enable_super_admin is falsy)",
    );
    test.skip(
      config.user_provisioning_enabled !== true,
      "user provisioning is disabled (user_provisioning_enabled is falsy)",
    );

    const response = await page.request.get("/api/admin/users");
    test.skip(
      !response.ok(),
      `the signed-in user is not a Super Admin (GET /api/admin/users returned ${response.status()})`,
    );
  });

  test.afterEach(async ({ page }) => {
    for (const orgId of createdOrgIds) {
      await page.request
        .delete(`/api/admin/organizations/${orgId}`)
        .catch(() => undefined);
    }
    if (createdUserId) {
      await page.request
        .delete(`/api/admin/users/${createdUserId}`)
        .catch(() => undefined);
    }
    createdOrgIds = [];
    createdUserId = undefined;
  });

  test("manages organizations, Super Admins and users", async ({ page }) => {
    test.setTimeout(6 * 60_000);

    const suffix = `${Date.now()}`;
    const email = `sa-e2e+${suffix}@example.com`;
    const orgBName = `sa-e2e-b-${suffix}`;

    // For the instance's first Super Admin, the setup guide's floating panel
    // starts open and can cover row actions. Collapse it whenever it shows.
    await page.addLocatorHandler(
      page.getByTestId("super-admin-setup-floating-panel"),
      async () => {
        await page.getByTestId("super-admin-setup-floating-toggle").click();
      },
    );

    const { orgAId, userId } =
      await test.step("arrange an organization and a member over REST", async () => {
        const orgA = await json<Organization>(
          await page.request.post("/api/organizations", {
            data: {
              name: `sa-e2e-a-${suffix}`,
              contact_name: CONTACT_NAME,
              contact_email: CONTACT_EMAIL,
            },
          }),
          "create organization A",
        );
        createdOrgIds.push(orgA.id);

        const user = await json<ProvisionedUser>(
          await page.request.post("/api/organizations/provision-user", {
            headers: { "X-Org-Id": orgA.id },
            data: { email, role: "member" },
          }),
          "provision the test user",
        );
        createdUserId = user.user_id;
        return { orgAId: orgA.id, userId: user.user_id };
      });

    await test.step("open the dashboard from Settings", async () => {
      await page.goto("/settings");
      await page
        .getByTestId("settings-navbar-desktop")
        .getByTestId("settings-nav-user-trigger")
        .click();
      await page.getByTestId("settings-nav-super-admin").click();
      await expect(page).toHaveURL(/\/super-admin$/);
      await expect(page.getByTestId("super-admin-dashboard")).toBeVisible();
    });

    const orgBId =
      await test.step("create an organization owned by the test user", async () => {
        await openDashboardPage(
          page,
          "/super-admin/organizations",
          "super-admin-organizations",
        );
        await page.getByTestId("super-admin-create-org").click();
        const form = page.getByTestId("create-organization-form");
        await form.getByTestId("create-organization-name").fill(orgBName);

        const owner = form.getByTestId("create-organization-owner");
        await owner.getByTestId("dropdown-trigger").click();
        await owner.getByRole("combobox").fill(email);
        await page.getByRole("option", { name: email, exact: true }).click();

        await form
          .getByTestId("create-organization-contact-name")
          .fill(CONTACT_NAME);
        await form
          .getByTestId("create-organization-contact-email")
          .fill(CONTACT_EMAIL);

        const created = page.waitForResponse(
          (response) =>
            response.request().method() === "POST" &&
            new URL(response.url()).pathname === "/api/organizations",
        );
        // The page header has a button with the same name.
        await form.getByRole("button", { name: "Create organization" }).click();
        const response = await created;
        expect(response.ok(), "create organization B").toBe(true);
        const orgB = (await response.json()) as Organization;
        createdOrgIds.push(orgB.id);
        await expect(form).toBeHidden();

        const { users } = await json<AdminUsers>(
          await page.request.get("/api/admin/users"),
          "load users",
        );
        const membership = users
          .find((user) => user.user_id === userId)
          ?.memberships.find((item) => item.org_id === orgB.id);
        expect(membership?.role).toBe("owner");
        return orgB.id;
      });

    await test.step("suspend and resume the organization", async () => {
      await page.getByTestId("super-admin-org-search").fill(orgBName);
      const actions = page.getByTestId(`super-admin-org-actions-${orgBId}`);
      const row = page.getByRole("row").filter({ has: actions });

      await actions.click();
      await page.getByTestId(`super-admin-org-suspend-${orgBId}`).click();
      await page.getByTestId("super-admin-org-confirm-submit").click();
      await expect(row).toContainText(/suspended/i);

      await actions.click();
      await page.getByTestId(`super-admin-org-resume-${orgBId}`).click();
      await expect(row).toContainText(/active/i);
    });

    await test.step("grant and revoke Super Admin", async () => {
      await openDashboardPage(
        page,
        "/super-admin/admins",
        "super-admin-admins",
      );
      await page.getByRole("button", { name: "Grant Super Admin" }).click();
      const form = page.getByTestId("super-admin-grant-form");
      await form.getByRole("textbox").fill(email);
      // The page header has a button with the same name.
      await form.getByRole("button", { name: "Grant Super Admin" }).click();
      await expect(form).toBeHidden();

      const actions = page.getByTestId(`super-admin-admin-actions-${userId}`);
      await actions.click();
      await page.getByTestId(`super-admin-admin-revoke-${userId}`).click();
      await page.getByTestId("super-admin-revoke-confirm-submit").click();
      await expect(actions).toHaveCount(0);
    });

    await test.step("change, remove and re-add a membership", async () => {
      const modal = await openManageUser(page, email, userId);
      const roleInOrgA = modal.getByTestId(
        `super-admin-group-current-role-${orgAId}`,
      );

      await roleInOrgA.getByTestId("dropdown-trigger").click();
      await page.getByRole("option", { name: "Admin", exact: true }).click();
      await expect(roleInOrgA.getByRole("combobox")).toHaveValue("Admin");

      await roleInOrgA.getByTestId("dropdown-trigger").click();
      await page
        .getByTestId(`super-admin-group-current-role-${orgAId}-remove`)
        .click();
      await modal.getByTestId("super-admin-groups-remove-confirm").click();
      await expect(roleInOrgA).toHaveCount(0);

      await modal.getByTestId("super-admin-groups-add").click();
      const addToOrgA = modal.getByTestId(`super-admin-group-add-${orgAId}`);
      await addToOrgA.click();
      await page
        .getByTestId(`super-admin-group-add-role-${orgAId}-member`)
        .click();
      await expect(addToOrgA).toHaveCount(0);
      await modal.getByTestId("super-admin-groups-footer-done").click();
      await expect(roleInOrgA.getByRole("combobox")).toHaveValue("Member");
    });

    await test.step("suspend and re-activate the account", async () => {
      const modal = page.getByTestId("super-admin-groups-modal");
      await modal.getByTestId("super-admin-user-suspend").click();
      await modal.getByTestId("super-admin-user-activate").click();
      await expect(modal.getByTestId("super-admin-user-suspend")).toBeVisible();
      await modal.getByTestId("super-admin-groups-footer-close").click();
      await expect(modal).toBeHidden();
    });

    await test.step("delete the organization, then the user", async () => {
      await openDashboardPage(
        page,
        "/super-admin/organizations",
        "super-admin-organizations",
      );
      await page.getByTestId("super-admin-org-search").fill(orgBName);
      const orgActions = page.getByTestId(`super-admin-org-actions-${orgBId}`);
      await orgActions.click();
      await page.getByTestId(`super-admin-org-remove-${orgBId}`).click();
      await page.getByTestId("super-admin-org-confirm-submit").click();
      await expect(orgActions).toHaveCount(0);

      const modal = await openManageUser(page, email, userId);
      await modal.getByTestId("super-admin-user-delete").click();
      await modal.getByTestId("super-admin-user-delete-confirm").click();
      await expect(modal).toBeHidden();
      await expect(
        page.getByTestId(`super-admin-user-actions-${userId}`),
      ).toHaveCount(0);
    });
  });
});
