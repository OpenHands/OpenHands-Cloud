import {
  expect,
  test,
  type APIRequestContext,
  type APIResponse,
} from "@playwright/test";

import { BudgetDatabase } from "../utils/budget-database";
import { loadBudgetE2EConfig } from "../utils/budgets";
import { runUser } from "../utils/config";

interface OrgDetails {
  id: string;
  name: string;
  is_personal: boolean;
}

interface OrgPage {
  items: OrgDetails[];
  current_org_id: string | null;
}

const config = loadBudgetE2EConfig();

const PERSONAL_BUDGET_REJECTION =
  "Organization budgets are not available for personal workspaces";

async function organizations(request: APIRequestContext): Promise<OrgPage> {
  const response = await request.get("/api/organizations");
  const body = await expectResponse(response, 200, "list organizations");
  return body as OrgPage;
}

async function expectResponse(
  response: APIResponse,
  status: number,
  operation: string,
): Promise<unknown> {
  const body = await response.text();
  if (response.status() !== status) {
    throw new Error(
      `${operation} returned ${response.status()} ${response.statusText()}; expected ${status}: ${body}`,
    );
  }
  try {
    return JSON.parse(body);
  } catch {
    return body;
  }
}

async function expectBudgetRejected(
  response: APIResponse,
  operation: string,
): Promise<void> {
  const body = await expectResponse(response, 400, operation);
  if (
    typeof body !== "object" ||
    body === null ||
    (body as { detail?: unknown }).detail !== PERSONAL_BUDGET_REJECTION
  ) {
    throw new Error(
      `${operation} rejected with an unexpected body: ${JSON.stringify(body)}`,
    );
  }
}

test.describe.serial("personal workspace budget guards @budgets", () => {
  let database: BudgetDatabase | undefined;
  let personalOrgId: string | null = null;
  let budgetRowCountsBefore: Record<string, number> | null = null;

  test.beforeEach(({ page: _page }, testInfo) => {
    test.skip(
      runUser(testInfo) !== "returning",
      "personal workspace budget guards use the stable returning-user fixture",
    );
    test.skip(
      !config.databaseUrl,
      "set BUDGET_E2E_DATABASE_URL to verify no budget rows are written",
    );
  });

  test("personal workspace rejects every budget entry point without writing rows", async ({
    page,
  }, testInfo) => {
    test.setTimeout(3 * 60_000);

    // page.request shares the project's authenticated browser context, so the
    // requests below act as the returning user rather than as an anonymous
    // client with no storage state.
    const req = page.request;

    const orgs = await organizations(req);
    const current = orgs.items.find((org) => org.id === orgs.current_org_id);
    if (!current) {
      throw new Error("No current organization was selected for the user");
    }

    // The user's own personal workspace is the org named after its owner;
    // fall back to whatever is selected if personal workspaces are hidden.
    const personal =
      orgs.items.find((org) => org.is_personal) ??
      (current.is_personal ? current : null);
    if (!personal) {
      throw new Error(
        "No personal workspace found for the returning user in /api/organizations",
      );
    }
    personalOrgId = personal.id;

    database = new BudgetDatabase(config.databaseUrl!);
    budgetRowCountsBefore =
      await database.getPersonalBudgetRowCounts(personalOrgId);

    await expectBudgetRejected(
      await req.get(`/api/organizations/${personalOrgId}/budgets`),
      "GET personal budget settings",
    );
    await expectBudgetRejected(
      await req.patch(`/api/organizations/${personalOrgId}/budgets`, {
        data: { enabled: false },
      }),
      "PATCH personal budget settings",
    );
    await expectBudgetRejected(
      await req.put(
        `/api/organizations/${personalOrgId}/budgets/overrides/${personalOrgId}`,
        { data: { monthly_limit: 50, is_disabled: false } },
      ),
      "PUT personal budget override",
    );
    await expectBudgetRejected(
      await req.delete(
        `/api/organizations/${personalOrgId}/budgets/overrides/${personalOrgId}`,
      ),
      "DELETE personal budget override",
    );

    if (!database) throw new Error("BudgetDatabase was not initialized");
    const budgetRowCountsAfter =
      await database.getPersonalBudgetRowCounts(personalOrgId);

    await testInfo.attach("personal-budget-row-counts.json", {
      body: JSON.stringify(
        {
          org_id: personalOrgId,
          before: budgetRowCountsBefore,
          after: budgetRowCountsAfter,
        },
        null,
        2,
      ),
      contentType: "application/json",
    });

    // The whole point of the personal-workspace guards: none of the budget
    // tables may gain a row for the personal org (the invariant migration 148
    // enforces). The GET/PATCH/PUT/DELETE calls above are the only entrants
    // a personal workspace can actually reach, so asserting zero created rows
    // here covers the guarded methods end to end.
    expect(budgetRowCountsAfter).toEqual(budgetRowCountsBefore);
  });
});
