import { expect, test } from "@playwright/test";
import fs from "fs";
import path from "path";
import { authNewUserFile, runUser } from "../utils/config";

test.beforeEach(({ page: _page }, testInfo) => {
  test.skip(
    runUser(testInfo) !== "returning",
    "Requires the stable returning-user fixture.",
  );
});

test.use({
  screenshot: "off",
  trace: "off",
  video: "off",
});

test("personal secret is visible to its owner but not another organization member", async ({
  browser,
  page,
}, testInfo) => {
  const secondaryAuthState =
    process.env.SECONDARY_AUTH_STATE || authNewUserFile;
  if (!secondaryAuthState) {
    throw new Error(
      "SECONDARY_AUTH_STATE is required and must point to a second member's Playwright storage state.",
    );
  }
  const secondaryAuthPath = path.resolve(secondaryAuthState);
  if (!fs.existsSync(secondaryAuthPath)) {
    throw new Error(
      `SECONDARY_AUTH_STATE does not exist: ${secondaryAuthPath}`,
    );
  }

  const secondaryContext = await browser.newContext({
    baseURL: testInfo.project.use.baseURL as string,
    storageState: secondaryAuthPath,
  });
  const secondaryPage = await secondaryContext.newPage();
  const secretName = `E2E_MEMBER_SECRET_${Date.now()}`;
  const secretValue = `member-secret-${Date.now()}`;
  let ownerOriginalOrgId: string | null = null;
  let secondaryOriginalOrgId: string | null = null;

  try {
    const ownerOrganizationsResponse =
      await page.request.get("/api/organizations");
    const secondaryOrganizationsResponse =
      await secondaryPage.request.get("/api/organizations");
    expect(ownerOrganizationsResponse.ok()).toBe(true);
    expect(secondaryOrganizationsResponse.ok()).toBe(true);

    const ownerOrganizations = (await ownerOrganizationsResponse.json()) as {
      current_org_id: string | null;
      items: Array<{ id: string; is_personal: boolean }>;
    };
    const secondaryOrganizations =
      (await secondaryOrganizationsResponse.json()) as {
        current_org_id: string | null;
        items: Array<{ id: string; is_personal: boolean }>;
      };
    ownerOriginalOrgId = ownerOrganizations.current_org_id;
    secondaryOriginalOrgId = secondaryOrganizations.current_org_id;

    const sharedOrganization = ownerOrganizations.items.find(
      ({ id, is_personal }) =>
        !is_personal &&
        secondaryOrganizations.items.some(
          (secondaryOrganization) => secondaryOrganization.id === id,
        ),
    );
    if (!sharedOrganization) {
      throw new Error(
        "The primary and secondary auth states must belong to the same non-personal organization.",
      );
    }

    if (ownerOriginalOrgId !== sharedOrganization.id) {
      const response = await page.request.post(
        `/api/organizations/${sharedOrganization.id}/switch`,
      );
      expect(response.ok()).toBe(true);
    }
    if (secondaryOriginalOrgId !== sharedOrganization.id) {
      const response = await secondaryPage.request.post(
        `/api/organizations/${sharedOrganization.id}/switch`,
      );
      expect(response.ok()).toBe(true);
    }

    await page.goto("/settings/secrets");
    await page.getByTestId("add-secret-button").click();
    await page.getByTestId("name-input").fill(secretName);
    await page.getByTestId("value-input").fill(secretValue);
    await page.getByTestId("submit-button").click();

    const ownerSecret = page
      .getByTestId("secret-item")
      .filter({ hasText: secretName });
    await expect(ownerSecret).toBeVisible();

    await secondaryPage.goto("/settings/secrets");
    const secondarySecret = secondaryPage
      .getByTestId("secret-item")
      .filter({ hasText: secretName });
    await expect(secondarySecret).toHaveCount(0);
  } finally {
    try {
      const deleteResponse = await page.request.delete(
        `/api/v1/secrets/${encodeURIComponent(secretName)}`,
      );
      expect(
        [200, 404],
        `Secret cleanup failed with HTTP ${deleteResponse.status()}.`,
      ).toContain(deleteResponse.status());
    } finally {
      if (ownerOriginalOrgId) {
        await page.request.post(
          `/api/organizations/${ownerOriginalOrgId}/switch`,
        );
      }
      if (secondaryOriginalOrgId) {
        await secondaryPage.request.post(
          `/api/organizations/${secondaryOriginalOrgId}/switch`,
        );
      }
      await secondaryContext.close();
    }
  }
});
