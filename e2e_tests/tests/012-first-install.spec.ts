import { test, expect, type APIResponse } from "@playwright/test";

import { fetchWebClientConfig } from "../utils/billing";
import { runUser } from "../utils/config";

/**
 * First-install wizard.
 *
 * On an install where the Super Admin dashboard is enabled before anyone signs
 * in, the first user becomes the instance Super Admin and the server sends
 * them to `/install` after sign-in. The login helper leaves the wizard pending
 * (see `runOnboardingSteps`), and this spec walks it: welcome, company, first
 * organization, then the starter modal on the org LLM settings. It checks that
 * the server records the finished wizard, that `/install` no longer opens, and
 * that the setup guide takes over.
 *
 * It runs only where the returning user is that first Super Admin and the
 * wizard is still pending, i.e. a freshly installed instance. Everywhere else,
 * including the long-lived release instances, it skips. The New User cannot
 * run it: the organization step sends the user's own email as the contact
 * email, and the server rejects the reserved `.e2e.test` domain.
 *
 * Once it has passed, the wizard is finished for good on that instance. To run
 * it again, the first Super Admin can send
 * `PATCH /api/admin/setup-state {"wizard_completed": false}`.
 */

interface SetupState {
  wizard_pending: boolean;
  guide_org_id: string | null;
}

interface InstanceSettings {
  company_name: string | null;
}

async function json<T>(response: APIResponse, operation: string): Promise<T> {
  if (!response.ok()) {
    throw new Error(
      `${operation} failed (${response.status()} ${response.statusText()}): ${await response.text()}`,
    );
  }
  return (await response.json()) as T;
}

// The install steps cross-fade; reduced motion makes each step clickable at once.
test.use({ contextOptions: { reducedMotion: "reduce" } });

test.describe("first install @super-admin", () => {
  // A retry after the wizard finished would skip, hiding the failure.
  test.describe.configure({ retries: 0 });

  test.beforeEach(async ({ page }, testInfo) => {
    test.skip(runUser(testInfo) !== "returning", "returning-user role only");

    const config = await fetchWebClientConfig(page.request);
    test.skip(
      !config.feature_flags?.enable_super_admin,
      "the Super Admin dashboard is disabled (feature_flags.enable_super_admin is falsy)",
    );

    const response = await page.request.get("/api/admin/setup-state");
    const setupState = response.ok()
      ? ((await response.json()) as SetupState)
      : null;
    test.skip(
      !setupState?.wizard_pending,
      "the first-install wizard is not pending for this user; it needs a fresh install",
    );
  });

  test("walks the wizard and hands over to the setup guide", async ({
    page,
  }) => {
    const suffix = `${Date.now()}`;
    const companyName = `E2E Company ${suffix}`;
    const orgName = `E2E First Org ${suffix}`;

    await test.step("welcome", async () => {
      await page.goto("/install");
      await expect(page.getByTestId("super-admin-install-welcome")).toBeVisible(
        { timeout: 30_000 },
      );
      await page.getByTestId("sa-nux-welcome-next").click();
    });

    await test.step("company", async () => {
      await expect(page).toHaveURL(/\/install\/company$/);
      await page.getByTestId("sa-nux-company-name").fill(companyName);
      await page.getByTestId("sa-nux-company-continue").click();
    });

    await test.step("first organization", async () => {
      await expect(page).toHaveURL(/\/install\/org$/);
      const orgNameInput = page.getByTestId("sa-nux-org-name");
      // Wait for the suggested name so it cannot overwrite ours.
      await expect(orgNameInput).toHaveValue(/.+/);
      await orgNameInput.fill(orgName);
      await page.getByTestId("sa-nux-org-continue").click();
    });

    await test.step("starter modal", async () => {
      await expect(page).toHaveURL(/\/settings\/org-defaults$/, {
        timeout: 30_000,
      });
      const modal = page.getByTestId("sa-nux-starter-modal");
      await expect(modal).toBeVisible();
      // Skip keeps the spec independent of the instance's LLM providers.
      await modal.getByTestId("sa-nux-starter-skip").click();
      await expect(modal).toBeHidden();
    });

    await test.step("the server records the finished wizard", async () => {
      const setupState = await json<SetupState>(
        await page.request.get("/api/admin/setup-state"),
        "load setup state",
      );
      expect(setupState.wizard_pending).toBe(false);
      expect(setupState.guide_org_id).toBeTruthy();

      const settings = await json<InstanceSettings>(
        await page.request.get("/api/admin/instance-settings"),
        "load instance settings",
      );
      expect(settings.company_name).toBe(companyName);
    });

    await test.step("the wizard no longer opens", async () => {
      await page.goto("/install");
      await expect(page).toHaveURL(/\/canvas/, { timeout: 30_000 });
    });

    await test.step("the setup guide takes over", async () => {
      await page.goto("/super-admin/setup");
      await expect(page.getByTestId("super-admin-setup")).toBeVisible({
        timeout: 30_000,
      });
      await expect(
        page.getByTestId("super-admin-setup-step-add-llm"),
      ).toBeVisible();
    });
  });
});
