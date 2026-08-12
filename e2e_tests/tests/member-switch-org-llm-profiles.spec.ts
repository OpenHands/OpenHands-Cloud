import { expect, test, type Page } from "@playwright/test";
import path from "path";

const authState = path.resolve(import.meta.dirname, "../fixtures/auth.json");

test.use({ storageState: authState });

interface LlmProfilesResponse {
  profiles: Array<{ name: string }>;
  active_profile: string | null;
}

interface ConversationSummary {
  active_profile?: string | null;
}

function requireEnvironment(name: string, guidance: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} is required. ${guidance}`);
  }
  return value;
}

function requireNonProductionTarget(): void {
  const baseUrl = requireEnvironment(
    "BASE_URL",
    "Point it to a non-production OHE deployment.",
  );
  const { hostname } = new URL(baseUrl);
  if (hostname === "app.all-hands.dev") {
    throw new Error(
      "This test creates a conversation and switches its LLM, so production is not allowed.",
    );
  }
}

async function getConversationProfile(
  page: Page,
  conversationId: string,
): Promise<string | null> {
  const response = await page.request.get(
    `/api/v1/app-conversations?ids=${encodeURIComponent(conversationId)}`,
  );
  if (!response.ok()) return null;
  const conversations = (await response.json()) as ConversationSummary[];
  return conversations[0]?.active_profile ?? null;
}

async function switchConversationProfile(
  page: Page,
  conversationId: string,
  profileName: string,
) {
  const switchResponse = page.waitForResponse((response) => {
    const { pathname } = new URL(response.url());
    return (
      response.request().method() === "POST" &&
      pathname.endsWith(
        `/api/v1/app-conversations/${conversationId}/switch_profile`,
      )
    );
  });

  await page.getByTestId("chat-input-llm-profile").click();
  await page
    .getByTestId(`chat-input-llm-profile-option-${profileName}`)
    .click();
  const response = await switchResponse;
  expect(response.ok(), `switching to '${profileName}' should succeed`).toBe(
    true,
  );
  await expect(page.getByTestId("chat-input-llm-profile")).toContainText(
    profileName,
  );
  await expect
    .poll(() => getConversationProfile(page, conversationId), {
      timeout: 60_000,
    })
    .toBe(profileName);
}

test("member switches among organization-provided LLM profiles", async ({
  page,
}) => {
  test.setTimeout(300_000);
  requireNonProductionTarget();
  const profileOne = requireEnvironment(
    "TEST_ORG_LLM_PROFILE_ONE",
    "Provide the name of an org-admin-configured LLM profile visible to the member.",
  );
  const profileTwo = requireEnvironment(
    "TEST_ORG_LLM_PROFILE_TWO",
    "Provide a second org-admin-configured LLM profile visible to the member.",
  );
  const launchPrompt = requireEnvironment(
    "TEST_LLM_SWITCH_PROMPT",
    "Provide a harmless prompt used to create the verification conversation.",
  );
  if (profileOne === profileTwo) {
    throw new Error(
      "TEST_ORG_LLM_PROFILE_ONE and TEST_ORG_LLM_PROFILE_TWO must differ.",
    );
  }
  let conversationId: string | undefined;

  const orgProfilesResponse = page.waitForResponse((response) => {
    const { pathname } = new URL(response.url());
    return (
      response.request().method() === "GET" &&
      /^\/api\/organizations\/[^/]+\/profiles$/.test(pathname)
    );
  });
  await page.goto("/");
  await expect(page.getByTestId("home-chat-launcher")).toBeVisible();

  const profilesResponse = await orgProfilesResponse;
  if (!profilesResponse.ok()) {
    throw new Error(
      `Organization profile request failed with HTTP ${profilesResponse.status()}.`,
    );
  }
  const profilesPath = new URL(profilesResponse.url()).pathname;
  const profiles = (await profilesResponse.json()) as LlmProfilesResponse;
  const availableNames = new Set(profiles.profiles.map(({ name }) => name));
  for (const profileName of [profileOne, profileTwo]) {
    if (!availableNames.has(profileName)) {
      throw new Error(
        `Configured organization profile '${profileName}' was not visible to the member.`,
      );
    }
  }
  if (!profiles.active_profile) {
    throw new Error(
      "The member fixture needs an active LLM profile so unchanged global state can be verified.",
    );
  }

  try {
    await page.getByTestId("chat-input").fill(launchPrompt);
    await page.getByTestId("submit-button").click();
    await page.waitForURL(/\/conversations\/(?!task-)[^/?#]+/, {
      timeout: 180_000,
    });
    conversationId = new URL(page.url()).pathname.split("/").pop();
    expect(conversationId).toBeTruthy();

    await expect
      .poll(() => getConversationProfile(page, conversationId || ""), {
        timeout: 60_000,
      })
      .not.toBeNull();
    const conversationProfile = await getConversationProfile(
      page,
      conversationId || "",
    );
    const firstProfile =
      conversationProfile === profileOne ? profileTwo : profileOne;
    const secondProfile = firstProfile === profileOne ? profileTwo : profileOne;

    await switchConversationProfile(page, conversationId || "", firstProfile);
    await switchConversationProfile(page, conversationId || "", secondProfile);

    const globalResponse = await page.request.get(profilesPath);
    expect(
      globalResponse.ok(),
      "global profile verification should succeed",
    ).toBe(true);
    const globalProfiles = (await globalResponse.json()) as LlmProfilesResponse;
    expect(globalProfiles.active_profile).toBe(profiles.active_profile);
  } finally {
    if (conversationId) {
      const response = await page.request.delete(
        `/api/v1/app-conversations/${encodeURIComponent(conversationId)}`,
      );
      expect
        .soft(response.ok(), "conversation cleanup should succeed")
        .toBe(true);
    }
  }
});
