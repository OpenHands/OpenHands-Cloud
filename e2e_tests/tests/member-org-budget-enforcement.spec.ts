import {
  expect,
  test,
  type APIRequestContext,
  type BrowserContext,
  type Page,
} from "@playwright/test";
import fs from "fs";
import { authNewUserFile, runUser } from "../utils/config";

test.beforeEach(({ page: _page }, testInfo) => {
  test.skip(
    runUser(testInfo) !== "returning",
    "Requires the stable returning-user fixture.",
  );
});

const NEAR_ZERO_LIMIT = 0.000001;

type Organization = {
  id: string;
  is_personal?: boolean;
};

type BudgetSettings = {
  enabled: boolean;
  monthly_limit: number | null;
  reset_day: number;
  default_user_monthly_limit: number | null;
  slack_channel: string | null;
  slack_team_id: string | null;
  current_spend: number;
  litellm_last_sync_error: string | null;
  thresholds: {
    percentage: number;
    email_enabled: boolean;
    slack_enabled: boolean;
  }[];
};

type StartTask = {
  id: string;
  status: string;
  detail: string | null;
  app_conversation_id: string | null;
};

async function requireAdminOrganization(page: Page): Promise<Organization> {
  const organizationsResponse = await page.request.get("/api/organizations");
  expect(organizationsResponse.ok()).toBe(true);
  const organizations = (await organizationsResponse.json()) as {
    items: Organization[];
    current_org_id: string | null;
  };
  const organization = organizations.items.find(
    ({ id }) => id === organizations.current_org_id,
  );
  if (!organization || organization.is_personal) {
    throw new Error(
      "The admin auth fixture must select a non-personal organization.",
    );
  }

  const meResponse = await page.request.get(
    `/api/organizations/${organization.id}/me`,
    { headers: { "X-Org-Id": organization.id } },
  );
  expect(meResponse.ok()).toBe(true);
  const me = (await meResponse.json()) as { role?: string };
  if (me.role !== "admin" && me.role !== "owner") {
    throw new Error(
      `This test requires an organization admin or owner; received role "${me.role ?? "unknown"}".`,
    );
  }

  return organization;
}

async function waitForTerminalStartTask(
  request: APIRequestContext,
  taskId: string,
): Promise<StartTask> {
  let task: StartTask | undefined;
  await expect
    .poll(
      async () => {
        const response = await request.get(
          `/api/v1/app-conversations/start-tasks?ids=${taskId}`,
        );
        expect(response.ok()).toBe(true);
        const tasks = (await response.json()) as StartTask[];
        [task] = tasks;
        return task?.status;
      },
      { timeout: 60_000, intervals: [250, 500, 1000] },
    )
    .toMatch(/ERROR|READY/);

  if (!task) {
    throw new Error(`Conversation start task ${taskId} was not returned.`);
  }
  return task;
}

test("individual member cannot exceed organization budget without incurring spend", async ({
  browser,
  page,
  baseURL,
}) => {
  const organization = await requireAdminOrganization(page);
  const secondaryAuthState =
    process.env.SECONDARY_AUTH_STATE || authNewUserFile;
  const memberEmail = process.env.TEST_MEMBER_EMAIL;
  if (!secondaryAuthState) {
    throw new Error(
      "SECONDARY_AUTH_STATE must point to a Playwright storage-state file for an existing organization member.",
    );
  }
  if (!fs.existsSync(secondaryAuthState)) {
    throw new Error(
      `SECONDARY_AUTH_STATE does not exist: ${secondaryAuthState}`,
    );
  }
  if (!memberEmail) {
    throw new Error("TEST_MEMBER_EMAIL must identify the secondary member.");
  }
  if (!baseURL) {
    throw new Error(
      "Playwright baseURL is required for the secondary context.",
    );
  }

  const headers = { "X-Org-Id": organization.id };
  const budgetUrl = `/api/organizations/${organization.id}/budgets`;
  const originalResponse = await page.request.get(budgetUrl, { headers });
  expect(originalResponse.ok()).toBe(true);
  const original = (await originalResponse.json()) as BudgetSettings;
  if (original.current_spend <= NEAR_ZERO_LIMIT) {
    throw new Error(
      `This fixture requires existing month-to-date spend greater than ${NEAR_ZERO_LIMIT} so the member probe cannot create spend to reach the cap.`,
    );
  }

  let secondaryContext: BrowserContext | undefined;
  let conversationId: string | null = null;
  try {
    secondaryContext = await browser.newContext({
      baseURL,
      storageState: secondaryAuthState,
    });
    const secondaryOrganizationsResponse =
      await secondaryContext.request.get("/api/organizations");
    expect(secondaryOrganizationsResponse.ok()).toBe(true);
    const secondaryOrganizations =
      (await secondaryOrganizationsResponse.json()) as {
        current_org_id: string | null;
      };
    if (secondaryOrganizations.current_org_id !== organization.id) {
      throw new Error(
        `SECONDARY_AUTH_STATE must have organization ${organization.id} selected; received ${secondaryOrganizations.current_org_id ?? "none"}.`,
      );
    }
    const secondaryMeResponse = await secondaryContext.request.get(
      `/api/organizations/${organization.id}/me`,
      { headers },
    );
    expect(secondaryMeResponse.ok()).toBe(true);
    const secondaryMe = (await secondaryMeResponse.json()) as {
      email?: string;
      role?: string;
    };
    if (secondaryMe.email !== memberEmail || secondaryMe.role !== "member") {
      throw new Error(
        `Secondary fixture must be member ${memberEmail}; received ${secondaryMe.email ?? "unknown"} with role ${secondaryMe.role ?? "unknown"}.`,
      );
    }

    await page.goto("/settings/budgets");
    await expect(
      page.getByRole("heading", { name: "Budget settings" }),
    ).toBeVisible();
    const enabledSwitch = page.getByRole("switch", {
      name: "Enable organization budget",
    });
    if ((await enabledSwitch.getAttribute("aria-checked")) !== "true") {
      await enabledSwitch.click();
    }
    await page.locator("#org-monthly-limit").fill(String(NEAR_ZERO_LIMIT));

    const updateResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === budgetUrl &&
        response.request().method() === "PATCH",
    );
    await page.getByRole("button", { name: "Save changes" }).click();
    const updateResponse = await updateResponsePromise;
    expect(updateResponse.ok()).toBe(true);
    const constrained = (await updateResponse.json()) as BudgetSettings;
    expect(constrained.monthly_limit).toBe(NEAR_ZERO_LIMIT);
    expect(constrained.current_spend).toBe(original.current_spend);
    expect(constrained.litellm_last_sync_error).toBeNull();

    const startResponse = await secondaryContext.request.post(
      "/api/v1/app-conversations",
      {
        data: {
          initial_message: {
            role: "user",
            content: [{ type: "text", text: "member budget rejection probe" }],
          },
          title: `member-budget-rejection-${Date.now()}`,
        },
      },
    );
    expect(startResponse.ok()).toBe(true);
    const initialTask = (await startResponse.json()) as StartTask;
    const terminalTask = await waitForTerminalStartTask(
      secondaryContext.request,
      initialTask.id,
    );
    conversationId = terminalTask.app_conversation_id;
    expect(terminalTask.status).toBe("ERROR");
    expect(terminalTask.app_conversation_id).toBeNull();
    expect(terminalTask.detail).toMatch(/budget.*exceed|budget.*reach/i);

    const afterProbeResponse = await page.request.get(budgetUrl, { headers });
    expect(afterProbeResponse.ok()).toBe(true);
    const afterProbe = (await afterProbeResponse.json()) as BudgetSettings;
    expect(afterProbe.current_spend).toBe(original.current_spend);
  } finally {
    if (conversationId && secondaryContext) {
      await secondaryContext.request.delete(
        `/api/v1/app-conversations/${conversationId}`,
      );
    }
    await secondaryContext?.close();
    const cleanupResponse = await page.request.patch(budgetUrl, {
      headers,
      data: {
        enabled: original.enabled,
        monthly_limit: original.monthly_limit,
        reset_day: original.reset_day,
        default_user_monthly_limit: original.default_user_monthly_limit,
        slack_channel: original.slack_channel,
        slack_team_id: original.slack_team_id,
        thresholds: original.thresholds,
      },
    });
    expect
      .soft(
        cleanupResponse.ok(),
        `Failed to restore budget settings (HTTP ${cleanupResponse.status()}).`,
      )
      .toBe(true);
  }
});
