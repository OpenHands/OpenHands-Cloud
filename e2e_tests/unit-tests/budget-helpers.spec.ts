import { expect, test } from "@playwright/test";

import {
  type BudgetE2EConfig,
  getLiteLLMTeamState,
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
  global.fetch = async (_input, init) => {
    observedHeaders = new Headers(init?.headers);
    return new Response('{"error":"budget exceeded"}', { status: 429 });
  };

  try {
    const result = await requestDirectLiteLLMCompletion(config, "virtual-key");
    expect(result.status).toBe(429);
    expect(observedHeaders?.get("authorization")).toBe("Bearer virtual-key");
    expect(observedHeaders?.has("x-goog-api-key")).toBe(false);
  } finally {
    global.fetch = originalFetch;
  }
});
