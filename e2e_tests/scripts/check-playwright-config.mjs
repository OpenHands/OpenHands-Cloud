import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const playwrightRoot = path.dirname(require.resolve("playwright/package.json"));
const { loadConfigFromFile } = require(
  path.join(playwrightRoot, "lib/common/configLoader.js"),
);

process.env.BASE_URL ??= "https://release-under-test.invalid";

const packageRoot = path.resolve(import.meta.dirname, "..");
const config = await loadConfigFromFile(
  path.join(packageRoot, "playwright.config.ts"),
);
const projects = new Map(
  config.projects.map(({ project }) => [project.name, project]),
);

assert.deepEqual(projects.get("setup")?.use.storageState, {
  cookies: [],
  origins: [],
});

const authFile = path.join(packageRoot, "fixtures/auth.json");
for (const browser of ["chromium", "firefox", "webkit"]) {
  assert.equal(projects.get(browser)?.use.storageState, authFile);
}
