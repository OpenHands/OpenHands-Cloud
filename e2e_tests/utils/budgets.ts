import type { APIRequestContext, APIResponse } from "@playwright/test";

export interface BudgetThreshold {
  percentage: number;
  email_enabled: boolean;
  slack_enabled: boolean;
}

export interface BudgetUser {
  user_id: string;
  current_spend: number;
  monthly_limit: number | null;
  effective_monthly_limit: number | null;
  is_disabled: boolean;
  is_override: boolean;
}

export interface BudgetSettings {
  enabled: boolean;
  monthly_limit: number | null;
  reset_day: number;
  default_user_monthly_limit: number | null;
  slack_channel: string | null;
  slack_team_id: string | null;
  litellm_last_sync_at: string | null;
  litellm_last_sync_status: string | null;
  litellm_last_sync_error: string | null;
  current_spend: number;
  current_spend_percentage: number;
  thresholds: BudgetThreshold[];
  users: BudgetUser[];
}

export interface OrgDetails {
  id: string;
  name: string;
  credits: number | null;
  is_personal: boolean;
}

export interface OrgPage {
  items: OrgDetails[];
  current_org_id: string | null;
}

export interface OrgMember {
  user_id: string;
  email: string;
  role: string;
}

export interface MemberFinancial {
  user_id: string;
  lifetime_spend: number;
  current_budget: number;
  max_budget: number | null;
}

interface MemberFinancialPage {
  items: MemberFinancial[];
}

export interface LiteLLMTeamState {
  maxBudget: number | null;
  spend: number;
}

export interface SlackAlert {
  text: string;
  spend: number;
}

interface SlackConfig {
  botToken: string;
  channelId: string;
  channelName: string;
  teamId: string;
}

export interface BudgetE2EConfig {
  enabled: boolean;
  orgId: string;
  monthlyLimit: number;
  userMonthlyLimit: number;
  prompt: string;
  pollIntervalMs: number;
  syncTimeoutMs: number;
  disabledObservationMs: number;
  litellmUrl?: string;
  litellmApiKey?: string;
  slack?: SlackConfig;
}

const readPositiveNumber = (name: string, defaultValue: number): number => {
  const raw = process.env[name]?.trim();
  if (!raw) return defaultValue;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive number`);
  }
  return parsed;
};

export function loadBudgetE2EConfig(): BudgetE2EConfig {
  const orgId = process.env.BUDGET_E2E_ORG_ID?.trim() || "";
  const litellmUrl = process.env.BUDGET_E2E_LITELLM_URL?.trim();
  const litellmApiKey = process.env.BUDGET_E2E_LITELLM_API_KEY?.trim();
  const slackValues = {
    botToken: process.env.BUDGET_E2E_SLACK_BOT_TOKEN?.trim(),
    channelId: process.env.BUDGET_E2E_SLACK_CHANNEL_ID?.trim(),
    channelName: process.env.BUDGET_E2E_SLACK_CHANNEL_NAME?.trim(),
    teamId: process.env.BUDGET_E2E_SLACK_TEAM_ID?.trim(),
  };
  const hasAnySlackValue = Object.values(slackValues).some(Boolean);
  const hasAllSlackValues = Object.values(slackValues).every(Boolean);
  if (hasAnySlackValue && !hasAllSlackValues) {
    throw new Error(
      "All BUDGET_E2E_SLACK_* variables are required when Slack verification is enabled",
    );
  }

  return {
    enabled:
      Boolean(orgId) &&
      process.env.BUDGET_E2E_MUTATION_CONFIRMED?.toLowerCase() === "true",
    orgId,
    monthlyLimit: readPositiveNumber("BUDGET_E2E_MONTHLY_LIMIT", 50),
    userMonthlyLimit: readPositiveNumber("BUDGET_E2E_USER_MONTHLY_LIMIT", 25),
    prompt:
      process.env.BUDGET_E2E_PROMPT?.trim() ||
      "Reply with exactly: budget-e2e-ok",
    pollIntervalMs: readPositiveNumber("BUDGET_E2E_POLL_INTERVAL_MS", 5_000),
    syncTimeoutMs: readPositiveNumber(
      "BUDGET_E2E_SYNC_TIMEOUT_MS",
      20 * 60_000,
    ),
    disabledObservationMs: readPositiveNumber(
      "BUDGET_E2E_DISABLED_OBSERVATION_MS",
      18 * 60_000,
    ),
    litellmUrl,
    litellmApiKey,
    slack: hasAllSlackValues
      ? {
          botToken: slackValues.botToken!,
          channelId: slackValues.channelId!,
          channelName: slackValues.channelName!,
          teamId: slackValues.teamId!,
        }
      : undefined,
  };
}

async function responseJson<T>(
  responsePromise: Promise<APIResponse>,
  operation: string,
): Promise<T> {
  const response = await responsePromise;
  if (!response.ok()) {
    throw new Error(
      `${operation} failed (${response.status()} ${response.statusText()}): ${await response.text()}`,
    );
  }
  return (await response.json()) as T;
}

export class BudgetApi {
  constructor(
    private readonly request: APIRequestContext,
    private readonly orgId: string,
  ) {}

  private get headers(): Record<string, string> {
    return { "X-Org-Id": this.orgId };
  }

  getOrganizations(): Promise<OrgPage> {
    return responseJson<OrgPage>(
      this.request.get("/api/organizations"),
      "list organizations",
    );
  }

  getOrg(): Promise<OrgDetails> {
    return responseJson<OrgDetails>(
      this.request.get(`/api/organizations/${this.orgId}`, {
        headers: this.headers,
      }),
      "get organization details",
    );
  }

  getMe(): Promise<OrgMember> {
    return responseJson<OrgMember>(
      this.request.get(`/api/organizations/${this.orgId}/me`, {
        headers: this.headers,
      }),
      "get organization member",
    );
  }

  switchOrg(orgId: string): Promise<OrgDetails> {
    return responseJson<OrgDetails>(
      this.request.post(`/api/organizations/${orgId}/switch`),
      "switch organization",
    );
  }

  getBudget(): Promise<BudgetSettings> {
    return responseJson<BudgetSettings>(
      this.request.get(`/api/organizations/${this.orgId}/budgets`, {
        headers: this.headers,
      }),
      "get budget settings",
    );
  }

  patchBudget(update: Record<string, unknown>): Promise<BudgetSettings> {
    return responseJson<BudgetSettings>(
      this.request.patch(`/api/organizations/${this.orgId}/budgets`, {
        headers: this.headers,
        data: update,
      }),
      "update budget settings",
    );
  }

  async getMemberFinancial(userId: string): Promise<MemberFinancial> {
    const page = await responseJson<MemberFinancialPage>(
      this.request.get(
        `/api/organizations/${this.orgId}/members/financial?limit=100`,
        { headers: this.headers },
      ),
      "get member financial data",
    );
    const member = page.items.find((item) => item.user_id === userId);
    if (!member) {
      throw new Error(`Member ${userId} was absent from financial data`);
    }
    return member;
  }

  putOverride(
    userId: string,
    update: { monthly_limit: number | null; is_disabled: boolean },
  ): Promise<BudgetUser> {
    return responseJson<BudgetUser>(
      this.request.put(
        `/api/organizations/${this.orgId}/budgets/overrides/${userId}`,
        { headers: this.headers, data: update },
      ),
      "update member budget override",
    );
  }

  async deleteOverride(userId: string): Promise<void> {
    const response = await this.request.delete(
      `/api/organizations/${this.orgId}/budgets/overrides/${userId}`,
      { headers: this.headers },
    );
    if (!response.ok() && response.status() !== 404) {
      throw new Error(
        `delete member budget override failed (${response.status()} ${response.statusText()}): ${await response.text()}`,
      );
    }
  }
}

export async function pollUntil<T>(
  read: () => Promise<T>,
  predicate: (value: T) => boolean,
  options: {
    description: string;
    timeoutMs: number;
    intervalMs: number;
  },
): Promise<T> {
  const deadline = Date.now() + options.timeoutMs;
  let lastValue: T | undefined;
  while (Date.now() < deadline) {
    lastValue = await read();
    if (predicate(lastValue)) return lastValue;
    await new Promise((resolve) => setTimeout(resolve, options.intervalMs));
  }
  throw new Error(
    `Timed out after ${options.timeoutMs}ms waiting for ${options.description}; last value: ${JSON.stringify(lastValue)}`,
  );
}

export async function getLiteLLMTeamState(
  config: BudgetE2EConfig,
): Promise<LiteLLMTeamState> {
  if (!config.litellmUrl || !config.litellmApiKey) {
    throw new Error("LiteLLM E2E admin configuration is not available");
  }
  const url = new URL("team/info", `${config.litellmUrl.replace(/\/$/, "")}/`);
  url.searchParams.set("team_id", config.orgId);
  const response = await fetch(url, {
    headers: { "x-goog-api-key": config.litellmApiKey },
  });
  if (!response.ok) {
    throw new Error(
      `get LiteLLM team failed (${response.status} ${response.statusText})`,
    );
  }
  const body = (await response.json()) as {
    team_info?: { max_budget?: number | null; spend?: number | null };
  };
  return {
    maxBudget: body.team_info?.max_budget ?? null,
    spend: body.team_info?.spend ?? 0,
  };
}

async function updateLiteLLM(
  config: BudgetE2EConfig,
  path: string,
  data: Record<string, unknown>,
): Promise<void> {
  if (!config.litellmUrl || !config.litellmApiKey) {
    throw new Error("LiteLLM E2E admin configuration is not available");
  }
  const response = await fetch(
    new URL(path, `${config.litellmUrl.replace(/\/$/, "")}/`),
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-goog-api-key": config.litellmApiKey,
      },
      body: JSON.stringify(data),
    },
  );
  if (!response.ok) {
    throw new Error(
      `update LiteLLM ${path} failed (${response.status} ${response.statusText})`,
    );
  }
}

export function updateLiteLLMTeamCap(
  config: BudgetE2EConfig,
  maxBudget: number | null,
): Promise<void> {
  return updateLiteLLM(config, "team/update", {
    team_id: config.orgId,
    max_budget: maxBudget,
  });
}

export function updateLiteLLMMemberCap(
  config: BudgetE2EConfig,
  userId: string,
  maxBudget: number | null,
): Promise<void> {
  return updateLiteLLM(config, "team/member_update", {
    team_id: config.orgId,
    user_id: userId,
    max_budget_in_team: maxBudget,
  });
}

export async function findSlackBudgetAlert(
  config: BudgetE2EConfig,
  orgName: string,
  oldestEpochSeconds: number,
): Promise<SlackAlert | undefined> {
  if (!config.slack) return undefined;
  const url = new URL("https://slack.com/api/conversations.history");
  url.searchParams.set("channel", config.slack.channelId);
  url.searchParams.set("oldest", String(oldestEpochSeconds));
  url.searchParams.set("limit", "100");
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${config.slack.botToken}` },
  });
  if (!response.ok) {
    throw new Error(
      `Slack history failed (${response.status} ${response.statusText})`,
    );
  }
  const body = (await response.json()) as {
    ok: boolean;
    error?: string;
    messages?: { text?: string }[];
  };
  if (!body.ok) {
    throw new Error(`Slack history failed: ${body.error || "unknown error"}`);
  }
  for (const message of body.messages || []) {
    const text = message.text || "";
    if (text.includes("OpenHands budget alert") && text.includes(orgName)) {
      const match = text.match(/Current spend: \*\$([\d,]+(?:\.\d+)?)\*/);
      if (match) {
        return { text, spend: Number(match[1].replace(/,/g, "")) };
      }
    }
  }
  return undefined;
}
