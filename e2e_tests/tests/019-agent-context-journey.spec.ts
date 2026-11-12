import { randomUUID } from "node:crypto";

import { expect, test, type Page, type TestInfo } from "@playwright/test";

import { ConversationPage, HomePage } from "../pages";
import {
  MEMORY_FILE_PATH,
  conversationIdFromUrl,
  deleteConversation,
  getMemorySettings,
  memoryEntryLine,
  setMemoryEnabled,
} from "../utils/agent-context";
import { listAgentReplies } from "../utils/agent-profiles";
import { fetchWebClientConfig } from "../utils/billing";
import { Cleanup } from "../utils/cleanup";
import { runUser } from "../utils/config";

/**
 * Agent Context journey: persistent memory switched on in settings reaches a
 * new conversation, and switched off it does not.
 *
 * The Agent Context page holds one switch, "Enable Persistent Memory". The
 * memory itself has no editor: the agent writes it by editing
 * `.openhands/memory/MEMORY.md` with its file editor, and the app server keeps
 * the latest content on the member and writes it into the next conversation's
 * sandbox. So the actor
 *
 *  - turns the switch on in the Agent Context page,
 *  - asks the agent to save one run-scoped entry (a codeword and a marker),
 *  - starts a second conversation whose prompt never mentions the marker and
 *    asks for the codeword's value,
 *  - checks the effective configuration (the switch and the stored content,
 *    and that the marker sits nowhere in the member's agent settings),
 *  - turns the switch off in the page and starts a third conversation that
 *    asks the same question.
 *
 * The test passes only when the entry is captured on the member, the second
 * conversation's own replies carry the marker, and the third conversation's
 * replies, which do show the agent answered, never do. The UI performs the
 * configuration; the API only checks stored state and what the agent said.
 *
 * It runs against the actor's own account and, like the Agent Profile journey,
 * needs a working LLM that follows instructions. It skips when the instance
 * shows no Agent Context page (neither `feature_flags.enable_agent_context_page`
 * nor `feature_flags.enable_agent_profiles_page` is on). `afterEach` deletes
 * the conversations, has the agent remove the run's entry from memory (the
 * content has no delete API) and puts the member's previous switch back.
 */

const AGENT_REPLY_TIMEOUT_MS = 180_000;
const MEMORY_WRITE_TIMEOUT_MS = 240_000;

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

async function openAgentContextSettings(page: Page): Promise<void> {
  await page.goto("/settings/agent-context");
  await expect(page.getByTestId("agent-context-settings-screen")).toBeVisible({
    timeout: 30_000,
  });
}

function memorySwitch(page: Page) {
  return page.getByTestId("agent-context-enable-memory-context");
}

/** Flip the memory switch the way a member does and save, unless it already shows `enabled`. */
async function setMemorySwitchInUi(
  page: Page,
  enabled: boolean,
): Promise<void> {
  const toggle = memorySwitch(page);
  await expect(toggle).toBeEnabled();
  if ((await toggle.isChecked()) !== enabled) {
    await page.locator("label").filter({ has: toggle }).click();
    await expect(toggle).toBeChecked({ checked: enabled });
    await page.getByTestId("agent-context-save-button").click();
  }
  await expect
    .poll(
      async () => (await getMemorySettings(page.request)).enable_memory_context,
      {
        message: `the stored memory switch is ${enabled ? "on" : "off"}`,
        timeout: 30_000,
      },
    )
    .toBe(enabled);
  await expect(toggle).toBeChecked({ checked: enabled });
}

/** Start a conversation from the home composer; returns its id. */
async function startConversation(
  page: Page,
  prompt: string,
  cleanup: Cleanup,
  label: string,
): Promise<string> {
  const homePage = new HomePage(page);
  await homePage.goto();
  await homePage.startNewConversation(prompt);
  const conversationId = conversationIdFromUrl(page.url());
  cleanup.defer(`delete the ${label} conversation`, () =>
    deleteConversation(page.request, conversationId),
  );
  return conversationId;
}

async function waitForAgentReply(
  page: Page,
  conversationId: string,
  text: string,
  message: string,
): Promise<void> {
  await expect
    .poll(
      async () =>
        (await listAgentReplies(page.request, conversationId)).some((reply) =>
          reply.includes(text),
        ),
      { message, timeout: AGENT_REPLY_TIMEOUT_MS },
    )
    .toBe(true);
}

/** Have the agent take `entryLine` out of memory, then delete that conversation. */
async function removeMemoryEntry(page: Page, entryLine: string): Promise<void> {
  const { request } = page;
  const stored = await getMemorySettings(request);
  if (!stored.memory_context?.includes(entryLine)) return;

  await setMemoryEnabled(request, true);
  const homePage = new HomePage(page);
  await homePage.goto();
  await homePage.startNewConversation(
    `Using your file editor tool, remove the line "${entryLine}" from the file ${MEMORY_FILE_PATH} in your working directory. Keep every other line unchanged and add nothing else.`,
  );
  const conversationId = conversationIdFromUrl(page.url());
  try {
    await expect
      .poll(
        async () =>
          (await getMemorySettings(request)).memory_context?.includes(
            entryLine,
          ) ?? false,
        {
          message: "the agent removed the run's entry from the stored memory",
          timeout: MEMORY_WRITE_TIMEOUT_MS,
        },
      )
      .toBe(false);
  } finally {
    await deleteConversation(request, conversationId);
  }
}

test.describe("agent context journey: create, apply, withhold", () => {
  const cleanup = new Cleanup();

  test.afterEach(async () => {
    await cleanup.run();
  });

  test("a memory entry saved by the agent reaches a new conversation only while persistent memory is on", async ({
    page,
  }, testInfo) => {
    test.setTimeout(
      3 * AGENT_REPLY_TIMEOUT_MS + MEMORY_WRITE_TIMEOUT_MS + 360_000,
    );

    const runId = randomUUID().slice(0, 8);
    const key = `qa-codeword-${runId}`;
    const marker = `QA-CTX-${runId.toUpperCase()}`;
    const noEntryToken = `QA-NO-ENTRY-${runId.toUpperCase()}`;
    const entryLine = memoryEntryLine(key, marker);
    const saveEntryPrompt = `Save one entry to your persistent memory. Using your file editor tool, add the single line "${entryLine}" to the file ${MEMORY_FILE_PATH} in your working directory. Create the file if it does not exist, otherwise keep every existing line. Add nothing else.`;
    const askEntryPrompt = `Look in your persistent memory for the entry "${key}". Reply with its value only. If your memory has no such entry, reply with exactly ${noEntryToken}.`;
    const { request } = page;

    testInfo.annotations.push({ type: "user", description: runUser(testInfo) });

    const config = await fetchWebClientConfig(request);
    test.skip(
      !config.feature_flags?.enable_agent_context_page &&
        !config.feature_flags?.enable_agent_profiles_page,
      "the Agent Context page is disabled (neither feature_flags.enable_agent_context_page nor feature_flags.enable_agent_profiles_page is on)",
    );

    const baseline =
      await test.step("Record the member's current memory switch", async () =>
        getMemorySettings(request));

    await test.step("Turn persistent memory on in the Agent Context page", async () => {
      cleanup.defer("restore the member's previous memory switch", () =>
        setMemoryEnabled(request, baseline.enable_memory_context),
      );
      await openAgentContextSettings(page);
      await setMemorySwitchInUi(page, true);
      await attachScreenshot(page, testInfo, "01-memory-switched-on");
    });

    await test.step("Create the context entry: the agent saves it to memory", async () => {
      cleanup.defer(`remove the memory entry ${key}`, () =>
        removeMemoryEntry(page, entryLine),
      );
      await startConversation(page, saveEntryPrompt, cleanup, "entry-saving");
      await expect
        .poll(
          async () =>
            (await getMemorySettings(request)).memory_context?.includes(
              entryLine,
            ) ?? false,
          {
            message:
              "the app server captured the entry the agent wrote to MEMORY.md (memory_context)",
            timeout: MEMORY_WRITE_TIMEOUT_MS,
          },
        )
        .toBe(true);
      await attachScreenshot(page, testInfo, "02-entry-saved-by-agent");
    });

    await test.step("Apply it: a new conversation answers from the saved entry", async () => {
      const conversationId = await startConversation(
        page,
        askEntryPrompt,
        cleanup,
        "memory-reading",
      );
      expect(
        await new ConversationPage(page).waitForMessageContaining(
          marker,
          AGENT_REPLY_TIMEOUT_MS,
        ),
      ).toContain(marker);
      await waitForAgentReply(
        page,
        conversationId,
        marker,
        "an agent reply carries the saved marker, though the prompt never names it",
      );
      await attachScreenshot(page, testInfo, "03-conversation-uses-entry");
    });

    await test.step("Inspect the effective configuration", async () => {
      const stored = await getMemorySettings(request);
      expect(stored.enable_memory_context, "the memory switch is on").toBe(
        true,
      );
      expect(
        stored.memory_context,
        "the stored memory holds the entry",
      ).toContain(entryLine);
      expect(
        JSON.stringify(stored.agent_settings),
        "the marker has not leaked into the member's agent settings",
      ).not.toContain(marker);

      await openAgentContextSettings(page);
      await page.reload();
      await expect(
        page.getByTestId("agent-context-settings-screen"),
      ).toBeVisible({ timeout: 30_000 });
      await expect(
        memorySwitch(page),
        "the Agent Context page still shows persistent memory on after a reload",
      ).toBeChecked();
      await attachScreenshot(page, testInfo, "04-memory-on-after-reload");
    });

    await test.step("Turn persistent memory off: a new conversation does not receive the entry", async () => {
      await setMemorySwitchInUi(page, false);
      await attachScreenshot(page, testInfo, "05-memory-switched-off");

      const conversationId = await startConversation(
        page,
        askEntryPrompt,
        cleanup,
        "memory-off",
      );
      await waitForAgentReply(
        page,
        conversationId,
        noEntryToken,
        "the agent answered that its memory has no such entry",
      );
      const replies = await listAgentReplies(request, conversationId);
      expect(
        replies.filter((reply) => reply.includes(marker)),
        "no agent reply in the memory-off conversation carries the marker",
      ).toEqual([]);
      await attachScreenshot(page, testInfo, "06-conversation-without-entry");
    });
  });
});
