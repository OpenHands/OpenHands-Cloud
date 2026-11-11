import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

import { expect, test } from "@playwright/test";

import {
  waitForRunCompleted,
  waitForRunFailed,
  waitForRunTerminal,
  type RunStatus,
} from "../utils/automations";

/**
 * Regression fixture for the automation run helpers.
 *
 * ``waitForRunTerminal`` accepts any terminal status, which is how a run that
 * ended ``FAILED`` used to pass the automation suite. These tests run a local
 * stand-in for ``GET /api/automation/v1/{id}/runs`` that replays a scripted
 * status sequence, then prove ``waitForRunCompleted`` rejects every outcome
 * except ``COMPLETED`` and reports the failure detail. The failure-path
 * helper ``waitForRunFailed`` gets the mirror proof: only ``FAILED`` passes.
 */

const API_KEY = "qa-fixture-key";
const POLL_MS = 10;
const TIMEOUT_MS = 300;
const runSuffix = `qa-${Date.now().toString(36)}`;

interface ScriptedRun {
  statuses: RunStatus[];
  errorDetail?: string;
}

const scripts = new Map<string, ScriptedRun>();
const polls = new Map<string, number>();
let server: Server;
let baseUrl = "";

function script(name: string, run: ScriptedRun): string {
  const id = `${name}-${runSuffix}`;
  scripts.set(id, run);
  return id;
}

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const match = /^\/api\/automation\/v1\/([^/?]+)\/runs/.exec(req.url ?? "");
    const scripted = match ? scripts.get(decodeURIComponent(match[1])) : null;
    if (req.headers.authorization !== `Bearer ${API_KEY}` || !scripted) {
      res.writeHead(match ? 401 : 404).end();
      return;
    }
    const id = decodeURIComponent((match as RegExpExecArray)[1]);
    const poll = polls.get(id) ?? 0;
    polls.set(id, poll + 1);
    const status =
      scripted.statuses[Math.min(poll, scripted.statuses.length - 1)];
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        runs: [
          {
            id: `run-${id}`,
            status,
            error_detail: status === "FAILED" ? scripted.errorDetail : null,
          },
        ],
      }),
    );
  });
  await new Promise<void>((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

test.afterAll(async () => {
  await new Promise<void>((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
});

test("the previous helper accepts a terminal FAILED run", async () => {
  const id = script("legacy-failed", {
    statuses: ["PENDING", "RUNNING", "FAILED"],
    errorDetail: "fixture: sandbox exited with code 1",
  });

  const run = await waitForRunTerminal(
    baseUrl,
    API_KEY,
    id,
    TIMEOUT_MS,
    POLL_MS,
  );

  expect(run.status).toBe("FAILED");
});

test("the corrected helper rejects a terminal FAILED run and prints the final failure detail", async () => {
  const id = script("failed", {
    statuses: ["PENDING", "RUNNING", "FAILED"],
    errorDetail: "fixture: sandbox exited with code 1",
  });

  const outcome = await waitForRunCompleted(
    baseUrl,
    API_KEY,
    id,
    TIMEOUT_MS,
    POLL_MS,
  ).then(
    () => "resolved",
    (error: Error) => error.message,
  );

  expect(outcome, "FAILED must not be accepted as success").not.toBe(
    "resolved",
  );
  expect(outcome).toContain(`run run-${id} of automation ${id}`);
  expect(outcome).toContain("ended FAILED, expected COMPLETED");
  expect(outcome).toContain(
    "error_detail: fixture: sandbox exited with code 1",
  );
  expect(outcome).toContain("lifecycle: PENDING -> RUNNING -> FAILED");
});

test("the corrected helper reports a FAILED run that had no error detail", async () => {
  const id = script("failed-no-detail", { statuses: ["FAILED"] });

  await expect(
    waitForRunCompleted(baseUrl, API_KEY, id, TIMEOUT_MS, POLL_MS),
  ).rejects.toThrow(
    "ended FAILED, expected COMPLETED (error_detail: none; lifecycle: FAILED)",
  );
});

(["CANCELLED", "SKIPPED"] as const).forEach((status) => {
  test(`the corrected helper rejects a terminal ${status} run`, async () => {
    const id = script(status.toLowerCase(), {
      statuses: ["RUNNING", status],
    });

    await expect(
      waitForRunCompleted(baseUrl, API_KEY, id, TIMEOUT_MS, POLL_MS),
    ).rejects.toThrow(
      `ended ${status}, expected COMPLETED (error_detail: none; lifecycle: RUNNING -> ${status})`,
    );
  });
});

test("the corrected helper times out on a run whose callback never arrives", async () => {
  const id = script("missing-callback", { statuses: ["PENDING", "RUNNING"] });

  await expect(
    waitForRunCompleted(baseUrl, API_KEY, id, TIMEOUT_MS, POLL_MS),
  ).rejects.toThrow(
    `did not reach a terminal status within ${TIMEOUT_MS}ms (lifecycle: PENDING -> RUNNING)`,
  );
});

test("the corrected helper returns a COMPLETED run", async () => {
  const id = script("completed", {
    statuses: ["PENDING", "RUNNING", "COMPLETED"],
  });

  const run = await waitForRunCompleted(
    baseUrl,
    API_KEY,
    id,
    TIMEOUT_MS,
    POLL_MS,
  );

  expect(run).toMatchObject({ id: `run-${id}`, status: "COMPLETED" });
});

test("the failure-path helper returns a FAILED run with its error detail", async () => {
  const id = script("expected-failed", {
    statuses: ["PENDING", "RUNNING", "FAILED"],
    errorDetail: "fixture: expected diagnostic",
  });

  const run = await waitForRunFailed(baseUrl, API_KEY, id, TIMEOUT_MS, POLL_MS);

  expect(run).toMatchObject({
    id: `run-${id}`,
    status: "FAILED",
    error_detail: "fixture: expected diagnostic",
  });
});

test("the failure-path helper rejects a COMPLETED run", async () => {
  const id = script("unexpected-completed", {
    statuses: ["PENDING", "RUNNING", "COMPLETED"],
  });

  await expect(
    waitForRunFailed(baseUrl, API_KEY, id, TIMEOUT_MS, POLL_MS),
  ).rejects.toThrow(
    `waitForRunFailed: run run-${id} of automation ${id} ended COMPLETED, expected FAILED (error_detail: none; lifecycle: PENDING -> RUNNING -> COMPLETED)`,
  );
});

(["CANCELLED", "SKIPPED"] as const).forEach((status) => {
  test(`the failure-path helper rejects a terminal ${status} run`, async () => {
    const id = script(`expected-failed-${status.toLowerCase()}`, {
      statuses: ["RUNNING", status],
    });

    await expect(
      waitForRunFailed(baseUrl, API_KEY, id, TIMEOUT_MS, POLL_MS),
    ).rejects.toThrow(`ended ${status}, expected FAILED`);
  });
});

test("the failure-path helper times out on a run whose callback never arrives", async () => {
  const id = script("expected-failed-missing-callback", {
    statuses: ["PENDING", "RUNNING"],
  });

  await expect(
    waitForRunFailed(baseUrl, API_KEY, id, TIMEOUT_MS, POLL_MS),
  ).rejects.toThrow(
    `did not reach a terminal status within ${TIMEOUT_MS}ms (lifecycle: PENDING -> RUNNING)`,
  );
});
