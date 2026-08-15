import { expect, test } from "@playwright/test";
import { runUser } from "../utils/config";

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

test("member creates an API key scoped to the selected organization and deletes it", async ({
  page,
}) => {
  const keyName = `e2e-member-key-${Date.now()}`;

  const organizationsResponse = await page.request.get("/api/organizations");
  expect(organizationsResponse.ok()).toBe(true);
  const organizations = (await organizationsResponse.json()) as {
    current_org_id: string | null;
    items: Array<{ id: string; name: string; is_personal: boolean }>;
  };
  const selectedOrganization = organizations.items.find(
    ({ id }) => id === organizations.current_org_id,
  );
  if (!selectedOrganization) {
    throw new Error(
      "The primary auth state must have a selected organization before this test runs.",
    );
  }
  const organizationLabel = selectedOrganization.is_personal
    ? "Personal Workspace"
    : selectedOrganization.name;

  try {
    await page.goto("/settings/api-keys");
    await page.getByRole("button", { name: /create api key/i }).click();

    const createModal = page.getByTestId("create-api-key-modal");
    await expect(createModal).toBeVisible();
    await page.getByTestId("api-key-name-input").fill(keyName);
    await page.getByTestId("api-key-org-selector").click();
    await page.getByRole("option", { name: organizationLabel }).click();
    await page.getByRole("button", { name: /^create$/i }).click();

    const generatedKeyModal = page.getByTestId("new-api-key-modal");
    await expect(generatedKeyModal).toBeVisible();
    await page.getByRole("button", { name: /^close$/i }).click();
    await expect(generatedKeyModal).not.toBeVisible();

    const keyRow = page.locator("tr", { hasText: keyName });
    await expect(keyRow).toContainText(organizationLabel);
  } finally {
    const keysResponse = await page.request.get("/api/keys");
    if (keysResponse.ok()) {
      const keys = (await keysResponse.json()) as Array<{
        id: string;
        name: string;
      }>;
      const createdKey = keys.find(({ name }) => name === keyName);
      if (createdKey) {
        const deleteResponse = await page.request.delete(
          `/api/keys/${encodeURIComponent(createdKey.id)}`,
        );
        expect(deleteResponse.ok()).toBe(true);
      }
    }
  }
});
