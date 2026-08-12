import { expect, test, type Page } from "@playwright/test";
import fs from "fs";
import path from "path";

const AUTH_STATE = path.resolve(import.meta.dirname, "../fixtures/auth.json");

test.use({ storageState: AUTH_STATE });

type Organization = {
  id: string;
  is_personal?: boolean;
};

type ProfileList = {
  profiles: { name: string }[];
  active_profile: string | null;
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

test("admin changes the default organization LLM profile and restores it", async ({
  page,
}) => {
  const organization = await requireAdminOrganization(page);
  const targetProfile = process.env.TEST_ORG_LLM_PROFILE_NAME;
  if (!targetProfile) {
    throw new Error(
      "TEST_ORG_LLM_PROFILE_NAME must name an existing non-default organization LLM profile.",
    );
  }

  const headers = { "X-Org-Id": organization.id };
  const profilesUrl = `/api/organizations/${organization.id}/profiles`;
  const originalResponse = await page.request.get(profilesUrl, { headers });
  expect(originalResponse.ok()).toBe(true);
  const original = (await originalResponse.json()) as ProfileList;
  if (!original.profiles.some(({ name }) => name === targetProfile)) {
    throw new Error(
      `TEST_ORG_LLM_PROFILE_NAME does not exist in organization ${organization.id}: ${targetProfile}`,
    );
  }
  if (!original.active_profile) {
    throw new Error(
      "The organization must already have an active LLM profile so the test can restore it.",
    );
  }
  if (original.active_profile === targetProfile) {
    throw new Error(
      "TEST_ORG_LLM_PROFILE_NAME must differ from the current active organization profile.",
    );
  }

  const targetActivationUrl = `${profilesUrl}/${encodeURIComponent(targetProfile)}/activate`;
  const originalActivationUrl = `${profilesUrl}/${encodeURIComponent(original.active_profile)}/activate`;

  try {
    await page.goto("/settings/org-defaults");
    const targetRow = page
      .getByTestId("profile-row")
      .filter({ hasText: targetProfile });
    await expect(targetRow).toHaveCount(1);
    await targetRow.getByTestId("profile-menu-trigger").click();

    const activationResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === targetActivationUrl &&
        response.request().method() === "POST",
    );
    await page.getByTestId("profile-set-active").click();
    const activationResponse = await activationResponsePromise;
    expect(activationResponse.ok()).toBe(true);
    await expect(targetRow.getByTestId("profile-active-badge")).toBeVisible();

    const updatedResponse = await page.request.get(profilesUrl, { headers });
    expect(updatedResponse.ok()).toBe(true);
    const updated = (await updatedResponse.json()) as ProfileList;
    expect(updated.active_profile).toBe(targetProfile);
  } finally {
    const cleanupResponse = await page.request.post(originalActivationUrl, {
      headers,
    });
    expect
      .soft(
        cleanupResponse.ok(),
        `Failed to restore organization LLM profile ${original.active_profile} (HTTP ${cleanupResponse.status()}).`,
      )
      .toBe(true);
    if (cleanupResponse.ok()) {
      const restoredResponse = await page.request.get(profilesUrl, { headers });
      expect.soft(restoredResponse.ok()).toBe(true);
      if (restoredResponse.ok()) {
        const restored = (await restoredResponse.json()) as ProfileList;
        expect.soft(restored.active_profile).toBe(original.active_profile);
      }
    }
  }
});
