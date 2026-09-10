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

test("existing automation is visible to its owner but not another organization member", async ({
  browser,
  page,
}, testInfo) => {
  const automationId = process.env.TEST_AUTOMATION_ID;
  if (!automationId) {
    throw new Error(
      "TEST_AUTOMATION_ID is required and must identify an automation owned by the primary member.",
    );
  }
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

    const sharedOrganizations = ownerOrganizations.items.filter(
      ({ id, is_personal }) =>
        !is_personal &&
        secondaryOrganizations.items.some(
          (secondaryOrganization) => secondaryOrganization.id === id,
        ),
    );
    if (sharedOrganizations.length === 0) {
      throw new Error(
        "The primary and secondary auth states must belong to the same non-personal organization.",
      );
    }

    const encodedAutomationId = encodeURIComponent(automationId);
    let fixtureOrganizationId: string | null = null;
    let ownerAutomation: { name: string } | null = null;
    for (const sharedOrganization of sharedOrganizations) {
      const switchResponse = await page.request.post(
        `/api/organizations/${sharedOrganization.id}/switch`,
      );
      expect(switchResponse.ok()).toBe(true);
      const automationResponse = await page.request.get(
        `/api/automation/v1/${encodedAutomationId}`,
      );
      if (automationResponse.ok()) {
        fixtureOrganizationId = sharedOrganization.id;
        ownerAutomation = (await automationResponse.json()) as { name: string };
        break;
      }
    }
    if (!fixtureOrganizationId || !ownerAutomation) {
      throw new Error(
        "TEST_AUTOMATION_ID is not accessible to the primary member in any organization shared with the secondary member.",
      );
    }

    if (secondaryOriginalOrgId !== fixtureOrganizationId) {
      const response = await secondaryPage.request.post(
        `/api/organizations/${fixtureOrganizationId}/switch`,
      );
      expect(response.ok()).toBe(true);
    }

    await page.goto(`/automations/${encodedAutomationId}`);
    await expect(
      page.getByRole("heading", { name: ownerAutomation.name }),
    ).toBeVisible();

    const secondaryAutomationResponse = await secondaryPage.request.get(
      `/api/automation/v1/${encodedAutomationId}`,
    );
    expect(secondaryAutomationResponse.status()).toBe(404);

    await secondaryPage.goto(`/automations/${encodedAutomationId}`);
    await expect(secondaryPage.getByText("Automation not found")).toBeVisible();
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
});
