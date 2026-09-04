import {
  expect,
  test,
  type APIRequestContext,
  type APIResponse,
} from "@playwright/test";

import { runUser } from "../utils/config";

interface CurrentUser {
  id: string;
  email: string;
}

interface Organization {
  id: string;
  name: string;
}

interface OrganizationPage {
  current_org_id: string | null;
}

interface ProvisionedUser {
  api_key: string;
  user_id: string;
}

interface OrganizationSettings {
  agent_settings: {
    llm: {
      model: string;
      base_url?: string | null;
    };
  };
}

interface MemberFinancial {
  user_id: string;
  lifetime_spend: number;
}

interface MemberFinancialPage {
  items: MemberFinancial[];
}

interface ConversationStartTask {
  id: string;
  status: string;
  detail?: string | null;
  app_conversation_id?: string | null;
  created_by_user_id?: string | null;
}

const enabled =
  process.env.MANAGED_KEY_E2E_MUTATION_CONFIRMED?.toLowerCase() === "true";

async function json<T>(response: APIResponse, operation: string): Promise<T> {
  if (!response.ok()) {
    throw new Error(
      `${operation} failed (${response.status()} ${response.statusText()}): ${await response.text()}`,
    );
  }
  return (await response.json()) as T;
}

async function financialByUser(
  request: APIRequestContext,
  orgId: string,
): Promise<Map<string, number>> {
  const response = await request.get(
    `/api/organizations/${orgId}/members/financial?limit=100`,
  );
  const page = await json<MemberFinancialPage>(response, "load member spend");
  return new Map(
    page.items.map(({ user_id, lifetime_spend }) => [user_id, lifetime_spend]),
  );
}

async function waitForConversation(
  request: APIRequestContext,
  taskId: string,
): Promise<ConversationStartTask> {
  const deadline = Date.now() + 8 * 60_000;
  while (Date.now() < deadline) {
    const response = await request.get(
      `/api/v1/app-conversations/start-tasks?ids=${encodeURIComponent(taskId)}`,
    );
    const tasks = await json<ConversationStartTask[]>(
      response,
      "load conversation start task",
    );
    const task = tasks[0];
    if (task?.status === "READY") return task;
    if (task?.status === "ERROR") {
      throw new Error(
        `conversation startup failed: ${task.detail ?? "unknown"}`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 5_000));
  }
  throw new Error("conversation did not become ready within eight minutes");
}

test.describe.serial("managed LLM key ownership @managed-key", () => {
  test.beforeEach(({ page: _page }, testInfo) => {
    test.skip(runUser(testInfo) !== "returning", "returning-user role only");
    test.skip(
      !enabled,
      "set MANAGED_KEY_E2E_MUTATION_CONFIRMED=true on a disposable install",
    );
  });

  test("org defaults preserve per-member attribution", async ({
    baseURL,
    page,
    playwright,
  }, testInfo) => {
    test.setTimeout(12 * 60_000);

    const suffix = `${Date.now()}`;
    const secondaryEmail = `managed-key-e2e+${suffix}@openhands.dev`;
    let originalOrgId: string | null = null;
    let orgId: string | null = null;
    let secondaryRequest: APIRequestContext | null = null;
    let conversationId: string | null = null;

    try {
      const me = await json<CurrentUser>(
        await page.request.get("/api/v1/users/me"),
        "load current user",
      );
      const organizations = await json<OrganizationPage>(
        await page.request.get("/api/organizations"),
        "load organizations",
      );
      originalOrgId = organizations.current_org_id;

      const org = await json<Organization>(
        await page.request.post("/api/organizations", {
          data: {
            name: `managed-key-e2e-${suffix}`,
            contact_name: "Managed Key E2E",
            contact_email: "managed-key-e2e@openhands.dev",
          },
        }),
        "create test organization",
      );
      orgId = org.id;

      const owner = await json<ProvisionedUser>(
        await page.request.post("/api/organizations/provision-user", {
          headers: { "X-Org-Id": orgId },
          data: {
            email: me.email,
            role: "owner",
            api_key_name: `managed-key-e2e-owner-${suffix}`,
          },
        }),
        "provision owner",
      );
      expect(owner.user_id).toBe(me.id);
      const secondary = await json<ProvisionedUser>(
        await page.request.post("/api/organizations/provision-user", {
          headers: { "X-Org-Id": orgId },
          data: {
            email: secondaryEmail,
            role: "member",
            api_key_name: `managed-key-e2e-member-${suffix}`,
          },
        }),
        "provision secondary member",
      );

      await json<Organization>(
        await page.request.post(`/api/organizations/${orgId}/switch`),
        "switch to test organization",
      );

      const settings = await json<OrganizationSettings>(
        await page.request.get(`/api/organizations/${orgId}/settings`),
        "load organization settings",
      );
      const { llm } = settings.agent_settings;
      expect(llm.model).toBeTruthy();

      const before = await financialByUser(page.request, orgId);
      expect(before.has(owner.user_id)).toBe(true);
      expect(before.has(secondary.user_id)).toBe(true);

      await json<OrganizationSettings>(
        await page.request.patch(`/api/organizations/${orgId}/settings`, {
          data: {
            agent_settings_diff: {
              llm: { model: llm.model, base_url: llm.base_url ?? null },
            },
          },
        }),
        "save managed organization defaults",
      );

      secondaryRequest = await playwright.request.newContext({
        baseURL,
        ignoreHTTPSErrors: true,
        extraHTTPHeaders: { "X-Access-Token": secondary.api_key },
      });
      const startTask = await json<ConversationStartTask>(
        await secondaryRequest.post("/api/v1/app-conversations", {
          data: {
            initial_message: {
              role: "user",
              content: [
                {
                  type: "text",
                  text: `Testing managed key ownership ${suffix}. Reply with OK.`,
                },
              ],
            },
            trigger: "openhands_api",
            title: `Managed key ownership ${suffix}`,
          },
        }),
        "start secondary-member conversation",
      );
      expect(startTask.created_by_user_id).toBe(secondary.user_id);

      const readyTask = await waitForConversation(
        secondaryRequest,
        startTask.id,
      );
      conversationId = readyTask.app_conversation_id ?? null;
      expect(conversationId).toBeTruthy();

      const ownerSpendBefore = before.get(owner.user_id) ?? 0;
      const memberSpendBefore = before.get(secondary.user_id) ?? 0;
      const spendDeadline = Date.now() + 4 * 60_000;
      let after = before;
      while (Date.now() < spendDeadline) {
        after = await financialByUser(page.request, orgId);
        if ((after.get(secondary.user_id) ?? 0) > memberSpendBefore) break;
        await new Promise((resolve) => setTimeout(resolve, 5_000));
      }

      const ownerSpendAfter = after.get(owner.user_id) ?? 0;
      const memberSpendAfter = after.get(secondary.user_id) ?? 0;
      expect(memberSpendAfter).toBeGreaterThan(memberSpendBefore);
      expect(ownerSpendAfter).toBeCloseTo(ownerSpendBefore, 8);

      const attributionEvidence = {
        org_id: orgId,
        owner_user_id: owner.user_id,
        member_user_id: secondary.user_id,
        owner_spend_before: ownerSpendBefore,
        owner_spend_after: ownerSpendAfter,
        member_spend_before: memberSpendBefore,
        member_spend_after: memberSpendAfter,
        conversation_id: conversationId,
      };
      console.log(
        `Managed key attribution evidence: ${JSON.stringify(attributionEvidence)}`,
      );
      await testInfo.attach("managed-key-attribution.json", {
        body: JSON.stringify(attributionEvidence, null, 2),
        contentType: "application/json",
      });
    } finally {
      if (secondaryRequest && conversationId) {
        await secondaryRequest
          .delete(`/api/v1/app-conversations/${conversationId}`)
          .catch(() => undefined);
      }
      await secondaryRequest?.dispose();

      if (originalOrgId) {
        await page.request
          .post(`/api/organizations/${originalOrgId}/switch`)
          .catch(() => undefined);
      }
      if (orgId) {
        await page.request
          .delete(`/api/organizations/${orgId}`)
          .catch(() => undefined);
      }
    }
  });
});
