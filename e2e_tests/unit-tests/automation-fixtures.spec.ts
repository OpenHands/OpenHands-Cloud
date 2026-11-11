import { execFileSync, spawn } from "node:child_process";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";

import { expect, test } from "@playwright/test";

import {
  completedRunScript,
  failedRunScript,
  missingCallbackRunScript,
  singleFileTarGz,
  stalledRunScript,
} from "../utils/automation-fixtures";

/**
 * The automation journey uploads a tarball it builds itself, so the archive
 * must be one the system ``tar`` accepts, and the script must carry the
 * run-scoped output exactly as a Python string literal.
 */

const runSuffix = `qa-${Date.now().toString(36)}`;

test("the tarball holds the script as main.py at the archive root", () => {
  const content = `print("tarball-${runSuffix}")\n`.repeat(40);

  const archive = singleFileTarGz("main.py", content);

  const listing = execFileSync("tar", ["-tzf", "-"], { input: archive })
    .toString()
    .trim();
  expect(listing).toBe("main.py");
  const extracted = execFileSync("tar", ["-xzOf", "-"], {
    input: archive,
  }).toString();
  expect(extracted).toBe(content);
});

test("a file whose size is a multiple of the tar block still extracts intact", () => {
  const content = "x".repeat(1024);

  const extracted = execFileSync("tar", ["-xzOf", "-"], {
    input: singleFileTarGz("main.py", content),
  }).toString();

  expect(extracted).toBe(content);
});

test("the completed-run script embeds the expected output as a string literal", () => {
  const output = `automation-output-${runSuffix} with "quotes" and \\ backslash`;

  const script = completedRunScript(output);

  expect(script).toContain(`EXPECTED_OUTPUT = ${JSON.stringify(output)}`);
  expect(script).not.toContain("__EXPECTED_OUTPUT__");
});

test("the failed-run script embeds the expected error as a string literal", () => {
  const error = `QA_EXPECTED_FAILURE ${runSuffix} with "quotes" and \\ backslash`;

  const script = failedRunScript(error);

  expect(script).toContain(`EXPECTED_ERROR = ${JSON.stringify(error)}`);
  expect(script).not.toContain("__EXPECTED_ERROR__");
});

test("the failed-run script reports FAILED with exactly the expected error and exits non-zero", async () => {
  const error = `QA_EXPECTED_FAILURE ${runSuffix}: upstream returned 503`;
  const received: Array<{ authorization?: string; body: unknown }> = [];
  const server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => {
      received.push({
        authorization: req.headers.authorization,
        body: JSON.parse(Buffer.concat(chunks).toString()),
      });
      res.writeHead(200).end("{}");
    });
  });
  await new Promise<void>((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });
  try {
    const { port } = server.address() as AddressInfo;
    const child = spawn("python3", ["-c", failedRunScript(error)], {
      env: {
        ...process.env,
        AUTOMATION_RUN_ID: `run-${runSuffix}`,
        AUTOMATION_CALLBACK_URL: `http://127.0.0.1:${port}/callback`,
        AUTOMATION_CALLBACK_API_KEY: "qa-callback-key",
      },
    });
    const exitCode = await new Promise<number | null>((resolve) => {
      child.on("close", resolve);
    });

    expect(exitCode).toBe(1);
    expect(received).toEqual([
      {
        authorization: "Bearer qa-callback-key",
        body: { status: "FAILED", run_id: `run-${runSuffix}`, error },
      },
    ]);
  } finally {
    await new Promise<void>((resolve) => {
      server.close(() => resolve());
    });
  }
});

interface ScriptOutcome {
  exitCode: number | null;
  stderr: string;
  callbacks: number;
}

/** Run a fixture script against a callback listener that counts every request. */
async function runWithCallbackListener(script: string): Promise<ScriptOutcome> {
  let callbacks = 0;
  const server = createServer((_req, res) => {
    callbacks += 1;
    res.writeHead(200).end("{}");
  });
  await new Promise<void>((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });
  try {
    const { port } = server.address() as AddressInfo;
    const child = spawn("python3", ["-c", script], {
      env: {
        ...process.env,
        AUTOMATION_RUN_ID: `run-${runSuffix}`,
        AUTOMATION_CALLBACK_URL: `http://127.0.0.1:${port}/callback`,
        AUTOMATION_CALLBACK_API_KEY: "qa-callback-key",
      },
    });
    let stderr = "";
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    const exitCode = await new Promise<number | null>((resolve) => {
      child.on("close", resolve);
    });
    return { exitCode, stderr, callbacks };
  } finally {
    await new Promise<void>((resolve) => {
      server.close(() => resolve());
    });
  }
}

test("the stalled-run script embeds its stall bound as a number", () => {
  const script = stalledRunScript(120);

  expect(script).toContain("MAX_STALL_SECONDS = 120");
  expect(script).not.toContain("__MAX_STALL_SECONDS__");
});

test("the stalled-run script stops at its bound, exits non-zero and never calls back", async () => {
  const startedAt = Date.now();

  const outcome = await runWithCallbackListener(stalledRunScript(2));

  expect(outcome.exitCode).toBe(1);
  expect(outcome.callbacks).toBe(0);
  expect(outcome.stderr).toContain("fixture stall bound reached");
  expect(Date.now() - startedAt).toBeGreaterThanOrEqual(2_000);
});

test("the missing-callback script exits non-zero without calling back", async () => {
  const outcome = await runWithCallbackListener(missingCallbackRunScript());

  expect(outcome.exitCode).toBe(1);
  expect(outcome.callbacks).toBe(0);
});
