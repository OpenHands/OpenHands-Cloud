import { expect, test, type Page, type Response } from "@playwright/test";
import { runUser } from "../utils/config";

test.beforeEach(({ page: _page }, testInfo) => {
  test.skip(
    runUser(testInfo) !== "returning",
    "Requires the stable returning-user fixture.",
  );
});

interface LlmProfile {
  name: string;
}

interface LlmProfilesResponse {
  profiles: LlmProfile[];
  active_profile: string | null;
}

interface AgentProfile {
  id: string | null;
  name: string;
  agent_kind: string;
  llm_profile_ref: string | null;
}

interface AgentProfilesResponse {
  profiles: AgentProfile[];
  active_agent_profile_id: string | null;
}

interface ConversationSummary {
  active_profile?: string | null;
  launched_agent_profile?: {
    agent_profile_id?: string | null;
  } | null;
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
      "This test changes active profiles and creates a conversation, so production is not allowed.",
    );
  }
}

async function requireJson<T>(response: Response, label: string): Promise<T> {
  if (!response.ok()) {
    throw new Error(`${label} request failed with HTTP ${response.status()}.`);
  }
  return response.json() as Promise<T>;
}

async function selectAgentProfile(page: Page, profileName: string) {
  await page.getByTestId("chat-plus-button").click();
  await page.getByTestId("switch-agent-profile-button").click();
  await page
    .getByTestId(`chat-input-agent-profile-option-${profileName}`)
    .click();
}

async function selectLlmProfile(page: Page, profileName: string) {
  await page.getByTestId("chat-input-llm-profile").click();
  await page
    .getByTestId(`chat-input-llm-profile-option-${profileName}`)
    .click();
  await expect(page.getByTestId("chat-input-llm-profile")).toContainText(
    profileName,
  );
}

test("pre-launch LLM selection overrides the agent profile LLM", async ({
  page,
}) => {
  test.setTimeout(300_000);
  requireNonProductionTarget();
  const selectedLlmProfile = requireEnvironment(
    "TEST_PRELAUNCH_LLM_PROFILE",
    "Provide an existing selectable LLM profile.",
  );
  const agentProfileName = requireEnvironment(
    "TEST_PRELAUNCH_AGENT_PROFILE",
    "Provide an existing OpenHands agent profile.",
  );
  const agentProfileLlm = requireEnvironment(
    "TEST_PRELAUNCH_AGENT_PROFILE_LLM",
    "Provide the different LLM profile referenced by TEST_PRELAUNCH_AGENT_PROFILE.",
  );
  const launchPrompt = requireEnvironment(
    "TEST_PRELAUNCH_PROMPT",
    "Provide a harmless prompt used to create the verification conversation.",
  );
  let conversationId: string | undefined;

  const llmProfilesResponse = page.waitForResponse((response) => {
    const { pathname } = new URL(response.url());
    return (
      response.request().method() === "GET" &&
      pathname.endsWith("/profiles") &&
      !pathname.includes("agent-profiles")
    );
  });
  const agentProfilesResponse = page.waitForResponse((response) => {
    const { pathname } = new URL(response.url());
    return (
      response.request().method() === "GET" &&
      pathname.endsWith("/api/agent-profiles")
    );
  });

  await page.goto("/");
  await expect(page.getByTestId("home-chat-launcher")).toBeVisible();

  const llmProfiles = await requireJson<LlmProfilesResponse>(
    await llmProfilesResponse,
    "LLM profiles",
  );
  const agentProfiles = await requireJson<AgentProfilesResponse>(
    await agentProfilesResponse,
    "agent profiles",
  );
  const originalLlmProfile = llmProfiles.active_profile;
  const originalAgentProfile = agentProfiles.profiles.find(
    (profile) => profile.id === agentProfiles.active_agent_profile_id,
  );
  const targetAgentProfile = agentProfiles.profiles.find(
    (profile) => profile.name === agentProfileName,
  );

  if (!originalLlmProfile) {
    throw new Error(
      "The fixture needs an active LLM profile so the test can restore it.",
    );
  }
  if (!originalAgentProfile) {
    throw new Error(
      "The fixture needs an active agent profile so the test can restore it.",
    );
  }
  if (!llmProfiles.profiles.some(({ name }) => name === selectedLlmProfile)) {
    throw new Error(
      `TEST_PRELAUNCH_LLM_PROFILE '${selectedLlmProfile}' was not returned by the deployment.`,
    );
  }
  if (!targetAgentProfile?.id) {
    throw new Error(
      `TEST_PRELAUNCH_AGENT_PROFILE '${agentProfileName}' was not returned with a stable id.`,
    );
  }
  if (targetAgentProfile.agent_kind !== "openhands") {
    throw new Error(
      "TEST_PRELAUNCH_AGENT_PROFILE must be an OpenHands profile.",
    );
  }
  if (targetAgentProfile.llm_profile_ref !== agentProfileLlm) {
    throw new Error(
      `TEST_PRELAUNCH_AGENT_PROFILE must reference '${agentProfileLlm}'.`,
    );
  }
  if (agentProfileLlm === selectedLlmProfile) {
    throw new Error(
      "TEST_PRELAUNCH_AGENT_PROFILE_LLM must differ from TEST_PRELAUNCH_LLM_PROFILE.",
    );
  }
  if (originalLlmProfile === selectedLlmProfile) {
    throw new Error(
      "TEST_PRELAUNCH_LLM_PROFILE must differ from the fixture's active profile so selection is exercised.",
    );
  }

  try {
    await selectAgentProfile(page, agentProfileName);
    await selectLlmProfile(page, selectedLlmProfile);
    await page.getByTestId("chat-input").fill(launchPrompt);
    await page.getByTestId("submit-button").click();
    await page.waitForURL(/\/conversations\/(?!task-)[^/?#]+/, {
      timeout: 180_000,
    });
    conversationId = new URL(page.url()).pathname.split("/").pop();
    expect(conversationId).toBeTruthy();

    const encodedConversationId = encodeURIComponent(conversationId || "");
    await expect
      .poll(
        async () => {
          const response = await page.request.get(
            `/api/v1/app-conversations?ids=${encodedConversationId}`,
          );
          if (!response.ok()) return null;
          const conversations =
            (await response.json()) as ConversationSummary[];
          return conversations[0]?.active_profile ?? null;
        },
        { timeout: 60_000 },
      )
      .toBe(selectedLlmProfile);
    await expect
      .poll(
        async () => {
          const response = await page.request.get(
            `/api/v1/app-conversations?ids=${encodedConversationId}`,
          );
          if (!response.ok()) return null;
          const conversations =
            (await response.json()) as ConversationSummary[];
          return (
            conversations[0]?.launched_agent_profile?.agent_profile_id ?? null
          );
        },
        { timeout: 60_000 },
      )
      .toBe(targetAgentProfile.id);
    await expect(page.getByTestId("chat-input-llm-profile")).toContainText(
      selectedLlmProfile,
    );
  } finally {
    if (conversationId) {
      const response = await page.request.delete(
        `/api/v1/app-conversations/${encodeURIComponent(conversationId)}`,
      );
      expect
        .soft(response.ok(), "conversation cleanup should succeed")
        .toBe(true);
    }
    if (originalAgentProfile && originalLlmProfile) {
      await page.goto("/");
      await expect(page.getByTestId("home-chat-launcher")).toBeVisible();
      if (originalAgentProfile.name !== agentProfileName) {
        await selectAgentProfile(page, originalAgentProfile.name);
      }
      await selectLlmProfile(page, originalLlmProfile);
    }
  }
});
