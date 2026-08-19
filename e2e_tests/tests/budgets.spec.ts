import { test, expect, type BrowserContext, type Page } from "@playwright/test";

import { ConversationPage, HomePage } from "../pages";
import {
  BudgetApi,
  type BudgetSettings,
  type BudgetUser,
  type LiteLLMTeamState,
  type MemberFinancial,
  findSlackBudgetAlert,
  getLiteLLMTeamState,
  loadBudgetE2EConfig,
  pollUntil,
  updateLiteLLMMemberCap,
  updateLiteLLMTeamCap,
} from "../utils/budgets";
import { authReturningFile, env, runUser } from "../utils/config";

interface BudgetEvidence {
  teamBeforeMaintenance: LiteLLMTeamState;
  teamAfterMaintenance: LiteLLMTeamState;
  memberBeforeMaintenance: MemberFinancial;
  memberAfterMaintenance: MemberFinancial;
  metadataSpendBefore: number;
  metadataSpendAfter: number;
  financialSpendBefore: number;
  financialSpendAfter: number;
  disabledSyncBefore: string | null;
  disabledSyncAfter: string | null;
  seededDisabledTeam: LiteLLMTeamState;
  observedDisabledTeam: LiteLLMTeamState;
  seededDisabledMember: MemberFinancial;
  observedDisabledMember: MemberFinancial;
  slackAlertSpend?: number;
  slackAlertText?: string;
}

const config = loadBudgetE2EConfig();
const maintenanceCycles = config.slack ? 2 : 1;
const suiteTimeout =
  config.syncTimeoutMs * maintenanceCycles +
  config.disabledObservationMs +
  10 * 60_000;
const closeToPrecision = 2;

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

async function generateSpend(page: Page, prompt: string): Promise<void> {
  const homePage = new HomePage(page);
  await homePage.goto();
  await homePage.startNewConversation("launch-new-conversation-button");
  const conversationPage = new ConversationPage(page);
  await conversationPage.waitForConversationReady();
  await conversationPage.executePrompt(prompt, 180_000);
}

test.describe("organization budget maintenance @budgets", () => {
  test.describe.configure({ mode: "serial" });

  let context: BrowserContext | undefined;
  let api: BudgetApi | undefined;
  let page: Page | undefined;
  let userId = "";
  let originalOrgId: string | null = null;
  let originalBudget: BudgetSettings | undefined;
  let originalOverride: BudgetUser | undefined;
  let originalTeam: LiteLLMTeamState | undefined;
  let originalMember: MemberFinancial | undefined;
  let evidence: BudgetEvidence | undefined;

  test.beforeEach(({ browser: _browser }, testInfo) => {
    test.skip(
      runUser(testInfo) !== "returning",
      "budget maintenance runs once with the configured returning admin",
    );
    test.skip(
      !config.enabled,
      "set BUDGET_E2E_ORG_ID and BUDGET_E2E_MUTATION_CONFIRMED=true for a dedicated non-personal test org",
    );
  });

  test.beforeAll(async ({ browser }, testInfo) => {
    testInfo.setTimeout(suiteTimeout);
    const role = testInfo.project.metadata.user;
    if (role !== "returning" || !config.enabled) return;
    if (!config.litellmUrl || !config.litellmApiKey) {
      throw new Error(
        "BUDGET_E2E_LITELLM_URL and BUDGET_E2E_LITELLM_API_KEY are required",
      );
    }

    context = await browser.newContext({
      baseURL: env.baseUrl,
      storageState: authReturningFile,
      ignoreHTTPSErrors: true,
    });
    page = await context.newPage();
    api = new BudgetApi(context.request, config.orgId);

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
    const me = await api.getMe();
    userId = me.user_id;
    originalBudget = await api.getBudget();
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
    const governedBudget = await api.getBudget();
    requireSuccessfulSync(governedBudget, "member override removal");
    const syncBeforeSpend = governedBudget.litellm_last_sync_at;
    if (!syncBeforeSpend) {
      throw new Error("Budget update did not record a LiteLLM sync timestamp");
    }

    const metadataSpendBefore = governedBudget.current_spend;
    const memberBeforeSpend = await api.getMemberFinancial(userId);
    const financialSpendBefore = memberBeforeSpend.lifetime_spend;
    const teamBeforeSpend = await getLiteLLMTeamState(config);

    await generateSpend(page, config.prompt);

    const metadataAfterSpend = await pollUntil(
      () => api!.getBudget(),
      (budget) => budget.current_spend > metadataSpendBefore,
      {
        description: "conversation spend to reach the budget reporting source",
        timeoutMs: 5 * 60_000,
        intervalMs: config.pollIntervalMs,
      },
    );
    const memberAfterSpend = await pollUntil(
      () => api!.getMemberFinancial(userId),
      (member) => member.lifetime_spend > financialSpendBefore,
      {
        description:
          "conversation spend to reach LiteLLM member financial data",
        timeoutMs: 5 * 60_000,
        intervalMs: config.pollIntervalMs,
      },
    );

    const teamBeforeMaintenance = await getLiteLLMTeamState(config);
    const memberBeforeMaintenance = await api.getMemberFinancial(userId);
    const postMaintenanceBudget = await pollUntil(
      () => api!.getBudget(),
      (budget) =>
        Boolean(
          budget.litellm_last_sync_at &&
          new Date(budget.litellm_last_sync_at).getTime() >
            new Date(syncBeforeSpend).getTime(),
        ),
      {
        description: "the next persisted budget maintenance synchronization",
        timeoutMs: config.syncTimeoutMs,
        intervalMs: config.pollIntervalMs,
      },
    );
    requireSuccessfulSync(postMaintenanceBudget, "periodic budget maintenance");
    const teamAfterMaintenance = await getLiteLLMTeamState(config);
    const memberAfterMaintenance = await api.getMemberFinancial(userId);

    let slackAlertSpend: number | undefined;
    let slackAlertText: string | undefined;
    if (config.slack) {
      const thresholdStartedAt = Math.floor(Date.now() / 1000);
      const alertMonthlyLimit = Math.max(
        metadataAfterSpend.current_spend * 50,
        0.01,
      );
      const slackConfigured = await api.patchBudget({
        monthly_limit: alertMonthlyLimit,
        slack_channel: config.slack.channelName,
        slack_team_id: config.slack.teamId,
        thresholds: [
          { percentage: 1, email_enabled: false, slack_enabled: true },
        ],
      });
      requireSuccessfulSync(slackConfigured, "Slack budget configuration");
      const slackSyncAt = slackConfigured.litellm_last_sync_at;
      if (!slackSyncAt) {
        throw new Error("Slack configuration did not record a sync timestamp");
      }
      const slackMaintenance = await pollUntil(
        () => api!.getBudget(),
        (budget) =>
          Boolean(
            budget.litellm_last_sync_at &&
            new Date(budget.litellm_last_sync_at).getTime() >
              new Date(slackSyncAt).getTime(),
          ),
        {
          description: "the maintenance cycle that emits the Slack alert",
          timeoutMs: config.syncTimeoutMs,
          intervalMs: config.pollIntervalMs,
        },
      );
      requireSuccessfulSync(slackMaintenance, "Slack alert maintenance");
      const alert = await pollUntil(
        () => findSlackBudgetAlert(config, org.name, thresholdStartedAt),
        (value) => value !== undefined,
        {
          description: "the budget threshold Slack notification",
          timeoutMs: 2 * 60_000,
          intervalMs: config.pollIntervalMs,
        },
      );
      slackAlertSpend = alert?.spend;
      slackAlertText = alert?.text;
    }

    const disabled = await api.patchBudget({ enabled: false });
    requireSuccessfulSync(disabled, "budget disable");
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

    await new Promise((resolve) =>
      setTimeout(resolve, config.disabledObservationMs),
    );

    const disabledAfterObservation = await api.getBudget();
    const observedDisabledTeam = await getLiteLLMTeamState(config);
    const observedDisabledMember = await api.getMemberFinancial(userId);

    evidence = {
      teamBeforeMaintenance,
      teamAfterMaintenance,
      memberBeforeMaintenance,
      memberAfterMaintenance,
      metadataSpendBefore,
      metadataSpendAfter: metadataAfterSpend.current_spend,
      financialSpendBefore,
      financialSpendAfter: memberAfterSpend.lifetime_spend,
      disabledSyncBefore,
      disabledSyncAfter: disabledAfterObservation.litellm_last_sync_at,
      seededDisabledTeam,
      observedDisabledTeam,
      seededDisabledMember,
      observedDisabledMember,
      slackAlertSpend,
      slackAlertText,
    };

    console.log(
      `[budgets] maintenance sync ${syncBeforeSpend} -> ${postMaintenanceBudget.litellm_last_sync_at}; ` +
        `team cap ${teamBeforeSpend.maxBudget} -> ${teamBeforeMaintenance.maxBudget} -> ${teamAfterMaintenance.maxBudget}; ` +
        `configured at ${configuredBudget.litellm_last_sync_at}`,
    );
  });

  test.afterAll(async ({ browser: _browser }, testInfo) => {
    testInfo.setTimeout(5 * 60_000);
    if (
      !api ||
      !context ||
      !originalBudget ||
      !originalTeam ||
      !originalMember
    ) {
      await context?.close();
      return;
    }

    const budgetSnapshot = originalBudget;
    const teamSnapshot = originalTeam;
    const memberSnapshot = originalMember;

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
      const restored = await api!.patchBudget(restorePayload(budgetSnapshot));
      requireSuccessfulSync(restored, "budget settings restore");
    });
    await attempt("restore LiteLLM team cap", () =>
      updateLiteLLMTeamCap(config, teamSnapshot.maxBudget),
    );
    await attempt("restore LiteLLM member cap", () =>
      updateLiteLLMMemberCap(config, userId, memberSnapshot.max_budget),
    );
    if (originalOrgId && originalOrgId !== config.orgId) {
      await attempt("restore current organization", () =>
        api!.switchOrg(originalOrgId!),
      );
    }
    await attempt("close browser context", () => context!.close());

    if (cleanupErrors.length > 0) {
      throw new Error(`Budget E2E cleanup failed: ${cleanupErrors.join("; ")}`);
    }
  });

  test("issue 1: organization cap stays fixed after periodic maintenance", async () => {
    expect(evidence).toBeDefined();
    await attachEvidence("issue-1-organization-cap.json", {
      before: evidence!.teamBeforeMaintenance,
      after: evidence!.teamAfterMaintenance,
    });
    expect(evidence!.teamAfterMaintenance.maxBudget).toBeCloseTo(
      evidence!.teamBeforeMaintenance.maxBudget!,
      closeToPrecision,
    );
  });

  test("issue 2: member cap does not renew the allowance on synchronization", async () => {
    expect(evidence).toBeDefined();
    await attachEvidence("issue-2-member-cap.json", {
      before: evidence!.memberBeforeMaintenance,
      after: evidence!.memberAfterMaintenance,
    });
    expect(evidence!.memberAfterMaintenance.max_budget).toBeCloseTo(
      evidence!.memberBeforeMaintenance.max_budget!,
      closeToPrecision,
    );
  });

  test("issue 3: disabled budgets are not mutated by periodic maintenance", async () => {
    expect(evidence).toBeDefined();
    await attachEvidence("issue-3-disabled-budget.json", {
      syncBefore: evidence!.disabledSyncBefore,
      syncAfter: evidence!.disabledSyncAfter,
      teamSeeded: evidence!.seededDisabledTeam,
      teamObserved: evidence!.observedDisabledTeam,
      memberSeeded: evidence!.seededDisabledMember,
      memberObserved: evidence!.observedDisabledMember,
    });
    expect(evidence!.disabledSyncAfter).toBe(evidence!.disabledSyncBefore);
    expect(evidence!.observedDisabledTeam.maxBudget).toBeCloseTo(
      evidence!.seededDisabledTeam.maxBudget!,
      closeToPrecision,
    );
    expect(evidence!.observedDisabledMember.max_budget).toBeCloseTo(
      evidence!.seededDisabledMember.max_budget!,
      closeToPrecision,
    );
  });

  test("issue 4: reporting spend matches LiteLLM spend for the same work", async () => {
    expect(evidence).toBeDefined();
    const metadataDelta =
      evidence!.metadataSpendAfter - evidence!.metadataSpendBefore;
    const financialDelta =
      evidence!.financialSpendAfter - evidence!.financialSpendBefore;
    await attachEvidence("issue-4-spend-sources.json", {
      metadataBefore: evidence!.metadataSpendBefore,
      metadataAfter: evidence!.metadataSpendAfter,
      metadataDelta,
      financialBefore: evidence!.financialSpendBefore,
      financialAfter: evidence!.financialSpendAfter,
      financialDelta,
    });
    expect(metadataDelta).toBeGreaterThan(0);
    expect(financialDelta).toBeGreaterThan(0);
    expect(metadataDelta).toBeCloseTo(financialDelta, closeToPrecision);
  });

  test("issue 5: Slack threshold alert uses the reporting spend source", async () => {
    test.skip(
      !config.slack,
      "set all BUDGET_E2E_SLACK_* variables to verify delivered alert content",
    );
    expect(evidence?.slackAlertSpend).toBeDefined();
    await attachEvidence("issue-5-slack-alert.json", {
      alertText: evidence!.slackAlertText,
      alertSpend: evidence!.slackAlertSpend,
      reportingSpend: evidence!.metadataSpendAfter,
    });
    expect(evidence!.slackAlertSpend).toBeCloseTo(
      evidence!.metadataSpendAfter,
      closeToPrecision,
    );
  });
});
