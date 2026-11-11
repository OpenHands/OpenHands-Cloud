import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

import { expect, test } from "@playwright/test";

import {
  cancelActiveRuns,
  captureSuccessAssertionFailure,
  type RunStatus,
} from "../utils/automations";

/**
 * The non-COMPLETED journey relies on two helpers: the one that evaluates the
 * success assertion for a run built to fail it, and the one that cancels
 * whatever its runs left active. A local stand-in for the automation service
 * replays a scripted run and applies cancellations, so both are proved
 * against the routes the journey uses.
 */

const API_KEY = "qa-fixture-key";
const POLL_MS = 10;
const TIMEOUT_MS = 300;
const runSuffix = `qa-${Date.now().toString(36)}`;

interface ScriptedRun {
  runId: string;
  statuses: RunStatus[];
  errorDetail?: string;
}

const scripts = new Map<string, ScriptedRun>();
const polls = new Map<string, number>();
const cancelRequests: string[] = [];
const cancelled = new Set<string>();
let server: Server;
let baseUrl = "";

function script(name: string, run: Omit<ScriptedRun, "runId">): string {
  const id = `${name}-${runSuffix}`;
  scripts.set(id, { runId: `run-${id}`, ...run });
  return id;
}

function currentStatus(id: string, scripted: ScriptedRun): RunStatus {
  if (cancelled.has(scripted.runId)) {
    return "CANCELLED";
  }
  const poll = polls.get(id) ?? 0;
  polls.set(id, poll + 1);
  return scripted.statuses[Math.min(poll, scripted.statuses.length - 1)];
}

test.beforeAll(async () => {
  server = createServer((req, res) => {
    if (req.headers.authorization !== `Bearer ${API_KEY}`) {
      res.writeHead(401).end();
      return;
    }
    const runs = /^\/api\/automation\/v1\/([^/?]+)\/runs/.exec(req.url ?? "");
    const cancel = /^\/api\/automation\/v1\/runs\/([^/?]+)\/cancel/.exec(
      req.url ?? "",
    );
    if (runs && req.method === "GET") {
      const id = decodeURIComponent(runs[1]);
      const scripted = scripts.get(id);
      if (!scripted) {
        res.writeHead(404).end();
        return;
      }
      const status = currentStatus(id, scripted);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          runs: [
            {
              id: scripted.runId,
              status,
              error_detail: status === "FAILED" ? scripted.errorDetail : null,
            },
          ],
        }),
      );
      return;
    }
    if (cancel && req.method === "POST") {
      const runId = decodeURIComponent(cancel[1]);
      cancelRequests.push(runId);
      const terminal = [...scripts.values()].some(
        (scripted) =>
          scripted.runId === runId &&
          scripted.statuses[scripted.statuses.length - 1] !== "RUNNING",
      );
      if (terminal) {
        res.writeHead(409).end(JSON.stringify({ detail: "already terminal" }));
        return;
      }
      cancelled.add(runId);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ id: runId, status: "CANCELLED" }));
      return;
    }
    res.writeHead(404).end();
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

test("the success assertion failure of a stalled run reports the timeout and its lifecycle", async () => {
  const id = script("stalled", { statuses: ["PENDING", "RUNNING"] });

  const failure = await captureSuccessAssertionFailure(
    baseUrl,
    API_KEY,
    id,
    TIMEOUT_MS,
    POLL_MS,
  );

  expect(failure.message).toContain(
    `did not reach a terminal status within ${TIMEOUT_MS}ms (lifecycle: PENDING -> RUNNING)`,
  );
  expect(failure.elapsedMs).toBeGreaterThanOrEqual(TIMEOUT_MS);
});

test("the success assertion failure of a cancelled run reports CANCELLED and returns before the bound", async () => {
  const id = script("cancelled", { statuses: ["RUNNING", "CANCELLED"] });

  const failure = await captureSuccessAssertionFailure(
    baseUrl,
    API_KEY,
    id,
    TIMEOUT_MS,
    POLL_MS,
  );

  expect(failure.message).toContain(
    "ended CANCELLED, expected COMPLETED (error_detail: none; lifecycle: RUNNING -> CANCELLED)",
  );
  expect(failure.elapsedMs).toBeLessThan(TIMEOUT_MS);
});

test("the success assertion failure of a FAILED run carries the error detail", async () => {
  const id = script("failed", {
    statuses: ["RUNNING", "FAILED"],
    errorDetail: "fixture: exit_code=1",
  });

  const failure = await captureSuccessAssertionFailure(
    baseUrl,
    API_KEY,
    id,
    TIMEOUT_MS,
    POLL_MS,
  );

  expect(failure.message).toContain(
    "ended FAILED, expected COMPLETED (error_detail: fixture: exit_code=1; lifecycle: RUNNING -> FAILED)",
  );
});

test("a run that ends COMPLETED makes the evaluation itself fail", async () => {
  const id = script("completed", { statuses: ["RUNNING", "COMPLETED"] });

  await expect(
    captureSuccessAssertionFailure(baseUrl, API_KEY, id, TIMEOUT_MS, POLL_MS),
  ).rejects.toThrow(
    `the success assertion passed run run-${id} of automation ${id}`,
  );
});

test("cancelActiveRuns cancels a running run and reports its id", async () => {
  const id = script("active", { statuses: ["RUNNING"] });

  const ids = await cancelActiveRuns(baseUrl, API_KEY, id);

  expect(ids).toEqual([`run-${id}`]);
  expect(cancelRequests).toContain(`run-${id}`);
});

test("cancelActiveRuns leaves a terminal run alone", async () => {
  const id = script("terminal", { statuses: ["COMPLETED"] });

  const ids = await cancelActiveRuns(baseUrl, API_KEY, id);

  expect(ids).toEqual([]);
  expect(cancelRequests).not.toContain(`run-${id}`);
});

test("cancelActiveRuns skips a run that ends before its cancel arrives", async () => {
  const id = script("racing", { statuses: ["PENDING", "COMPLETED"] });

  const ids = await cancelActiveRuns(baseUrl, API_KEY, id);

  expect(ids).toEqual([]);
  expect(cancelRequests).toContain(`run-${id}`);
});
