import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

import { Cleanup } from "../utils/cleanup";
import { runUser } from "../utils/config";
import { e2eTimestamp } from "../utils/org-management";

/**
 * Personal MCP servers on Settings > MCP.
 *
 * Every save on that page resends the member's whole server list, so a save
 * that drops a field silently changes servers the member did not touch. The
 * test switches one server off, then adds another, and passes only when the
 * first server is still off after a reload and in the stored settings.
 *
 * It runs as the New User only: that account is recreated every run, so the
 * shared Returning User's MCP servers are never rewritten. `afterEach` removes
 * the two servers through the same settings endpoint the page uses.
 */

type StoredMcpServers = Record<string, { enabled?: boolean; url?: string }>;

const SETTINGS_PATH = "/api/v1/settings";

async function storedMcpServers(
  request: APIRequestContext,
): Promise<StoredMcpServers> {
  const response = await request.get(SETTINGS_PATH);
  if (!response.ok()) {
    throw new Error(
      `GET ${SETTINGS_PATH} failed (${response.status()}): ${await response.text()}`,
    );
  }
  const body = (await response.json()) as {
    agent_settings?: { mcp_config?: Record<string, unknown> | null };
  };
  const config = body.agent_settings?.mcp_config ?? {};
  return ((config.mcpServers as StoredMcpServers | undefined) ??
    config) as StoredMcpServers;
}

async function removeMcpServers(
  request: APIRequestContext,
  names: string[],
): Promise<void> {
  const servers = await storedMcpServers(request);
  if (!names.some((name) => name in servers)) return;
  const remaining = Object.fromEntries(
    Object.entries(servers).filter(([name]) => !names.includes(name)),
  );
  const response = await request.post(SETTINGS_PATH, {
    data: {
      agent_settings_diff: {
        mcp_config: Object.keys(remaining).length > 0 ? remaining : null,
      },
    },
  });
  if (!response.ok()) {
    throw new Error(
      `POST ${SETTINGS_PATH} failed (${response.status()}): ${await response.text()}`,
    );
  }
}

function serverRow(page: Page, url: string) {
  return page.getByTestId("mcp-server-item").filter({ hasText: url });
}

function waitForSettingsSave(page: Page) {
  return page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === SETTINGS_PATH,
  );
}

async function addSseServer(page: Page, name: string, url: string) {
  await page.getByRole("button", { name: "Add Server", exact: true }).click();
  const modal = page.getByTestId("add-mcp-server-modal");
  await expect(modal).toBeVisible();
  await modal.getByTestId("name-input").fill(name);
  await modal.getByTestId("url-input").fill(url);
  const saved = waitForSettingsSave(page);
  await modal.getByTestId("submit-button").click();
  expect((await saved).ok(), `saving MCP server ${name}`).toBe(true);
  await expect(modal).toBeHidden();
  await expect(serverRow(page, url)).toBeVisible();
}

test.describe("personal MCP servers", () => {
  const cleanup = new Cleanup();

  test.afterEach(async () => {
    await cleanup.run();
  });

  test("a switched-off server stays off after another server is added", async ({
    page,
  }, testInfo) => {
    test.skip(runUser(testInfo) !== "new-user", "new-user role only");
    testInfo.annotations.push({ type: "user", description: runUser(testInfo) });

    const suffix = e2eTimestamp().toLowerCase();
    const offName = `e2e-mcp-off-${suffix}`;
    const onName = `e2e-mcp-on-${suffix}`;
    const offUrl = `https://${offName}.e2e.example.com/sse`;
    const onUrl = `https://${onName}.e2e.example.com/sse`;
    const { request } = page;

    cleanup.defer(`remove MCP servers ${offName} and ${onName}`, () =>
      removeMcpServers(request, [offName, onName]),
    );

    await page.goto("/settings/mcp");
    await expect(page.getByTestId("mcp-settings-screen")).toBeVisible({
      timeout: 30_000,
    });

    await test.step("Add a server and switch it off", async () => {
      await addSseServer(page, offName, offUrl);
      const toggle = serverRow(page, offUrl).getByRole("switch");
      await expect(toggle).toHaveAttribute("aria-checked", "true");
      const saved = waitForSettingsSave(page);
      await toggle.click();
      expect((await saved).ok(), "saving the switched-off server").toBe(true);
      await expect(toggle).toHaveAttribute("aria-checked", "false");
    });

    await test.step("Add a second server", async () => {
      await addSseServer(page, onName, onUrl);
    });

    await test.step("After a reload, the first server is still off", async () => {
      await page.reload();
      await expect(serverRow(page, offUrl).getByRole("switch")).toHaveAttribute(
        "aria-checked",
        "false",
      );
      await expect(serverRow(page, onUrl).getByRole("switch")).toHaveAttribute(
        "aria-checked",
        "true",
      );
    });

    await test.step("The stored settings keep the first server off", async () => {
      const servers = await storedMcpServers(request);
      expect(servers[offName], `stored server ${offName}`).toMatchObject({
        url: offUrl,
        enabled: false,
      });
      expect(servers[onName], `stored server ${onName}`).toMatchObject({
        url: onUrl,
      });
      expect(servers[onName].enabled).not.toBe(false);
    });
  });
});
