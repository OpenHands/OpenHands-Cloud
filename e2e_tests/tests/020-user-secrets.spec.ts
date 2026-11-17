import { createHash, randomBytes } from "node:crypto";

import { expect, test, type APIRequestContext } from "@playwright/test";

import { ConversationPage, HomePage } from "../pages";
import { Cleanup } from "../utils/cleanup";
import { runUser } from "../utils/config";

/**
 * Personal secrets reach the agent's sandbox.
 *
 * The test saves a personal secret through the API the Settings > Secrets page
 * uses, starts a conversation, and asks the agent to hash `$<secret name>` in
 * the sandbox. The agent server masks secret values in tool output, so the
 * test asserts the SHA-256 of the value instead: the hash is not in the prompt,
 * and a sandbox without the secret hashes an empty string instead.
 *
 * It runs as the New User only, so the shared Returning User's secrets are
 * never written. `afterEach` deletes the conversation and the secret.
 */

const SECRETS_PATH = "/api/v1/secrets";
const CONVERSATIONS_SEARCH_PATH = "/api/v1/app-conversations/search";

async function failure(
  label: string,
  response: Awaited<ReturnType<APIRequestContext["get"]>>,
): Promise<Error> {
  return new Error(
    `${label} failed (${response.status()} ${response.statusText()}): ${await response.text()}`,
  );
}

async function conversationIdsSince(
  request: APIRequestContext,
  since: Date,
): Promise<string[]> {
  const response = await request.get(CONVERSATIONS_SEARCH_PATH, {
    params: { created_at__gte: since.toISOString(), limit: "100" },
  });
  if (!response.ok()) throw await failure("search conversations", response);
  const { items } = (await response.json()) as { items: Array<{ id: string }> };
  return items.map(({ id }) => id);
}

test.describe("personal secrets", () => {
  const cleanup = new Cleanup();

  test.afterEach(async () => {
    await cleanup.run();
  });

  test("a saved secret is available to the agent as an environment variable", async ({
    page,
  }, testInfo) => {
    test.skip(runUser(testInfo) !== "new-user", "new-user role only");
    testInfo.annotations.push({ type: "user", description: runUser(testInfo) });
    // Sandbox cold start plus one bash call outlast the 120s default cap.
    test.setTimeout(240_000);

    const name = `E2E_SECRET_${randomBytes(6).toString("hex").toUpperCase()}`;
    const value = `oh-e2e-${randomBytes(16).toString("hex")}`;
    const expectedHash = createHash("sha256").update(value).digest("hex");
    const { request } = page;

    await test.step("Save a personal secret", async () => {
      const response = await request.post(SECRETS_PATH, {
        data: { name, value, description: "E2E test secret" },
      });
      if (!response.ok()) throw await failure(`save secret ${name}`, response);
      cleanup.defer(`delete secret ${name}`, async () => {
        const deleted = await request.delete(
          `${SECRETS_PATH}/${encodeURIComponent(name)}`,
        );
        if (!deleted.ok() && deleted.status() !== 404) {
          throw await failure(`delete secret ${name}`, deleted);
        }
      });
    });

    await test.step("The agent hashes the secret in the sandbox", async () => {
      // Allow for clock skew between the runner and the app server.
      const since = new Date(Date.now() - 60_000);
      const existing = new Set(await conversationIdsSince(request, since));
      cleanup.defer("delete the secret test's conversation", async () => {
        const created = (await conversationIdsSince(request, since)).filter(
          (id) => !existing.has(id),
        );
        for (const id of created) {
          const deleted = await request.delete(
            `/api/v1/app-conversations/${encodeURIComponent(id)}`,
          );
          if (!deleted.ok() && deleted.status() !== 404) {
            throw await failure(`delete conversation ${id}`, deleted);
          }
        }
      });

      const homePage = new HomePage(page);
      await homePage.goto();
      await homePage.startNewConversation(
        `In the sandbox, please run this exact bash command and quote the ` +
          `SHA-256 hex digest it prints (the 64-character hex string before ` +
          `the trailing dash):\n\n` +
          `    printf '%s' "$${name}" | sha256sum\n\n` +
          `Report only the hex digest, verbatim, in your reply.`,
      );

      const conversationPage = new ConversationPage(page);
      await conversationPage.waitForConversationReady();
      const message = await conversationPage.waitForMessageContaining(
        expectedHash,
        180_000,
      );
      expect(message).toContain(expectedHash);
    });
  });
});
