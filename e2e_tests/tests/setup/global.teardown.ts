import { test as teardown } from "@playwright/test";

import { e2eRunId, isEnvironment } from "../../utils/config";
import { sweepE2eOrgs } from "../../utils/org-management";

// Runs after every test project, also when tests fail. Anything it deletes is
// an org a spec's own cleanup missed, so a non-zero count is worth a look.
teardown("delete e2e orgs this run left behind", async ({ baseURL }) => {
  const apiKey = process.env.SUPER_ADMIN_API_KEY;
  teardown.skip(!apiKey, "SUPER_ADMIN_API_KEY is not set");
  teardown.skip(isEnvironment("production"), "never sweeps production");

  const runId = e2eRunId();
  await sweepE2eOrgs(
    baseURL || "",
    apiKey!,
    (org) => org.runId === runId,
    `Global teardown (run ${runId})`,
  );
});
