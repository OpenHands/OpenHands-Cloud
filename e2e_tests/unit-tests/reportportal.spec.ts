import { expect, test } from "@playwright/test";

import { getReportPortalReporter } from "../reportportal";

const reportPortalEnv = {
  REPORTPORTAL_ENABLED: "true",
  REPORTPORTAL_ENDPOINT: "http://127.0.0.1:1/api/v2",
  REPORTPORTAL_PROJECT: "openhands",
  REPORTPORTAL_API_KEY: "not-a-real-key",
  REPORTPORTAL_ENVIRONMENT: "development",
};

test("ReportPortal gets one row per test, not one row per browser action", () => {
  const savedEnv = { ...process.env };
  Object.assign(process.env, reportPortalEnv);
  try {
    const reporter = getReportPortalReporter();
    expect(reporter?.[0]).toBe("@reportportal/agent-js-playwright");
    expect(reporter?.[1]).toMatchObject({ includeTestSteps: false });
  } finally {
    process.env = savedEnv;
  }
});
