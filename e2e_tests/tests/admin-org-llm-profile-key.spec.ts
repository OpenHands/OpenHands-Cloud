import { expect, test, type Page } from "@playwright/test";
import { runUser } from "../utils/config";

test.beforeEach(({ page: _page }, testInfo) => {
  test.skip(
    runUser(testInfo) !== "returning",
    "Requires the stable returning-user fixture.",
  );
});

const PROFILE_PREFIX_PATTERN = /^[A-Za-z0-9._-]{1,40}$/;

test.use({ trace: "off" });

type Organization = {
  id: string;
  is_personal?: boolean;
};

type ProfileList = {
  profiles: {
    name: string;
    model: string | null;
    api_key_set: boolean;
  }[];
};

type ProfileDetail = {
  name: string;
  llm: Record<string, unknown>;
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

test("admin sets an organization LLM profile key without exposing it", async ({
  page,
}) => {
  const organization = await requireAdminOrganization(page);
  const profilePrefix = process.env.TEST_ORG_LLM_PROFILE_NAME;
  const model = process.env.TEST_ORG_LLM_MODEL;
  const apiKey = process.env.TEST_ORG_LLM_API_KEY;
  const baseUrl = process.env.TEST_ORG_LLM_BASE_URL;
  if (!profilePrefix) {
    throw new Error(
      "TEST_ORG_LLM_PROFILE_NAME must provide a unique-safe profile name prefix.",
    );
  }
  if (!PROFILE_PREFIX_PATTERN.test(profilePrefix)) {
    throw new Error(
      "TEST_ORG_LLM_PROFILE_NAME must be 1-40 characters using only letters, numbers, dots, underscores, or hyphens.",
    );
  }
  if (!model) {
    throw new Error(
      "TEST_ORG_LLM_MODEL must identify the model for the temporary organization profile.",
    );
  }
  if (!apiKey) {
    throw new Error(
      "TEST_ORG_LLM_API_KEY must provide the temporary profile key.",
    );
  }

  const profileName = `${profilePrefix}-${Date.now().toString(36)}`;
  const headers = { "X-Org-Id": organization.id };
  const profilesUrl = `/api/organizations/${organization.id}/profiles`;
  const profileUrl = `${profilesUrl}/${encodeURIComponent(profileName)}`;
  const initialResponse = await page.request.get(profilesUrl, { headers });
  expect(initialResponse.ok()).toBe(true);
  const initial = (await initialResponse.json()) as ProfileList;
  if (initial.profiles.some(({ name }) => name === profileName)) {
    throw new Error(
      `Generated temporary profile already exists: ${profileName}`,
    );
  }

  let profileCreated = false;
  try {
    const createResponse = await page.request.post(profileUrl, {
      headers,
      data: {
        include_secrets: true,
        llm: {
          model,
          ...(baseUrl ? { base_url: baseUrl } : {}),
          api_key: apiKey,
        },
      },
    });
    expect(createResponse.ok()).toBe(true);
    profileCreated = true;

    const listResponse = await page.request.get(profilesUrl, { headers });
    expect(listResponse.ok()).toBe(true);
    const listed = (await listResponse.json()) as ProfileList;
    const summary = listed.profiles.find(({ name }) => name === profileName);
    expect(summary).toMatchObject({
      name: profileName,
      model,
      api_key_set: true,
    });
    if (JSON.stringify(listed).includes(apiKey)) {
      throw new Error(
        "Organization profile list exposed the supplied API key.",
      );
    }

    const detailResponse = await page.request.get(profileUrl, { headers });
    expect(detailResponse.ok()).toBe(true);
    const detail = (await detailResponse.json()) as ProfileDetail;
    expect(detail.name).toBe(profileName);
    expect(detail.llm.model).toBe(model);
    if (JSON.stringify(detail).includes(apiKey)) {
      throw new Error(
        "Organization profile detail exposed the supplied API key.",
      );
    }

    await page.goto("/settings/org-defaults");
    const profileRow = page
      .getByTestId("profile-row")
      .filter({ hasText: profileName });
    await expect(profileRow).toHaveCount(1);
    await expect(profileRow).toContainText(model);
  } finally {
    if (profileCreated) {
      const cleanupResponse = await page.request.delete(profileUrl, {
        headers,
      });
      expect
        .soft(
          cleanupResponse.ok(),
          `Failed to delete temporary organization LLM profile ${profileName} (HTTP ${cleanupResponse.status()}).`,
        )
        .toBe(true);
    }
  }
});
