import { expect, test } from "@playwright/test";

import { Cleanup } from "../utils/cleanup";
import {
  e2eOrgCreatedAt,
  e2eOrgName,
  e2eTimestamp,
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

test("an e2e org name carries the time it was created", () => {
  const now = new Date("2026-11-04T20:03:58.435Z");
  expect(e2eOrgName("test org", e2eTimestamp(now))).toBe(
    "e2e test org 2026-11-04T20-03-58-435Z",
  );
  expect(
    e2eOrgCreatedAt(e2eOrgName("automations member", e2eTimestamp(now))),
  ).toEqual(now);
});

for (const name of [
  "Personal Workspace",
  "e2e test org",
  "managed-key-e2e-1793822400000",
  "sa-e2e-a-1793822400000",
  "acme e2e test org 2026-11-04T20-03-58-435Z",
  "e2e test org 2026-13-04T20-03-58-435Z",
]) {
  test(`"${name}" is not an e2e org name`, () => {
    expect(e2eOrgCreatedAt(name)).toBeNull();
  });
}
