import { expect, test } from "@playwright/test";

import {
  type BudgetE2EConfig,
  getLiteLLMMemberState,
  getLiteLLMTeamState,
  loadBudgetE2EConfig,
  requestDirectLiteLLMCompletion,
} from "../utils/budgets";

const config: BudgetE2EConfig = {
  enabled: true,
  orgId: "budget-test-org",
  monthlyLimit: 50,
  userMonthlyLimit: 25,
  minimumSpendDelta: 0.02,
  directPrompt: "test prompt",
  directModel: "azure-test-model",
  expectedLiteLLMVersion: "1.94.1",
  pollIntervalMs: 10,
  syncTimeoutMs: 100,
  litellmUrl: "https://litellm.example.test",
  litellmApiKey: "admin-key",
};

test("rejects LiteLLM team responses with missing spend", async () => {
  const originalFetch = global.fetch;
  global.fetch = async () =>
    new Response(JSON.stringify({ team_info: { max_budget: 50 } }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });

  try {
    await expect(getLiteLLMTeamState(config)).rejects.toThrow(
      "team_info.spend must be a finite non-negative number",
    );
  } finally {
    global.fetch = originalFetch;
  }
});

test("direct SDK traffic authenticates only with the virtual key", async () => {
  const originalFetch = global.fetch;
  let observedHeaders: Headers | undefined;
  let observedBody: Record<string, unknown> | undefined;
  global.fetch = async (_input, init) => {
    observedHeaders = new Headers(init?.headers);
    observedBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return new Response('{"error":"budget exceeded"}', { status: 429 });
  };

  try {
    const result = await requestDirectLiteLLMCompletion(config, "virtual-key");
    expect(result.status).toBe(429);
    expect(observedHeaders?.get("authorization")).toBe("Bearer virtual-key");
    expect(observedHeaders?.has("x-goog-api-key")).toBe(false);
    expect(observedBody?.messages).toEqual([
      {
        role: "system",
        content:
          "You are OpenHands agent, a helpful AI assistant that can interact with a computer to solve tasks.",
      },
      { role: "user", content: config.directPrompt },
    ]);
  } finally {
    global.fetch = originalFetch;
  }
});

test("reads nested LiteLLM 1.94.1 member caps and prefers membership spend", async () => {
  const originalFetch = global.fetch;
  global.fetch = async () =>
    new Response(
      JSON.stringify({
        team_info: {
          team_member_budget_table: { max_budget: 50 },
          members_with_roles: [{ user_id: "private-member", role: "user" }],
        },
        team_memberships: [
          {
            user_id: "private-member",
            spend: 12,
            budget_id: "private-budget",
            litellm_budget_table: { max_budget: 25 },
          },
        ],
        keys: [{ user_id: "private-member", spend: 999 }],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );

  try {
    await expect(
      getLiteLLMMemberState(config, "private-member"),
    ).resolves.toEqual({
      userId: "private-member",
      maxBudget: 25,
      spend: 12,
    });
  } finally {
    global.fetch = originalFetch;
  }
});

test("sums key spend for a role-only LiteLLM member", async () => {
  const originalFetch = global.fetch;
  global.fetch = async () =>
    new Response(
      JSON.stringify({
        team_info: {
          team_member_budget_table: null,
          members_with_roles: [{ user_id: "role-only-member", role: "user" }],
        },
        team_memberships: [],
        keys: [
          { user_id: "role-only-member", spend: 2.5 },
          { user_id: "role-only-member", spend: 3.75 },
        ],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );

  try {
    await expect(
      getLiteLLMMemberState(config, "role-only-member"),
    ).resolves.toEqual({
      userId: "role-only-member",
      maxBudget: null,
      spend: 6.25,
    });
  } finally {
    global.fetch = originalFetch;
  }
});

test("accepts omitted Slack settings but rejects partial Slack configuration", () => {
  const names = [
    "BUDGET_E2E_ORG_ID",
    "BUDGET_E2E_MUTATION_CONFIRMED",
    "BUDGET_E2E_DATABASE_URL",
    "BUDGET_E2E_LITELLM_URL",
    "BUDGET_E2E_LITELLM_API_KEY",
    "BUDGET_E2E_DIRECT_MODEL",
    "BUDGET_E2E_SERVICE_USER_ID",
    "BUDGET_E2E_SERVICE_API_KEY",
    "BUDGET_E2E_SLACK_BOT_TOKEN",
    "BUDGET_E2E_SLACK_CHANNEL_ID",
    "BUDGET_E2E_SLACK_CHANNEL_NAME",
    "BUDGET_E2E_SLACK_TEAM_ID",
  ] as const;
  const original = new Map(names.map((name) => [name, process.env[name]]));

  try {
    process.env.BUDGET_E2E_ORG_ID = "budget-test-org";
    process.env.BUDGET_E2E_MUTATION_CONFIRMED = "true";
    process.env.BUDGET_E2E_DATABASE_URL = "postgresql://example.test/db";
    process.env.BUDGET_E2E_LITELLM_URL = "https://litellm.example.test";
    process.env.BUDGET_E2E_LITELLM_API_KEY = "admin-key";
    process.env.BUDGET_E2E_DIRECT_MODEL = "test-model";
    process.env.BUDGET_E2E_SERVICE_USER_ID = "service-user";
    process.env.BUDGET_E2E_SERVICE_API_KEY = "service-key";
    delete process.env.BUDGET_E2E_SLACK_BOT_TOKEN;
    delete process.env.BUDGET_E2E_SLACK_CHANNEL_ID;
    delete process.env.BUDGET_E2E_SLACK_CHANNEL_NAME;
    delete process.env.BUDGET_E2E_SLACK_TEAM_ID;

    expect(loadBudgetE2EConfig()).toMatchObject({
      enabled: true,
      slack: undefined,
    });

    process.env.BUDGET_E2E_SLACK_CHANNEL_ID = "partial-channel";
    expect(() => loadBudgetE2EConfig()).toThrow(
      "All BUDGET_E2E_SLACK_* variables are required when Slack verification is enabled",
    );
  } finally {
    for (const name of names) {
      const value = original.get(name);
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});
