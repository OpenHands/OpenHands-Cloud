import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import path from "node:path";

test.use({
  storageState: path.resolve(import.meta.dirname, "../fixtures/auth.json"),
});

test("authenticated OHE member can start a Canvas conversation with a plugin", async ({
  page,
}) => {
  test.setTimeout(180_000);

  if (!process.env.BASE_URL) {
    throw new Error(
      "BASE_URL is required to target the authenticated OHE deployment",
    );
  }

  const pluginName = process.env.OHE_E2E_PLUGIN_NAME;
  if (!pluginName) {
    throw new Error(
      "OHE_E2E_PLUGIN_NAME is required and must name a plugin available to the authenticated OHE member",
    );
  }

  const prompt = `OHE plugin conversation e2e ${randomUUID()}`;
  let createdConversationId: string | undefined;

  try {
    await page.goto("/canvas");
    await page.getByTestId("open-plugin-picker").click();
    await expect(page.getByTestId("plugin-picker-modal")).toBeVisible();

    await page.getByTestId("plugin-picker-search-input").fill(pluginName);
    const pluginCard = page.getByTestId(`plugin-picker-card-${pluginName}`);
    await expect(
      pluginCard,
      `configured plugin ${pluginName} should be available`,
    ).toBeVisible();

    const pluginToggle = page.getByTestId(`plugin-picker-toggle-${pluginName}`);
    await pluginToggle.click();
    await expect(pluginToggle).toHaveAttribute("aria-checked", "true");
    await page.getByTestId("plugin-picker-done").click();
    await expect(page.getByTestId("plugin-picker-count")).toHaveText("1");

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
