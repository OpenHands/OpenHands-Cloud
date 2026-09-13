import type { ReporterDescription } from "@playwright/test";

function requiredEnvironmentVariable(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(
      `${name} is required when ReportPortal reporting is enabled.`,
    );
  }
  return value;
}

export function getReportPortalReporter(): ReporterDescription | undefined {
  const enabled = process.env.REPORTPORTAL_ENABLED?.trim().toLowerCase();
  if (!enabled || enabled === "false") {
    return undefined;
  }
  if (enabled !== "true") {
    throw new Error("REPORTPORTAL_ENABLED must be either true or false.");
  }

  const endpoint = new URL(
    requiredEnvironmentVariable("REPORTPORTAL_ENDPOINT"),
  ).toString();
  const environment = requiredEnvironmentVariable("REPORTPORTAL_ENVIRONMENT");
  const revision = process.env.REPORTPORTAL_REVISION?.trim();
  const workflow = process.env.REPORTPORTAL_WORKFLOW?.trim();
  const attributes = [{ key: "environment", value: environment }];

  if (revision) {
    attributes.push({ key: "revision", value: revision });
  }
  if (workflow) {
    attributes.push({ key: "workflow", value: workflow });
  }

  return [
    "@reportportal/agent-js-playwright",
    {
      apiKey: requiredEnvironmentVariable("REPORTPORTAL_API_KEY"),
      endpoint,
      project: requiredEnvironmentVariable("REPORTPORTAL_PROJECT"),
      launch: process.env.REPORTPORTAL_LAUNCH?.trim() || "OpenHands Cloud E2E",
      attributes,
      description: revision
        ? `OpenHands Cloud release revision ${revision}`
        : undefined,
      includeTestSteps: true,
      includePlaywrightProjectNameToCodeReference: true,
      skippedIssue: false,
      uploadTrace: true,
      uploadVideo: true,
    },
  ];
}
