import { createHash } from "node:crypto";

import { expect, test, type APIRequestContext } from "@playwright/test";

import { ConversationPage, HomePage } from "../pages";
import { Cleanup } from "../utils/cleanup";
import { runUser } from "../utils/config";

/**
 * A conversation started on a repository works in a clone of that repository.
 *
 * The test opens a public repository from the home screen's Open Repository
 * dialog, starts a conversation, and asks the agent to hash the repository's
 * LICENSE in the sandbox. The expected SHA-256 comes from the default branch
 * on GitHub and is not in the prompt. The conversation record must name the
 * repository, its default branch and the GitHub provider.
 *
 * It runs as the Returning User only: the New User has no GitHub account.
 * `afterEach` deletes the conversation.
 */

interface ConversationRecord {
  id: string;
  selected_repository: string | null;
  selected_branch: string | null;
  git_provider: string | null;
}

const REPOSITORY = "OpenHands/OpenHands-Cloud";
const DEFAULT_BRANCH = "main";
const CONVERSATIONS_SEARCH_PATH = "/api/v1/app-conversations/search";

async function conversationsSince(
  request: APIRequestContext,
  since: Date,
): Promise<ConversationRecord[]> {
  const response = await request.get(CONVERSATIONS_SEARCH_PATH, {
    params: { created_at__gte: since.toISOString(), limit: "100" },
  });
  if (!response.ok()) {
    throw new Error(
      `search conversations failed (${response.status()}): ${await response.text()}`,
    );
  }
  return ((await response.json()) as { items: ConversationRecord[] }).items;
}

test.describe("repository conversations", () => {
  const cleanup = new Cleanup();

  test.afterEach(async () => {
    await cleanup.run();
  });

  test("the agent reads a file from the repository the conversation was started on", async ({
    page,
  }, testInfo) => {
    test.skip(runUser(testInfo) !== "returning", "returning role only");
    testInfo.annotations.push({ type: "user", description: runUser(testInfo) });
    // Sandbox cold start plus the clone and one bash call outlast the 120s cap.
    test.setTimeout(300_000);

    const license = await fetch(
      `https://raw.githubusercontent.com/${REPOSITORY}/${DEFAULT_BRANCH}/LICENSE`,
    );
    expect(license.ok, `fetch ${REPOSITORY} LICENSE from GitHub`).toBe(true);
    const expectedHash = createHash("sha256")
      .update(Buffer.from(await license.arrayBuffer()))
      .digest("hex");

    const { request } = page;
    // Allow for clock skew between the runner and the app server.
    const since = new Date(Date.now() - 60_000);
    const existing = new Set(
      (await conversationsSince(request, since)).map(({ id }) => id),
    );
    const created = async () =>
      (await conversationsSince(request, since)).filter(
        ({ id }) => !existing.has(id),
      );
    cleanup.defer("delete the repository conversation", async () => {
      for (const { id } of await created()) {
        const deleted = await request.delete(
          `/api/v1/app-conversations/${encodeURIComponent(id)}`,
        );
        if (!deleted.ok() && deleted.status() !== 404) {
          throw new Error(
            `delete conversation ${id} failed (${deleted.status()}): ${await deleted.text()}`,
          );
        }
      }
    });

    await test.step("Start a conversation on the repository", async () => {
      const homePage = new HomePage(page);
      await homePage.goto();
      await homePage.selectRepository(`https://github.com/${REPOSITORY}`);
      await homePage.startNewConversation(
        `The ${REPOSITORY} repository is already cloned in your workspace. ` +
          `Do not clone or download anything. In the root of that clone, ` +
          `run this exact bash command and quote the SHA-256 hex digest it ` +
          `prints (the 64-character hex string before the file name):\n\n` +
          `    sha256sum LICENSE\n\n` +
          `Report only the hex digest, verbatim, in your reply.`,
      );
    });

    await test.step("The agent hashes LICENSE in the clone", async () => {
      const conversationPage = new ConversationPage(page);
      await conversationPage.waitForConversationReady();
      const message = await conversationPage.waitForMessageContaining(
        expectedHash,
        180_000,
      );
      expect(message).toContain(expectedHash);
    });

    await test.step("The conversation record names the repository", async () => {
      const conversations = await created();
      expect(conversations).toHaveLength(1);
      expect(conversations[0]).toMatchObject({
        selected_repository: REPOSITORY,
        selected_branch: DEFAULT_BRANCH,
        git_provider: "github",
      });
    });
  });
});
