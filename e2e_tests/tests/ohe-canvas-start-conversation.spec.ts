import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import path from "node:path";

test.use({
  storageState: path.resolve(import.meta.dirname, "../fixtures/auth.json"),
});

test("authenticated OHE member can start a Canvas conversation", async ({
  page,
}) => {
  test.setTimeout(180_000);

  if (!process.env.BASE_URL) {
    throw new Error(
      "BASE_URL is required to target the authenticated OHE deployment",
    );
  }

  const prompt = `OHE Canvas conversation e2e ${randomUUID()}`;
  let createdConversationId: string | undefined;

  try {
    await page.goto("/canvas");
    await expect(page.getByTestId("home-chat-launcher")).toBeVisible();

    await page.getByTestId("chat-input").fill(prompt);
    const createResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/api/v1/app-conversations"),
    );
    await page.getByTestId("submit-button").click();

    const createResponse = await createResponsePromise;
    expect(createResponse.ok()).toBeTruthy();
    const created = (await createResponse.json()) as {
      id: string;
      app_conversation_id: string | null;
    };

    createdConversationId = created.app_conversation_id ?? undefined;
    if (!createdConversationId) {
      await expect(page).toHaveURL(
        /\/canvas\/conversations\/(?!task-)[^/?#]+/,
        { timeout: 120_000 },
      );
      createdConversationId = new URL(page.url()).pathname.split("/").at(-1);
    }
    expect(createdConversationId).toBeTruthy();
  } finally {
    if (createdConversationId) {
      const cleanupResponse = await page.request.delete(
        `/api/v1/app-conversations/${createdConversationId}`,
      );
      expect(cleanupResponse.ok(), "created conversation cleanup").toBeTruthy();
    }
  }
});
