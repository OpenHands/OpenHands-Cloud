import { expect, test } from "@playwright/test";
import { runUser } from "../utils/config";

test.beforeEach(({ page: _page }, testInfo) => {
  test.skip(
    runUser(testInfo) !== "returning",
    "Requires the stable returning-user fixture.",
  );
});

interface AgentProfileSummary {
  id: string;
  name: string;
  agent_kind: string;
}

interface AgentProfileList {
  active_agent_profile_id: string | null;
  profiles: AgentProfileSummary[];
}

test("authenticated OHE member can switch Canvas agent profile", async ({
  page,
}) => {
  if (!process.env.BASE_URL) {
    throw new Error(
      "BASE_URL is required to target the authenticated OHE deployment",
    );
  }

  const targetName = process.env.OHE_E2E_AGENT_PROFILE_NAME;
  if (!targetName) {
    throw new Error(
      "OHE_E2E_AGENT_PROFILE_NAME is required and must name a configured OpenHands or ACP profile different from the active profile",
    );
  }

  await page.goto("/canvas");
  const profileResponse = await page.request.get("/api/agent-profiles");
  expect(profileResponse.ok(), "load configured agent profiles").toBeTruthy();
  const profileList = (await profileResponse.json()) as AgentProfileList;
  const originalProfileId = profileList.active_agent_profile_id;
  const targetProfile = profileList.profiles.find(
    (profile) => profile.name === targetName,
  );

  if (!originalProfileId) {
    throw new Error(
      "The OHE member must have an active agent profile before this test runs",
    );
  }
  if (!targetProfile) {
    throw new Error(
      `OHE_E2E_AGENT_PROFILE_NAME=${targetName} was not found; configured profiles: ${profileList.profiles.map((profile) => `${profile.name} (${profile.agent_kind})`).join(", ")}`,
    );
  }
  if (!(["openhands", "acp"] as string[]).includes(targetProfile.agent_kind)) {
    throw new Error(
      `Configured profile ${targetName} has unsupported agent kind ${targetProfile.agent_kind}; expected openhands or acp`,
    );
  }
  if (targetProfile.id === originalProfileId) {
    throw new Error(
      `OHE_E2E_AGENT_PROFILE_NAME=${targetName} is already active; configure a different OpenHands or ACP profile`,
    );
  }

  let switched = false;
  try {
    await page.getByTestId("chat-plus-button").click();
    await page.getByTestId("switch-agent-profile-button").click();
    await expect(page.getByTestId("agent-profile-submenu")).toBeVisible();

    const activateResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith(
          `/api/agent-profiles/${encodeURIComponent(targetProfile.id)}/activate`,
        ),
    );
    await page
      .getByTestId(`chat-input-agent-profile-option-${targetName}`)
      .click();

    const activateResponse = await activateResponsePromise;
    expect(activateResponse.ok(), "activate target agent profile").toBeTruthy();
    switched = true;

    const updatedResponse = await page.request.get("/api/agent-profiles");
    expect(
      updatedResponse.ok(),
      "reload configured agent profiles",
    ).toBeTruthy();
    const updatedProfiles = (await updatedResponse.json()) as AgentProfileList;
    expect(updatedProfiles.active_agent_profile_id).toBe(targetProfile.id);
  } finally {
    if (switched) {
      const restoreResponse = await page.request.post(
        `/api/agent-profiles/${originalProfileId}/activate`,
        { data: {} },
      );
      expect(
        restoreResponse.ok(),
        "restore original agent profile",
      ).toBeTruthy();
    }
  }
});
