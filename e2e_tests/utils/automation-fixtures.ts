import { readFileSync } from "node:fs";
import path from "node:path";
import { gzipSync } from "node:zlib";

/**
 * Deterministic script fixtures for automation specs.
 *
 * The scripts live next to this file in ``automation-fixtures/`` and run
 * inside the sandbox with the standard library only, so a run's outcome does
 * not depend on any LLM, repository or credential of the target.
 */

const fixturesDir = path.resolve(import.meta.dirname, "automation-fixtures");
const TAR_BLOCK = 512;

function octal(value: number, width: number): string {
  return `${value.toString(8).padStart(width - 1, "0")}\0`;
}

/** A gzipped tar archive holding one regular file at the archive root. */
export function singleFileTarGz(fileName: string, content: string): Buffer {
  const data = Buffer.from(content, "utf8");
  const header = Buffer.alloc(TAR_BLOCK);
  header.write(fileName, 0, 100, "ascii");
  header.write(octal(0o644, 8), 100, "ascii");
  header.write(octal(0, 8), 108, "ascii");
  header.write(octal(0, 8), 116, "ascii");
  header.write(octal(data.length, 12), 124, "ascii");
  header.write(octal(Math.floor(Date.now() / 1000), 12), 136, "ascii");
  header.write("        ", 148, "ascii");
  header.write("0", 156, "ascii");
  header.write("ustar\0", 257, "ascii");
  header.write("00", 263, "ascii");
  const checksum = header.reduce((sum, byte) => sum + byte, 0);
  header.write(`${checksum.toString(8).padStart(6, "0")}\0 `, 148, "ascii");

  const padding = Buffer.alloc(
    (TAR_BLOCK - (data.length % TAR_BLOCK)) % TAR_BLOCK,
  );
  const endOfArchive = Buffer.alloc(TAR_BLOCK * 2);
  return gzipSync(Buffer.concat([header, data, padding, endOfArchive]));
}

/**
 * Script that drives one conversation against a loopback stub LLM, whose only
 * reply calls the ``finish`` tool with ``expectedOutput``, and then reports
 * ``COMPLETED`` with that conversation's id. It reports ``FAILED`` with the
 * reason on any error.
 */
export function completedRunScript(expectedOutput: string): string {
  const source = readFileSync(
    path.join(fixturesDir, "completed_run.py"),
    "utf8",
  );
  return source.replace("__EXPECTED_OUTPUT__", JSON.stringify(expectedOutput));
}

/**
 * Script that raises a known error and reports ``FAILED`` with
 * ``expectedError`` through the completion callback. It starts no
 * conversation, so a run of it must end ``FAILED`` with no linked conversation.
 */
export function failedRunScript(expectedError: string): string {
  const source = readFileSync(path.join(fixturesDir, "failed_run.py"), "utf8");
  return source.replace("__EXPECTED_ERROR__", JSON.stringify(expectedError));
}

/**
 * Script that prints a heartbeat and then stalls without ever calling the
 * completion callback, so its run stays ``RUNNING`` until something cancels
 * it. It exits on its own after ``maxStallSeconds``, which bounds a run the
 * suite abandons.
 */
export function stalledRunScript(maxStallSeconds: number): string {
  const source = readFileSync(path.join(fixturesDir, "stalled_run.py"), "utf8");
  return source.replace("__MAX_STALL_SECONDS__", String(maxStallSeconds));
}

/**
 * Script that exits non-zero without calling the completion callback, as a
 * script that crashes before it can report would. The service gets no
 * outcome from it, so the run can only end by the service's own deadline.
 */
export function missingCallbackRunScript(): string {
  return readFileSync(
    path.join(fixturesDir, "missing_callback_run.py"),
    "utf8",
  );
}
