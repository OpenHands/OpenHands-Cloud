import { test, expect, type BrowserContext } from "@playwright/test";

import {
  BudgetDatabase,
  type BudgetCycleState,
  type BudgetMaintenanceResult,
} from "../utils/budget-database";
import {
  BudgetApi,
  type BudgetSettings,
  type BudgetUser,
  type LiteLLMTeamState,
  type MemberFinancial,
  createLiteLLMTestKey,
  deleteLiteLLMTestKey,
  findSlackBudgetAlert,
  generateDirectLiteLLMSpend,
  getLiteLLMTeamState,
  getLiteLLMVersion,
  loadBudgetE2EConfig,
  pollUntil,
  updateLiteLLMMemberCap,
  updateLiteLLMTeamCap,
} from "../utils/budgets";
import { authReturningFile, env, runUser } from "../utils/config";

interface BudgetEvidence {
  rolloverMaintenance: BudgetMaintenanceResult;
  firstSpendMaintenance: BudgetMaintenanceResult;
  secondSpendMaintenance: BudgetMaintenanceResult;
  disabledMaintenance: BudgetMaintenanceResult;
  cycleAfterRollover: BudgetCycleState;
  cycleAfterFirstSpend: BudgetCycleState;
  cycleAfterSecondSpend: BudgetCycleState;
  teamAfterRollover: LiteLLMTeamState;
  teamAfterFirstSpend: LiteLLMTeamState;
  teamAfterSecondSpend: LiteLLMTeamState;
  memberAfterRollover: MemberFinancial;
  memberAfterFirstSpend: MemberFinancial;
  memberAfterSecondSpend: MemberFinancial;
  reportingSpendBefore: number;
  reportingSpendAfter: number;
  financialSpendBefore: number;
  financialSpendAfter: number;
  conversationsBeforeDirectSpend: number;
  conversationsAfterDirectSpend: number;
  alertReportingSpendBefore: number;
  alertReportingSpendAfter: number;
  alertFinancialSpendBefore: number;
  alertFinancialSpendAfter: number;
  alertTeamSpendBefore: number;
  alertTeamSpendAfter: number;
  slackAlertSpend: number;
  slackAlertText: string;
  overrideMaintenance: BudgetMaintenanceResult;
  overrideLimit: number;
  overrideExpectedCap: number;
  overrideExpectedMember: MemberFinancial;
  overrideClearedMember: MemberFinancial;
  overrideReconciledMember: MemberFinancial;
  disabledSyncBefore: string | null;
  disabledSyncAfter: string | null;
  disabledSyncStatus: string | null;
  seededDisabledTeam: LiteLLMTeamState;
  observedDisabledTeam: LiteLLMTeamState;
  seededDisabledMember: MemberFinancial;
  observedDisabledMember: MemberFinancial;
}

const config = loadBudgetE2EConfig();
const suiteTimeout = config.syncTimeoutMs * 6 + 10 * 60_000;
const closeToPrecision = 2;
const maxSpendAttempts = 8;

function restorePayload(settings: BudgetSettings): Record<string, unknown> {
  return {
    enabled: settings.enabled,
    monthly_limit: settings.monthly_limit,
    reset_day: settings.reset_day,
    default_user_monthly_limit: settings.default_user_monthly_limit,
    slack_channel: settings.slack_channel,
    slack_team_id: settings.slack_team_id,
    thresholds: settings.thresholds,
  };
}

function requireSuccessfulSync(
  settings: BudgetSettings,
  operation: string,
): void {
  if (settings.litellm_last_sync_status !== "success") {
    throw new Error(
      `${operation} LiteLLM sync status was ${settings.litellm_last_sync_status}: ${settings.litellm_last_sync_error || "no error detail"}`,
    );
  }
}

function attachEvidence(
  name: string,
  evidence: Record<string, unknown>,
): Promise<void> {
  return test.info().attach(name, {
    body: Buffer.from(JSON.stringify(evidence, null, 2)),
    contentType: "application/json",
  });
}

function runMaintenance(
  database: BudgetDatabase,
): Promise<BudgetMaintenanceResult> {
  return database.runMaintenance(
    config.orgId,
    config.syncTimeoutMs,
    config.pollIntervalMs,
  );
}

async function generateMinimumDirectSpend(
  api: BudgetApi,
  userId: string,
  key: string,
  startingSpend: number,
): Promise<MemberFinancial> {
  let member = await api.getMemberFinancial(userId);
  for (let attempt = 1; attempt <= maxSpendAttempts; attempt += 1) {
    await generateDirectLiteLLMSpend(config, key);
    const previousSpend = member.lifetime_spend;
    member = await pollUntil(
      () => api.getMemberFinancial(userId),
      (candidate) => candidate.lifetime_spend > previousSpend,
      {
        description: `direct LiteLLM spend attempt ${attempt}`,
        timeoutMs: 2 * 60_000,
        intervalMs: config.pollIntervalMs,
      },
    );
    if (member.lifetime_spend - startingSpend >= config.minimumSpendDelta) {
      return member;
    }
  }
  throw new Error(
    `Direct LiteLLM spend increased by only ${member.lifetime_spend - startingSpend}; expected at least ${config.minimumSpendDelta}`,
  );
}

test.describe("organization budget maintenance @budgets", () => {
  test.describe.configure({ mode: "serial" });

  let context: BrowserContext | undefined;
  let api: BudgetApi | undefined;
  let database: BudgetDatabase | undefined;
  let userId = "";
  let originalOrgId: string | null = null;
  let originalBudget: BudgetSettings | undefined;
  let originalCycle: BudgetCycleState | undefined;
  let originalOverride: BudgetUser | undefined;
  let originalTeam: LiteLLMTeamState | undefined;
  let originalMember: MemberFinancial | undefined;
  let testKeyAlias: string | undefined;
  let evidence: BudgetEvidence | undefined;

  test.beforeEach(({ browser: _browser }, testInfo) => {
    test.skip(
      runUser(testInfo) !== "returning",
      "budget maintenance runs once with the configured returning admin",
    );
    test.skip(
      !config.enabled,
      "set the complete BUDGET_E2E_* certification contract for a dedicated non-personal test org",
    );
  });

  test.beforeAll(async ({ browser }, testInfo) => {
    testInfo.setTimeout(suiteTimeout);
    const role = testInfo.project.metadata.user;
    if (role !== "returning" || !config.enabled) return;
    if (
      !config.databaseUrl ||
      !config.litellmUrl ||
      !config.litellmApiKey ||
      !config.slack
    ) {
      throw new Error(
        "Complete budget E2E certification configuration is required",
      );
    }

    context = await browser.newContext({
      baseURL: env.baseUrl,
      storageState: authReturningFile,
      ignoreHTTPSErrors: true,
    });
    api = new BudgetApi(context.request, config.orgId);
    database = new BudgetDatabase(config.databaseUrl);
    const liteLLMVersion = await getLiteLLMVersion(config);
    if (liteLLMVersion !== config.expectedLiteLLMVersion) {
      throw new Error(
        `Budget certification requires LiteLLM ${config.expectedLiteLLMVersion}, received ${liteLLMVersion}`,
      );
    }

    const orgPage = await api.getOrganizations();
    originalOrgId = orgPage.current_org_id;
    const org = await api.getOrg();
    if (org.is_personal) {
      throw new Error("BUDGET_E2E_ORG_ID must not be a personal organization");
    }
    if (
      !/budget|e2e|test/i.test(org.name) &&
      process.env.BUDGET_E2E_ALLOW_ANY_ORG?.toLowerCase() !== "true"
    ) {
      throw new Error(
        `Refusing to mutate org "${org.name}"; use a dedicated budget/E2E/test org or set BUDGET_E2E_ALLOW_ANY_ORG=true`,
      );
    }

    await api.switchOrg(config.orgId);
    userId = (await api.getMe()).user_id;
    originalBudget = await api.getBudget();
    originalCycle = await database.getCycleState(config.orgId);
    originalOverride = originalBudget.users.find(
      (user) => user.user_id === userId && user.is_override,
    );
    originalTeam = await getLiteLLMTeamState(config);
    originalMember = await api.getMemberFinancial(userId);

    const configuredBudget = await api.patchBudget({
      enabled: true,
      monthly_limit: config.monthlyLimit,
      default_user_monthly_limit: config.userMonthlyLimit,
      slack_channel: null,
      slack_team_id: null,
      thresholds: [],
    });
    requireSuccessfulSync(configuredBudget, "initial budget update");
    await api.deleteOverride(userId);
    requireSuccessfulSync(await api.getBudget(), "member override removal");

    await database.makeCycleStale(config.orgId);
    const rolloverMaintenance = await runMaintenance(database);
    const cycleAfterRollover = await database.getCycleState(config.orgId);
    const budgetAfterRollover = await api.getBudget();
    requireSuccessfulSync(budgetAfterRollover, "stale-cycle rollover");
    const teamAfterRollover = await getLiteLLMTeamState(config);
    const memberAfterRollover = await api.getMemberFinancial(userId);
    const reportingSpendBefore = budgetAfterRollover.current_spend;
    const financialSpendBefore = memberAfterRollover.lifetime_spend;
    const conversationsBeforeDirectSpend = await api.getConversationCount();

    const generatedKey = await createLiteLLMTestKey(config, userId);
    testKeyAlias = generatedKey.key_alias;
    if (!testKeyAlias) throw new Error("LiteLLM test key alias is absent");

    await generateMinimumDirectSpend(
      api,
      userId,
      generatedKey.key,
      financialSpendBefore,
    );
    const firstSpendMaintenance = await runMaintenance(database);
    const cycleAfterFirstSpend = await database.getCycleState(config.orgId);
    const teamAfterFirstSpend = await getLiteLLMTeamState(config);
    const memberAfterFirstSpend = await api.getMemberFinancial(userId);

    await generateMinimumDirectSpend(
      api,
      userId,
      generatedKey.key,
      memberAfterFirstSpend.lifetime_spend,
    );
    const secondSpendMaintenance = await runMaintenance(database);
    const cycleAfterSecondSpend = await database.getCycleState(config.orgId);
    const teamAfterSecondSpend = await getLiteLLMTeamState(config);
    const memberAfterSecondSpend = await api.getMemberFinancial(userId);
    const reportingAfterDirect = await api.getBudget();
    requireSuccessfulSync(
      reportingAfterDirect,
      "second direct-spend maintenance",
    );
    const conversationsAfterDirectSpend = await api.getConversationCount();

    const thresholdStartedAt = Math.floor(Date.now() / 1000);
    const alertMonthlyLimit =
      reportingAfterDirect.current_spend + config.minimumSpendDelta / 2;
    const slackConfigured = await api.patchBudget({
      monthly_limit: alertMonthlyLimit,
      slack_channel: config.slack.channelName,
      slack_team_id: config.slack.teamId,
      thresholds: [
        { percentage: 100, email_enabled: false, slack_enabled: true },
      ],
    });
    requireSuccessfulSync(slackConfigured, "Slack budget configuration");
    const alertFinancialSpendBefore = memberAfterSecondSpend.lifetime_spend;
    await generateMinimumDirectSpend(
      api,
      userId,
      generatedKey.key,
      alertFinancialSpendBefore,
    );
    const alertMaintenance = await runMaintenance(database);
    const alertReporting = await api.getBudget();
    requireSuccessfulSync(
      alertReporting,
      "authoritative-spend alert maintenance",
    );
    const alertFinancial = await api.getMemberFinancial(userId);
    const alertTeam = await getLiteLLMTeamState(config);
    const alert = await pollUntil(
      () => findSlackBudgetAlert(config, org.name, thresholdStartedAt),
      (value) => value !== undefined,
      {
        description: "the budget threshold Slack notification",
        timeoutMs: 2 * 60_000,
        intervalMs: config.pollIntervalMs,
      },
    );
    if (!alert) throw new Error("Slack budget alert was not delivered");

    if (memberAfterRollover.max_budget === null) {
      throw new Error("Default member cap was absent after cycle rollover");
    }
    const memberCycleBaseline =
      memberAfterRollover.max_budget - config.userMonthlyLimit;
    const overrideLimit = Math.max(
      Math.min(config.userMonthlyLimit / 2, config.monthlyLimit / 2),
      0.01,
    );
    const overrideExpectedCap = memberCycleBaseline + overrideLimit;
    await api.putOverride(userId, {
      monthly_limit: overrideLimit,
      is_disabled: false,
    });
    const overrideExpectedMember = await pollUntil(
      () => api!.getMemberFinancial(userId),
      (member) =>
        member.max_budget !== null &&
        Math.abs(member.max_budget - overrideExpectedCap) < 0.005,
      {
        description: "the individual override cap to appear in LiteLLM",
        timeoutMs: 60_000,
        intervalMs: config.pollIntervalMs,
      },
    );
    await updateLiteLLMMemberCap(config, userId, null);
    const overrideClearedMember = await pollUntil(
      () => api!.getMemberFinancial(userId),
      (member) => member.max_budget === null,
      {
        description: "the individual override cap to be cleared directly",
        timeoutMs: 60_000,
        intervalMs: config.pollIntervalMs,
      },
    );
    const overrideMaintenance = await runMaintenance(database);
    const overrideReconciledMember = await api.getMemberFinancial(userId);

    const disabled = await api.patchBudget({ enabled: false });
    requireSuccessfulSync(disabled, "budget disable transition");
    const disabledSyncBefore = disabled.litellm_last_sync_at;
    const currentTeam = await getLiteLLMTeamState(config);
    const currentMember = await api.getMemberFinancial(userId);
    await updateLiteLLMTeamCap(config, currentTeam.spend + 3.21);
    await updateLiteLLMMemberCap(
      config,
      userId,
      currentMember.lifetime_spend + 1.23,
    );
    const seededDisabledTeam = await getLiteLLMTeamState(config);
    const seededDisabledMember = await api.getMemberFinancial(userId);
    const disabledMaintenance = await runMaintenance(database);
    const disabledAfterMaintenance = await api.getBudget();
    const observedDisabledTeam = await getLiteLLMTeamState(config);
    const observedDisabledMember = await api.getMemberFinancial(userId);

    evidence = {
      rolloverMaintenance,
      firstSpendMaintenance,
      secondSpendMaintenance,
      disabledMaintenance,
      cycleAfterRollover,
      cycleAfterFirstSpend,
      cycleAfterSecondSpend,
      teamAfterRollover,
      teamAfterFirstSpend,
      teamAfterSecondSpend,
      memberAfterRollover,
      memberAfterFirstSpend,
      memberAfterSecondSpend,
      reportingSpendBefore,
      reportingSpendAfter: reportingAfterDirect.current_spend,
      financialSpendBefore,
      financialSpendAfter: memberAfterSecondSpend.lifetime_spend,
      conversationsBeforeDirectSpend,
      conversationsAfterDirectSpend,
      alertReportingSpendBefore: reportingAfterDirect.current_spend,
      alertReportingSpendAfter: alertReporting.current_spend,
      alertFinancialSpendBefore,
      alertFinancialSpendAfter: alertFinancial.lifetime_spend,
      alertTeamSpendBefore: teamAfterSecondSpend.spend,
      alertTeamSpendAfter: alertTeam.spend,
      slackAlertSpend: alert.spend,
      slackAlertText: alert.text,
      overrideMaintenance,
      overrideLimit,
      overrideExpectedCap,
      overrideExpectedMember,
      overrideClearedMember,
      overrideReconciledMember,
      disabledSyncBefore,
      disabledSyncAfter: disabledAfterMaintenance.litellm_last_sync_at,
      disabledSyncStatus: disabledAfterMaintenance.litellm_last_sync_status,
      seededDisabledTeam,
      observedDisabledTeam,
      seededDisabledMember,
      observedDisabledMember,
    };

    await attachEvidence("maintenance-run-summary.json", {
      liteLLMVersion,
      rolloverMaintenance,
      firstSpendMaintenance,
      secondSpendMaintenance,
      alertMaintenance,
      overrideMaintenance,
      disabledMaintenance,
    });
  });

  test.afterAll(async ({ browser: _browser }, testInfo) => {
    testInfo.setTimeout(5 * 60_000);
    if (
      !api ||
      !database ||
      !context ||
      !originalBudget ||
      !originalCycle ||
      !originalTeam ||
      !originalMember
    ) {
      let cleanupError: unknown;
      try {
        await database?.cleanupCreatedTasks();
      } catch (error) {
        cleanupError = error;
      } finally {
        await context?.close();
      }
      if (cleanupError) throw cleanupError;
      return;
    }

    const cleanupErrors: string[] = [];
    const attempt = async (
      operation: string,
      cleanup: () => Promise<unknown>,
    ): Promise<void> => {
      try {
        await cleanup();
      } catch (error) {
        cleanupErrors.push(`${operation}: ${String(error)}`);
      }
    };

    if (testKeyAlias) {
      await attempt("delete temporary LiteLLM key", () =>
        deleteLiteLLMTestKey(config, testKeyAlias!),
      );
    }
    await attempt("restore member override", async () => {
      if (originalOverride) {
        await api!.putOverride(userId, {
          monthly_limit: originalOverride.monthly_limit,
          is_disabled: originalOverride.is_disabled,
        });
      } else {
        await api!.deleteOverride(userId);
      }
    });
    await attempt("restore budget settings", async () => {
      const restored = await api!.patchBudget(restorePayload(originalBudget!));
      requireSuccessfulSync(restored, "budget settings restore");
    });
    await attempt("restore budget cycle", () =>
      database!.restoreCycleState(config.orgId, originalCycle!),
    );
    await attempt("restore LiteLLM team cap", () =>
      updateLiteLLMTeamCap(config, originalTeam!.maxBudget),
    );
    await attempt("restore LiteLLM member cap", () =>
      updateLiteLLMMemberCap(config, userId, originalMember!.max_budget),
    );
    if (originalOrgId && originalOrgId !== config.orgId) {
      await attempt("restore current organization", () =>
        api!.switchOrg(originalOrgId!),
      );
    }
    await attempt("delete test maintenance tasks", () =>
      database!.cleanupCreatedTasks(),
    );
    await attempt("close browser context", () => context!.close());

    if (cleanupErrors.length > 0) {
      throw new Error(`Budget E2E cleanup failed: ${cleanupErrors.join("; ")}`);
    }
  });

  test("issue 1: stale rollover persists and organization cap stays fixed", async () => {
    expect(evidence).toBeDefined();
    await attachEvidence("issue-1-organization-cap.json", {
      maintenance: [
        evidence!.rolloverMaintenance,
        evidence!.firstSpendMaintenance,
        evidence!.secondSpendMaintenance,
      ],
      cycles: [
        evidence!.cycleAfterRollover,
        evidence!.cycleAfterFirstSpend,
        evidence!.cycleAfterSecondSpend,
      ],
      teams: [
        evidence!.teamAfterRollover,
        evidence!.teamAfterFirstSpend,
        evidence!.teamAfterSecondSpend,
      ],
    });
    expect(evidence!.cycleAfterFirstSpend).toEqual(
      evidence!.cycleAfterRollover,
    );
    expect(evidence!.cycleAfterSecondSpend).toEqual(
      evidence!.cycleAfterRollover,
    );
    expect(evidence!.teamAfterFirstSpend.maxBudget).toBeCloseTo(
      evidence!.teamAfterRollover.maxBudget!,
      closeToPrecision,
    );
    expect(evidence!.teamAfterSecondSpend.maxBudget).toBeCloseTo(
      evidence!.teamAfterRollover.maxBudget!,
      closeToPrecision,
    );
  });

  test("issue 2: member cap does not renew after repeated synchronization", async () => {
    expect(evidence).toBeDefined();
    await attachEvidence("issue-2-member-cap.json", {
      afterRollover: evidence!.memberAfterRollover,
      afterFirstSpend: evidence!.memberAfterFirstSpend,
      afterSecondSpend: evidence!.memberAfterSecondSpend,
    });
    expect(
      evidence!.memberAfterFirstSpend.lifetime_spend -
        evidence!.memberAfterRollover.lifetime_spend,
    ).toBeGreaterThanOrEqual(config.minimumSpendDelta);
    expect(
      evidence!.memberAfterSecondSpend.lifetime_spend -
        evidence!.memberAfterFirstSpend.lifetime_spend,
    ).toBeGreaterThanOrEqual(config.minimumSpendDelta);
    expect(evidence!.memberAfterFirstSpend.max_budget).toBeCloseTo(
      evidence!.memberAfterRollover.max_budget!,
      closeToPrecision,
    );
    expect(evidence!.memberAfterSecondSpend.max_budget).toBeCloseTo(
      evidence!.memberAfterRollover.max_budget!,
      closeToPrecision,
    );
  });

  test("issue 6: existing override is reconciled to a stable member cap", async () => {
    expect(evidence).toBeDefined();
    await attachEvidence("issue-6-override-reconciliation.json", {
      maintenance: evidence!.overrideMaintenance,
      overrideLimit: evidence!.overrideLimit,
      expectedCap: evidence!.overrideExpectedCap,
      initial: evidence!.overrideExpectedMember,
      cleared: evidence!.overrideClearedMember,
      reconciled: evidence!.overrideReconciledMember,
    });
    expect(evidence!.overrideMaintenance.status).toBe("COMPLETED");
    expect(evidence!.overrideExpectedMember.max_budget).toBeCloseTo(
      evidence!.overrideExpectedCap,
      closeToPrecision,
    );
    expect(evidence!.overrideClearedMember.max_budget).toBeNull();
    expect(evidence!.overrideReconciledMember.max_budget).toBeCloseTo(
      evidence!.overrideExpectedCap,
      closeToPrecision,
    );
  });

  test("issue 3: completed disabled maintenance preserves manual caps", async () => {
    expect(evidence).toBeDefined();
    await attachEvidence("issue-3-disabled-budget.json", {
      maintenance: evidence!.disabledMaintenance,
      syncBefore: evidence!.disabledSyncBefore,
      syncAfter: evidence!.disabledSyncAfter,
      syncStatus: evidence!.disabledSyncStatus,
      teamSeeded: evidence!.seededDisabledTeam,
      teamObserved: evidence!.observedDisabledTeam,
      memberSeeded: evidence!.seededDisabledMember,
      memberObserved: evidence!.observedDisabledMember,
    });
    expect(evidence!.disabledMaintenance.status).toBe("COMPLETED");
    expect(evidence!.disabledSyncStatus).toBe("skipped");
    expect(new Date(evidence!.disabledSyncAfter!).getTime()).toBeGreaterThan(
      new Date(evidence!.disabledSyncBefore!).getTime(),
    );
    expect(evidence!.observedDisabledTeam.maxBudget).toBeCloseTo(
      evidence!.seededDisabledTeam.maxBudget!,
      closeToPrecision,
    );
    expect(evidence!.observedDisabledMember.max_budget).toBeCloseTo(
      evidence!.seededDisabledMember.max_budget!,
      closeToPrecision,
    );
  });

  test("issue 4: LiteLLM-only spend appears without a conversation", async () => {
    expect(evidence).toBeDefined();
    const reportingDelta =
      evidence!.reportingSpendAfter - evidence!.reportingSpendBefore;
    const financialDelta =
      evidence!.financialSpendAfter - evidence!.financialSpendBefore;
    const teamDelta =
      evidence!.teamAfterSecondSpend.spend - evidence!.teamAfterRollover.spend;
    await attachEvidence("issue-4-authoritative-spend.json", {
      reportingBefore: evidence!.reportingSpendBefore,
      reportingAfter: evidence!.reportingSpendAfter,
      reportingDelta,
      financialBefore: evidence!.financialSpendBefore,
      financialAfter: evidence!.financialSpendAfter,
      financialDelta,
      teamDelta,
      conversationsBefore: evidence!.conversationsBeforeDirectSpend,
      conversationsAfter: evidence!.conversationsAfterDirectSpend,
    });
    expect(evidence!.conversationsAfterDirectSpend).toBe(
      evidence!.conversationsBeforeDirectSpend,
    );
    expect(financialDelta).toBeGreaterThanOrEqual(config.minimumSpendDelta * 2);
    expect(teamDelta).toBeCloseTo(financialDelta, closeToPrecision);
    expect(reportingDelta).toBeCloseTo(teamDelta, closeToPrecision);
  });

  test("issue 5: LiteLLM-only threshold crossing emits authoritative Slack spend", async () => {
    expect(evidence).toBeDefined();
    const reportingDelta =
      evidence!.alertReportingSpendAfter - evidence!.alertReportingSpendBefore;
    const financialDelta =
      evidence!.alertFinancialSpendAfter - evidence!.alertFinancialSpendBefore;
    const teamDelta =
      evidence!.alertTeamSpendAfter - evidence!.alertTeamSpendBefore;
    await attachEvidence("issue-5-slack-alert.json", {
      alertText: evidence!.slackAlertText,
      alertSpend: evidence!.slackAlertSpend,
      reportingBefore: evidence!.alertReportingSpendBefore,
      reportingAfter: evidence!.alertReportingSpendAfter,
      reportingDelta,
      financialDelta,
      teamDelta,
    });
    expect(financialDelta).toBeGreaterThanOrEqual(config.minimumSpendDelta);
    expect(teamDelta).toBeCloseTo(financialDelta, closeToPrecision);
    expect(reportingDelta).toBeCloseTo(teamDelta, closeToPrecision);
    expect(evidence!.slackAlertSpend).toBeCloseTo(
      evidence!.alertReportingSpendAfter,
      closeToPrecision,
    );
  });
});
