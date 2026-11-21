import { expect, test } from "@playwright/test";
import { createServer, type Server, type ServerResponse } from "http";
import type { AddressInfo } from "net";

import { Cleanup } from "../utils/cleanup";
import { e2eRunId } from "../utils/config";
import {
  deleteOrphanedAccounts,
  e2eOrgName,
  e2eTimestamp,
  parseE2eOrgName,
  sweepE2eOrgs,
} from "../utils/org-management";

test("cleanup runs newest-first and keeps going past a failure", async () => {
  const cleanup = new Cleanup();
  const order: string[] = [];
  cleanup.defer("org", async () => {
    order.push("org");
  });
  cleanup.defer("broken", async () => {
    throw new Error("boom");
  });
  cleanup.defer("automation", async () => {
    order.push("automation");
  });

  const failures = await cleanup.run();

  expect(order).toEqual(["automation", "org"]);
  expect(failures).toEqual(["Cleanup: broken failed: Error: boom"]);
  expect(await cleanup.run()).toEqual([]);
});

test("an e2e org name carries its creation time and run", () => {
  const now = new Date("2026-11-04T20:03:58.435Z");
  const name = e2eOrgName("automations member", e2eTimestamp(now), "a1b2c3d4");

  expect(name).toBe(
    "e2e automations member 2026-11-04T20-03-58-435Z run-a1b2c3d4",
  );
  expect(parseE2eOrgName(name)).toEqual({ createdAt: now, runId: "a1b2c3d4" });
});

test("org names default to this run's ID", () => {
  expect(parseE2eOrgName(e2eOrgName("test org"))?.runId).toBe(e2eRunId());
});

test("names from before the run suffix still parse, with no run", () => {
  expect(parseE2eOrgName("e2e test org 2026-11-04T20-03-58-435Z")).toEqual({
    createdAt: new Date("2026-11-04T20:03:58.435Z"),
    runId: null,
  });
});

for (const name of [
  "Personal Workspace",
  "e2e test org",
  "managed-key-e2e-1793822400000",
  "sa-e2e-a-1793822400000",
  "acme e2e test org 2026-11-04T20-03-58-435Z",
  "e2e test org 2026-13-04T20-03-58-435Z",
  "e2e test org 2026-11-04T20-03-58-435Z run-",
  "e2e test org 2026-11-04T20-03-58-435Z run-Bad_ID",
]) {
  test(`"${name}" is not an e2e org name`, () => {
    expect(parseE2eOrgName(name)).toBeNull();
  });
}

/**
 * Answer a Super Admin list request the way the server does: ``page_id`` is
 * an offset. Pages hold two items so the client has to page.
 */
function servePage(res: ServerResponse, url: URL, all: unknown[]): void {
  const offset = Number(url.searchParams.get("page_id") ?? 0);
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(
    JSON.stringify({ items: all.slice(offset, offset + 2), total: all.length }),
  );
}

test.describe("sweepE2eOrgs against a stand-in admin API", () => {
  const orgs = [
    { id: "legacy", name: "e2e test org 2026-11-01T00-00-00-000Z" },
    { id: "this-run", name: "e2e test org 2026-11-04T20-00-00-000Z run-abc" },
    { id: "other-run", name: "e2e test org 2026-11-04T20-00-00-000Z run-xyz" },
    { id: "broken", name: "e2e test org 2026-11-01T00-00-00-000Z run-xyz" },
    { id: "gone", name: "e2e test org 2026-11-01T00-00-00-001Z run-xyz" },
    { id: "real", name: "Acme Corp" },
  ];
  let server: Server;
  let baseUrl = "";
  let deleted: string[] = [];
  let auth: Array<string | undefined> = [];
  let listQueries: string[] = [];

  test.beforeAll(async () => {
    server = createServer((req, res) => {
      auth.push(req.headers.authorization);
      const url = new URL(req.url ?? "", "http://stand-in");
      const id = /^\/api\/admin\/organizations\/(.+)$/.exec(url.pathname)?.[1];
      if (req.method === "GET" && url.pathname === "/api/admin/organizations") {
        listQueries.push(url.search);
        servePage(res, url, orgs);
      } else if (req.method === "DELETE" && id === "broken") {
        res.writeHead(500).end("boom");
      } else if (req.method === "DELETE" && id === "gone") {
        res.writeHead(404).end();
      } else if (req.method === "DELETE" && id) {
        deleted.push(id);
        res.writeHead(200).end("{}");
      } else {
        res.writeHead(404).end();
      }
    });
    await new Promise<void>((resolve) => server.listen(0, resolve));
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/`;
  });

  test.afterAll(async () => {
    await new Promise((resolve) => server.close(resolve));
  });

  test.beforeEach(() => {
    deleted = [];
    auth = [];
    listQueries = [];
  });

  test("deletes only the selected e2e orgs and survives failed deletes", async () => {
    const cutoff = new Date("2026-11-04T00:00:00.000Z").getTime();
    await sweepE2eOrgs(
      baseUrl,
      "key",
      (org) => org.runId !== "abc" && org.createdAt.getTime() < cutoff,
      "test",
    );

    expect(deleted).toEqual(["legacy"]);
    expect(new Set(auth)).toEqual(new Set(["Bearer key"]));
    expect(listQueries).toEqual(
      [0, 2, 4].map(
        (offset) => `?q=e2e&include_personal=false&page_id=${offset}&limit=100`,
      ),
    );
  });

  test("a run's teardown deletes only that run's orgs", async () => {
    await sweepE2eOrgs(baseUrl, "key", (org) => org.runId === "abc", "test");
    expect(deleted).toEqual(["this-run"]);
  });

  test("an unreachable API is logged, not thrown", async () => {
    await expect(
      sweepE2eOrgs("http://127.0.0.1:9/", "key", () => true, "test"),
    ).resolves.toBeUndefined();
  });
});

test.describe("deleteOrphanedAccounts against a stand-in admin API", () => {
  const email = "e2e-new-user@e2e.example.com";
  const users = [
    { user_id: "current-sub", email },
    { user_id: "last-owner", email },
    { user_id: null, email },
    { user_id: "real-user", email: "someone@acme.com" },
    { user_id: "old-sub", email: "E2E-New-User@e2e.example.com" },
  ];
  let server: Server;
  let baseUrl = "";
  let deleted: string[] = [];

  test.beforeAll(async () => {
    server = createServer((req, res) => {
      const url = new URL(req.url ?? "", "http://stand-in");
      const id = /^\/api\/admin\/directory\/users\/(.+)$/.exec(
        url.pathname,
      )?.[1];
      if (req.headers.authorization !== "Bearer key") {
        res.writeHead(401).end();
      } else if (
        req.method === "GET" &&
        url.pathname === "/api/admin/directory/users"
      ) {
        servePage(res, url, users);
      } else if (req.method === "DELETE" && id === "last-owner") {
        res.writeHead(409).end("last owner");
      } else if (req.method === "DELETE" && id) {
        deleted.push(id);
        res.writeHead(200).end("{}");
      } else {
        res.writeHead(404).end();
      }
    });
    await new Promise<void>((resolve) => server.listen(0, resolve));
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/`;
  });

  test.afterAll(async () => {
    await new Promise((resolve) => server.close(resolve));
  });

  test.beforeEach(() => {
    deleted = [];
  });

  test("deletes only the other accounts with the email and survives a refused delete", async () => {
    await deleteOrphanedAccounts(baseUrl, "key", email, "current-sub", "test");
    expect(deleted).toEqual(["old-sub"]);
  });

  test("refuses an email outside the e2e-only domains", async () => {
    await expect(
      deleteOrphanedAccounts(baseUrl, "key", "someone@acme.com", "x", "test"),
    ).rejects.toThrow();
    expect(deleted).toEqual([]);
  });

  test("an unreachable API is logged, not thrown", async () => {
    await expect(
      deleteOrphanedAccounts("http://127.0.0.1:9/", "key", email, "x", "test"),
    ).resolves.toBeUndefined();
  });
});
