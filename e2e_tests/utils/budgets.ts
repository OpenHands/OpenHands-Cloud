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
  cycle_start_at: string;
  cycle_end_at: string;
  spend_status: "live" | "stale" | "unavailable";
  spend_observed_at: string | null;
  current_spend: number | null;
  current_spend_percentage: number | null;
  unmapped_spend: number | null;
  unmapped_member_count: number | null;
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

export interface LiteLLMMemberState {
  userId: string;
  maxBudget: number | null;
  spend: number;
}

export interface LiteLLMCompletionResult {
  status: number;
  body: string;
}

interface OrgConversationPage {
  total_items: number;
}

interface LiteLLMGeneratedKey {
  key: string;
  key_alias?: string;
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
  minimumSpendDelta: number;
  directPrompt: string;
  directModel?: string;
  expectedLiteLLMVersion: string;
  pollIntervalMs: number;
  syncTimeoutMs: number;
  databaseUrl?: string;
  litellmUrl?: string;
  litellmApiKey?: string;
  serviceUserId?: string;
  serviceApiKey?: string;
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
  const databaseUrl = process.env.BUDGET_E2E_DATABASE_URL?.trim();
  const litellmUrl = process.env.BUDGET_E2E_LITELLM_URL?.trim();
  const litellmApiKey = process.env.BUDGET_E2E_LITELLM_API_KEY?.trim();
  const serviceUserId = process.env.BUDGET_E2E_SERVICE_USER_ID?.trim();
  const serviceApiKey = process.env.BUDGET_E2E_SERVICE_API_KEY?.trim();
  const directModel = process.env.BUDGET_E2E_DIRECT_MODEL?.trim();
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
  const enabled =
    Boolean(orgId) &&
    process.env.BUDGET_E2E_MUTATION_CONFIRMED?.toLowerCase() === "true";
  if (enabled && !databaseUrl) {
    throw new Error(
      "BUDGET_E2E_DATABASE_URL is required for maintenance fixtures",
    );
  }
  if (enabled && !directModel) {
    throw new Error(
      "BUDGET_E2E_DIRECT_MODEL is required for direct LiteLLM traffic",
    );
  }
  if (enabled && (!serviceUserId || !serviceApiKey)) {
    throw new Error(
      "BUDGET_E2E_SERVICE_USER_ID and BUDGET_E2E_SERVICE_API_KEY are required for unmapped SDK verification",
    );
  }

  return {
    enabled,
    orgId,
    monthlyLimit: readPositiveNumber("BUDGET_E2E_MONTHLY_LIMIT", 50),
    userMonthlyLimit: readPositiveNumber("BUDGET_E2E_USER_MONTHLY_LIMIT", 25),
    minimumSpendDelta: readPositiveNumber(
      "BUDGET_E2E_MINIMUM_SPEND_DELTA",
      0.02,
    ),
    directPrompt:
      process.env.BUDGET_E2E_DIRECT_PROMPT?.trim() ||
      "Write a detailed 500-word explanation of idempotent billing synchronization.",
    directModel,
    expectedLiteLLMVersion:
      process.env.BUDGET_E2E_EXPECTED_LITELLM_VERSION?.trim() || "1.94.1",
    pollIntervalMs: readPositiveNumber("BUDGET_E2E_POLL_INTERVAL_MS", 5_000),
    syncTimeoutMs: readPositiveNumber(
      "BUDGET_E2E_SYNC_TIMEOUT_MS",
      20 * 60_000,
    ),
    databaseUrl,
    litellmUrl,
    litellmApiKey,
    serviceUserId,
    serviceApiKey,
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

function requiredNonNegativeNumber(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new Error(`${field} must be a finite non-negative number`);
  }
  return value;
}

function nullableNonNegativeNumber(
  value: unknown,
  field: string,
): number | null {
  if (value === null || value === undefined) return null;
  return requiredNonNegativeNumber(value, field);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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

  async getConversationCount(): Promise<number> {
    const page = await responseJson<OrgConversationPage>(
      this.request.get(
        `/api/organizations/${this.orgId}/conversations?page=1&per_page=1`,
        { headers: this.headers },
      ),
      "get organization conversation count",
    );
    return page.total_items;
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

export async function getLiteLLMVersion(
  config: BudgetE2EConfig,
): Promise<string> {
  if (!config.litellmUrl || !config.litellmApiKey) {
    throw new Error("LiteLLM E2E admin configuration is not available");
  }
  const url = new URL(
    "health/readiness/details",
    `${config.litellmUrl.replace(/\/$/, "")}/`,
  );
  const response = await fetch(url, {
    headers: { "x-goog-api-key": config.litellmApiKey },
  });
  if (!response.ok) {
    throw new Error(
      `get LiteLLM readiness details failed (${response.status} ${response.statusText})`,
    );
  }
  const details = (await response.json()) as { litellm_version?: string };
  if (!details.litellm_version) {
    throw new Error(
      "LiteLLM readiness details did not include litellm_version",
    );
  }
  return details.litellm_version;
}

async function getLiteLLMTeam(
  config: BudgetE2EConfig,
): Promise<Record<string, unknown>> {
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
  const body: unknown = await response.json();
  if (!isRecord(body)) {
    throw new Error("LiteLLM team response must be an object");
  }
  return body;
}

export async function getLiteLLMTeamState(
  config: BudgetE2EConfig,
): Promise<LiteLLMTeamState> {
  const body = await getLiteLLMTeam(config);
  const teamInfo = body.team_info;
  if (!isRecord(teamInfo)) {
    throw new Error("LiteLLM team response is missing team_info");
  }
  return {
    maxBudget: nullableNonNegativeNumber(
      teamInfo.max_budget,
      "team_info.max_budget",
    ),
    spend: requiredNonNegativeNumber(teamInfo.spend, "team_info.spend"),
  };
}

export async function getLiteLLMMemberState(
  config: BudgetE2EConfig,
  userId: string,
): Promise<LiteLLMMemberState | null> {
  const body = await getLiteLLMTeam(config);
  const teamInfo = body.team_info;
  if (!isRecord(teamInfo)) {
    throw new Error("LiteLLM team response is missing team_info");
  }
  const defaultBudgetTable = teamInfo.team_member_budget_table;
  if (
    defaultBudgetTable !== undefined &&
    defaultBudgetTable !== null &&
    !isRecord(defaultBudgetTable)
  ) {
    throw new Error("team_info.team_member_budget_table must be an object");
  }
  const defaultMaxBudget = isRecord(defaultBudgetTable)
    ? nullableNonNegativeNumber(
        defaultBudgetTable.max_budget,
        "team_info.team_member_budget_table.max_budget",
      )
    : null;

  const memberships = Array.isArray(body.team_memberships)
    ? body.team_memberships
    : [];
  const membership = memberships.find(
    (candidate) => isRecord(candidate) && candidate.user_id === userId,
  );
  if (isRecord(membership)) {
    const budgetTable = membership.litellm_budget_table;
    if (
      budgetTable !== undefined &&
      budgetTable !== null &&
      !isRecord(budgetTable)
    ) {
      throw new Error(
        `membership.${userId}.litellm_budget_table must be an object`,
      );
    }
    return {
      userId,
      maxBudget: isRecord(budgetTable)
        ? nullableNonNegativeNumber(
            budgetTable.max_budget,
            `membership.${userId}.litellm_budget_table.max_budget`,
          )
        : defaultMaxBudget,
      spend: requiredNonNegativeNumber(
        membership.spend,
        `membership.${userId}.spend`,
      ),
    };
  }

  const roster = Array.isArray(teamInfo.members_with_roles)
    ? teamInfo.members_with_roles
    : [];
  const isRosterMember = roster.some(
    (candidate) => isRecord(candidate) && candidate.user_id === userId,
  );
  if (!isRosterMember) return null;

  const matchingKeys = (Array.isArray(body.keys) ? body.keys : []).filter(
    (candidate) => isRecord(candidate) && candidate.user_id === userId,
  );
  if (matchingKeys.length === 0) {
    throw new Error(`role-only member ${userId} has no validated key spend`);
  }
  const spend = matchingKeys.reduce(
    (total, key, index) =>
      total +
      requiredNonNegativeNumber(
        (key as Record<string, unknown>).spend,
        `keys.${userId}.${index}.spend`,
      ),
    0,
  );
  return { userId, maxBudget: defaultMaxBudget, spend };
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

export async function removeLiteLLMTeamMember(
  config: BudgetE2EConfig,
  userId: string,
): Promise<void> {
  await updateLiteLLM(config, "team/member_delete", {
    team_id: config.orgId,
    user_id: userId,
  });
}

export async function ensureLiteLLMTeamMember(
  config: BudgetE2EConfig,
  userId: string,
  maxBudget: number | null,
): Promise<void> {
  if (await getLiteLLMMemberState(config, userId)) return;
  await updateLiteLLM(config, "team/member_add", {
    team_id: config.orgId,
    member: {
      user_id: userId,
      role: "user",
    },
    max_budget_in_team: maxBudget,
  });
}

export async function createLiteLLMTestKey(
  config: BudgetE2EConfig,
  userId: string,
): Promise<LiteLLMGeneratedKey> {
  if (!config.litellmUrl || !config.litellmApiKey || !config.directModel) {
    throw new Error("LiteLLM direct-traffic configuration is not available");
  }
  const keyAlias = `budget-e2e-${config.orgId}-${userId}-${Date.now()}`;
  const response = await fetch(
    new URL("key/generate", `${config.litellmUrl.replace(/\/$/, "")}/`),
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-goog-api-key": config.litellmApiKey,
      },
      body: JSON.stringify({
        user_id: userId,
        team_id: config.orgId,
        key_alias: keyAlias,
        models: [config.directModel],
      }),
    },
  );
  if (!response.ok) {
    throw new Error(
      `generate LiteLLM test key failed (${response.status} ${response.statusText}): ${await response.text()}`,
    );
  }
  const generated = (await response.json()) as LiteLLMGeneratedKey;
  if (!generated.key) {
    throw new Error("LiteLLM test key response did not contain a key");
  }
  return { key: generated.key, key_alias: keyAlias };
}

export function deleteLiteLLMTestKey(
  config: BudgetE2EConfig,
  keyAlias: string,
): Promise<void> {
  return updateLiteLLM(config, "key/delete", { key_aliases: [keyAlias] });
}

export async function requestDirectLiteLLMCompletion(
  config: BudgetE2EConfig,
  key: string,
): Promise<LiteLLMCompletionResult> {
  if (!config.litellmUrl || !config.litellmApiKey || !config.directModel) {
    throw new Error("LiteLLM direct-traffic configuration is not available");
  }
  const response = await fetch(
    new URL("chat/completions", `${config.litellmUrl.replace(/\/$/, "")}/`),
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: config.directModel,
        messages: [
          {
            role: "system",
            content:
              "You are responding to an automated budget certification request.",
          },
          { role: "user", content: config.directPrompt },
        ],
        max_tokens: 1024,
      }),
    },
  );
  return { status: response.status, body: await response.text() };
}

export async function generateDirectLiteLLMSpend(
  config: BudgetE2EConfig,
  key: string,
): Promise<void> {
  const response = await requestDirectLiteLLMCompletion(config, key);
  if (response.status < 200 || response.status >= 300) {
    throw new Error(
      `direct LiteLLM completion failed (${response.status}): ${response.body}`,
    );
  }
  JSON.parse(response.body);
}

export async function requireDirectBudgetDenial(
  config: BudgetE2EConfig,
  key: string,
): Promise<LiteLLMCompletionResult> {
  const response = await requestDirectLiteLLMCompletion(config, key);
  if (
    ![400, 429].includes(response.status) ||
    !/budget|exceed|limit/i.test(response.body)
  ) {
    throw new Error(
      `expected a LiteLLM budget denial, received ${response.status}: ${response.body}`,
    );
  }
  return response;
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
