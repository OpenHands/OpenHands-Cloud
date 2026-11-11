import { execFileSync } from "node:child_process";

import { expect, test } from "@playwright/test";

import {
  completedRunScript,
  singleFileTarGz,
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
