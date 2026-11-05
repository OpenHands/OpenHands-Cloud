import { test as setup } from "@playwright/test";

import { e2eRunId, isEnvironment } from "../../utils/config";
import { sweepE2eOrgs } from "../../utils/org-management";

// Older than any run still in progress, so concurrent runs keep their orgs.
const STALE_AFTER_MS = 2 * 60 * 60 * 1000;

// Catches what crashed runs left behind, before this run's specs create more.
setup("delete e2e orgs earlier runs left behind", async ({ baseURL }) => {
  const apiKey = process.env.SUPER_ADMIN_API_KEY;
  setup.skip(!apiKey, "SUPER_ADMIN_API_KEY is not set");
  setup.skip(isEnvironment("production"), "never sweeps production");

  const runId = e2eRunId();
  const cutoff = Date.now() - STALE_AFTER_MS;
  await sweepE2eOrgs(
    baseURL || "",
    apiKey!,
    (org) => org.runId !== runId && org.createdAt.getTime() < cutoff,
    "Global setup",
  );
});
