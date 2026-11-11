import { randomUUID } from "node:crypto";

import { expect, test, type Page, type TestInfo } from "@playwright/test";

import { ConversationPage, HomePage } from "../pages";
import {
  activateAgentProfile,
  deactivateAgentProfile,
  deleteConversationsLaunchedBy,
  deletePersonalAgentProfile,
  getActiveLlmProfileName,
  getLaunchedAgentProfile,
  getPersonalAgentProfile,
  listAgentProfiles,
  listConversationsLaunchedBy,
  listAgentReplies,
} from "../utils/agent-profiles";
import { fetchWebClientConfig } from "../utils/billing";
import { Cleanup } from "../utils/cleanup";
import { runUser } from "../utils/config";
import { e2eTimestamp } from "../utils/org-management";

/**
 * Agent Profile journey: a profile created in settings and chosen as the
 * default shapes the next conversation.
 *
 * The actor creates a personal profile on the Agent profiles settings page. Its
 * one distinctive runtime instruction ("Default + your instructions") tells the
 * agent to end every message with a run-scoped marker. The actor sets the
 * profile as the default, starts a conversation from the home composer, and
 * the test passes only when
 *
 *  - the stored profile holds the instruction and the member's default points
 *    at it,
 *  - the conversation record names the profile and its revision as the one
 *    that launched it,
 *  - the agent's own replies carry the marker, though the user's prompt never
 *    mentions it, and
 *  - after reloads the settings page still lists the profile as the default
 *    with the instruction intact, and the conversation still shows the marker.
 *
 * The UI performs the configuration; the API only checks stored and effective
 * state. The journey needs an LLM profile on the actor, like
 * `003-conversations-legacy`, and skips when the instance's **Agent profiles
 * page** option (`feature_flags.enable_agent_profiles_page`) is off.
 * `afterEach` puts the member's previous default back, then deletes the
 * conversations and the profile, newest first.
 */

const SYSTEM_PROMPT_MODE_LABEL = "Default + your instructions";
const AGENT_REPLY_TIMEOUT_MS = 180_000;

async function attachScreenshot(
  page: Page,
  testInfo: TestInfo,
  name: string,
): Promise<void> {
  await testInfo.attach(name, {
    body: await page.screenshot(),
    contentType: "image/png",
  });
}

async function openAgentProfilesSettings(page: Page): Promise<void> {
  await page.goto("/settings/agents");
  await expect(page.getByTestId("add-agent-profile")).toBeEnabled({
    timeout: 30_000,
  });
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function profileRow(page: Page, name: string) {
  return page.getByTestId("agent-profile-row").filter({ hasText: name });
}

test.describe("agent profile journey: create, select, launch", () => {
  const cleanup = new Cleanup();

  test.afterEach(async () => {
    await cleanup.run();
  });

  test("a profile with one instruction is created, made the default and shapes a new conversation", async ({
    page,
  }, testInfo) => {
    test.setTimeout(AGENT_REPLY_TIMEOUT_MS + 180_000);

    const suffix = `qa-${e2eTimestamp()}`;
    const profileName = `qa-agent-profile-${suffix}`;
    const marker = `QA-MARKER-${randomUUID().slice(0, 8).toUpperCase()}`;
    const instruction = `Always end every message you send, including your final message, with the exact tag ${marker}.`;
    const prompt = "In one short sentence, name the capital city of France.";
    const { request } = page;

    testInfo.annotations.push({ type: "user", description: runUser(testInfo) });

    const config = await fetchWebClientConfig(request);
    test.skip(
      !config.feature_flags?.enable_agent_profiles_page,
      "the Agent profiles page is disabled (feature_flags.enable_agent_profiles_page is falsy)",
    );

    const previousDefaultId =
      await test.step("Record the member's current default profile", async () =>
        (await listAgentProfiles(request)).active_agent_profile_id);

    const llmProfileName = await getActiveLlmProfileName(request);
    expect(llmProfileName, "the actor has an active LLM profile").toBeTruthy();

    await test.step("Create the profile with one runtime instruction in settings", async () => {
      await openAgentProfilesSettings(page);
      cleanup.defer(`delete agent profile ${profileName}`, () =>
        deletePersonalAgentProfile(request, profileName),
      );

      await page.getByTestId("add-agent-profile").click();
      await expect(page.getByTestId("agent-profile-editor")).toBeVisible();
      await page.getByTestId("agent-profile-name-input").fill(profileName);
      const llmSelector = page.getByTestId("agent-profile-llm-selector");
      await expect(
        llmSelector,
        "the actor needs an LLM profile to build an Agent Profile on",
      ).toBeVisible();
      await llmSelector.click();
      await llmSelector.press("ControlOrMeta+A");
      await llmSelector.pressSequentially(llmProfileName as string);
      await page
        .getByRole("option", {
          name: new RegExp(`^${escapeRegExp(llmProfileName as string)}`),
        })
        .click();

      const promptMode = page.getByTestId("agent-profile-system-prompt-mode");
      await promptMode.click();
      await promptMode.press("ControlOrMeta+A");
      await promptMode.pressSequentially("Default +");
      await page
        .getByRole("option", { name: SYSTEM_PROMPT_MODE_LABEL })
        .click();
      await page.getByTestId("agent-profile-system-prompt").fill(instruction);
      await attachScreenshot(page, testInfo, "01-profile-editor-filled");

      await page.getByTestId("save-agent-profile-btn").click();
      await expect(profileRow(page, profileName)).toBeVisible();
    });

    const profile =
      await test.step("The saved profile holds the instruction", async () => {
        const stored = await getPersonalAgentProfile(request, profileName);
        expect(stored, `profile ${profileName} is stored`).not.toBeNull();
        expect(stored?.agent_kind).toBe("openhands");
        expect(stored?.system_message_suffix).toBe(instruction);
        expect(stored?.persona ?? null).toBeNull();
        return stored as NonNullable<typeof stored>;
      });

    await test.step("Select the profile as the member's default", async () => {
      cleanup.defer(`delete conversations launched by ${profileName}`, () =>
        deleteConversationsLaunchedBy(request, profile.id),
      );
      cleanup.defer("restore the member's previous default profile", () =>
        previousDefaultId
          ? activateAgentProfile(request, previousDefaultId)
          : deactivateAgentProfile(request, profile.id),
      );

      await profileRow(page, profileName)
        .getByTestId("agent-profile-menu-trigger")
        .click();
      await page.getByTestId("profile-set-active").click();
      await expect(
        profileRow(page, profileName).getByTestId("agent-profile-active-badge"),
      ).toBeVisible();
      await attachScreenshot(page, testInfo, "02-profile-selected-default");

      expect((await listAgentProfiles(request)).active_agent_profile_id).toBe(
        profile.id,
      );
    });

    const conversationId =
      await test.step("Start a conversation that reveals the profile's behavior", async () => {
        const homePage = new HomePage(page);
        await homePage.goto();
        await homePage.startNewConversation(prompt);
        const reply = await new ConversationPage(page).waitForMessageContaining(
          marker,
          AGENT_REPLY_TIMEOUT_MS,
        );
        expect(reply).toContain(marker);
        await attachScreenshot(page, testInfo, "03-conversation-shows-marker");

        const launched = await listConversationsLaunchedBy(request, profile.id);
        expect(
          launched,
          "the app server recorded one conversation launched by the profile",
        ).toHaveLength(1);
        return launched[0];
      });

    await test.step("The conversation was launched by the profile and the agent obeyed it", async () => {
      await expect
        .poll(() => getLaunchedAgentProfile(request, conversationId), {
          message: "the app server records the launching profile",
        })
        .toMatchObject({
          agent_profile_id: profile.id,
          revision: profile.revision,
        });

      await expect
        .poll(
          async () =>
            (await listAgentReplies(request, conversationId)).some((reply) =>
              reply.includes(marker),
            ),
          {
            message: "an agent reply carries the profile's marker",
            timeout: 60_000,
          },
        )
        .toBe(true);
    });

    await test.step("Reload the profile settings: the profile persists as the default", async () => {
      await openAgentProfilesSettings(page);
      await page.reload();
      await expect(page.getByTestId("add-agent-profile")).toBeEnabled({
        timeout: 30_000,
      });
      const row = profileRow(page, profileName);
      await expect(row).toBeVisible();
      await expect(row.getByTestId("agent-profile-active-badge")).toBeVisible();

      await row.getByTestId("agent-profile-menu-trigger").click();
      await page.getByTestId("profile-edit").click();
      await expect(page.getByTestId("agent-profile-editor")).toBeVisible();
      await expect(page.getByTestId("agent-profile-name-input")).toHaveValue(
        profileName,
      );
      await expect(
        page.getByTestId("agent-profile-system-prompt-mode"),
      ).toHaveValue(SYSTEM_PROMPT_MODE_LABEL);
      await expect(page.getByTestId("agent-profile-system-prompt")).toHaveValue(
        instruction,
      );
      await page
        .getByTestId("agent-profile-system-prompt")
        .scrollIntoViewIfNeeded();
      await attachScreenshot(page, testInfo, "04-profile-after-reload");
    });

    await test.step("Reload the conversation: it still shows the profile's behavior", async () => {
      await page.goto(`/conversations/${conversationId}`);
      await page.reload();
      expect(
        await new ConversationPage(page).waitForMessageContaining(
          marker,
          AGENT_REPLY_TIMEOUT_MS,
        ),
      ).toContain(marker);
      expect(
        await getLaunchedAgentProfile(request, conversationId),
      ).toMatchObject({
        agent_profile_id: profile.id,
        revision: profile.revision,
      });
      await attachScreenshot(page, testInfo, "05-conversation-after-reload");
    });
  });
});
