import { expect, test } from "@playwright/test";

import { fetchWebClientConfig } from "../utils/billing";
import {
  authNewUserFile,
  newUserEmail,
  runUser,
  superAdminApiKey,
} from "../utils/config";
import {
  createPromptAutomation,
  deleteAutomation,
  dispatchAutomation,
  isAutomationServiceReachable,
  waitForRunTerminal,
  type AutomationResponse,
} from "../utils/automations";
import { Cleanup } from "../utils/cleanup";
import {
  createOrg,
  deleteOrg,
  e2eOrgName,
  e2eTimestamp,
  provisionUser,
  type OrgResponse,
  type ProvisionUserResponse,
} from "../utils/org-management";

/**
 * Automations specs.
 *
 * Exercises the OpenHands Automation Service surface at
 * ``/api/automation/v1/*`` end-to-end via REST (no browser). Runs only for
 * the "new-user" role: it provisions the New User into two fresh e2e orgs at
 * different roles and drives the whole flow with the org-bound API keys the
 * provisioning API returns.
 *
 * Coverage:
 *
 *  1. **Org-role regression guard** — the New User is provisioned as
 *     ``owner`` into org A and ``member`` into org B. Each of those two
 *     API keys creates a prompt-preset automation. Both requests must
 *     return 201. A recent regression made ``POST preset/prompt`` reject
 *     ``member`` role with 403 (``manage_automations`` permission had been
 *     scoped too tightly); this test would have caught it.
 *
 *  2. **Cron-preset lifecycle** — the automation created by the owner is
 *     dispatched immediately (bypassing cron) and polled until its most
 *     recent run reaches a terminal status (``COMPLETED`` or ``FAILED``).
 *     ``FAILED`` is accepted here because whether the sandbox agent
 *     successfully executes the trivial prompt depends on cluster-level
 *     LLM configuration that this suite does not own; the value of the
 *     assertion is proving that dispatch → sandbox spawn → callback →
 *     status persistence all wired up.
 *
 *  3. **Service account lifecycle** — the owner creates a service account in
 *     org A, finds it in the list, and deletes it. Skips when
 *     ``feature_flags.enable_service_accounts`` is off.
 *
 * Skip conditions:
 *  - ``runUser(testInfo) !== "new-user"`` — provisioning uses the new-user
 *    email, so this suite has nothing to do for the returning role.
 *  - Automation service unreachable at ``BASE_URL`` — the subchart is
 *    optional in the parent chart; when it is not deployed the entire suite
 *    skips rather than failing every case with a 404.
 *
 * Cleanup:
 *  - ``afterAll`` deletes every created automation, then its org (superadmin
 *    ``DELETE /api/admin/organizations``), regardless of test outcome. The
 *    New User is left in place: re-provisioning the same email is idempotent.
 */

interface ServiceAccount {
  id: string;
  org_id: string;
  name: string;
  role: string;
  is_disabled: boolean;
}

interface RoleFixture {
  role: "owner" | "member";
  org: OrgResponse;
  provisioned: ProvisionUserResponse;
  automation?: AutomationResponse;
}

// A single run of the automation is expected to be sub-minute on a warm
// cluster (sandbox already claimed from the warm pool), and up to a few
// minutes on a cold one (agent-server image pull + Postgres check). Give
// enough headroom for a cold path without hanging the suite forever.
const RUN_TERMINAL_TIMEOUT_MS = 5 * 60 * 1000;

test.describe.serial("automations", () => {
  const fixtures: Record<"owner" | "member", RoleFixture | undefined> = {
    owner: undefined,
    member: undefined,
  };
  let baseUrl = "";
  let adminApiKey = "";

  test.beforeAll(async ({ baseURL }, testInfo) => {
    if (runUser(testInfo) !== "new-user") {
      // The per-test ``test.skip`` calls below will short-circuit; nothing
      // to do here.
      return;
    }
    baseUrl = baseURL || "";
    adminApiKey = superAdminApiKey();

    const reachable = await isAutomationServiceReachable(baseUrl, adminApiKey);
    if (!reachable) {
      // Cannot ``test.skip`` from beforeAll on a describe.serial — leave
      // the fixtures unset and let the per-test skip guard handle it.
      console.warn(
        "Automation service is not reachable at " +
          `${baseUrl}/api/automation/v1; skipping the automations suite. ` +
          "Confirm the ``automation`` subchart is enabled on this cluster.",
      );
    }
  });

  const cleanup = new Cleanup();

  test.afterAll(async () => {
    await cleanup.run();
  });

  // Test 1a + 1b: the regression guard, split so the report shows two
  // rows (one per role) rather than one aggregate case.
  for (const role of ["owner", "member"] as const) {
    test(`${role} in a fresh org can create a prompt automation`, async ({
      baseURL,
    }, testInfo) => {
      test.skip(runUser(testInfo) !== "new-user", "new-user role only");

      const base = baseURL || "";
      const adminKey = superAdminApiKey();
      test.skip(
        !(await isAutomationServiceReachable(base, adminKey)),
        "Automation service not reachable on this cluster",
      );

      const timestamp = e2eTimestamp();
      const org = await createOrg(base, adminKey, {
        name: e2eOrgName(`automations ${role}`, timestamp),
        contact_name: "Testy Tester",
        contact_email: "e2e_test@openhands.org",
      });
      cleanup.defer(`delete org ${org.id}`, () =>
        deleteOrg(base, adminKey, org.id),
      );
      console.log(`Created org "${org.name}" (id=${org.id}) for role=${role}`);

      const provisioned = await provisionUser(base, adminKey, org.id, {
        email: newUserEmail(),
        role,
      });
      expect(provisioned.role).toBe(role);
      expect(provisioned.api_key).toBeTruthy();

      const automation = await createPromptAutomation(
        base,
        provisioned.api_key,
        {
          name: `e2e ${role} automation ${timestamp}`,
          // A benign prompt: no repo cloning, no external side effects. The
          // regression this asserts on is the authorization check on the
          // POST — whether the agent later produces meaningful output is
          // out of scope for this test.
          prompt:
            "Print the word ready and exit. Do not modify any files, " +
            "do not call any tools, do not clone any repositories.",
          // A far-future cron so the automation is quiescent between the
          // moment it is created and the moment the cleanup ``afterAll``
          // deletes it. 3 AM on the 29th of February — the automation is
          // deleted long before it could ever tick.
          trigger: {
            type: "cron",
            schedule: "0 3 29 2 *",
            timezone: "UTC",
          },
        },
      );

      cleanup.defer(`delete automation ${automation.id}`, () =>
        deleteAutomation(base, provisioned.api_key, automation.id),
      );
      expect(automation.id).toBeTruthy();
      expect(automation.enabled).toBe(true);
      expect(automation.trigger.type).toBe("cron");

      fixtures[role] = { role, org, provisioned, automation };
      console.log(
        `${role} created automation ${automation.id} in org ${org.id}`,
      );
    });
  }

  // Test 2: cron-preset lifecycle. Uses the owner's automation from test
  // 1a (member's is left untouched — a dispatch from the member key
  // exercises the same code path, so one is enough).
  // Playwright reflects the first arg to detect fixture use and requires an
  // object destructuring pattern even when no fixture is needed.
  // eslint-disable-next-line no-empty-pattern
  test("dispatch reaches a terminal run status", async ({}, testInfo) => {
    test.skip(runUser(testInfo) !== "new-user", "new-user role only");
    const { owner } = fixtures;
    test.skip(
      !owner?.automation,
      "owner automation must have been created by the prior test",
    );
    // Non-null assertions safe below: the skip above returns when either
    // is falsy, so ``owner`` and ``owner.automation`` are both defined
    // past this point.
    const ownerFixture = owner as RoleFixture & {
      automation: AutomationResponse;
    };

    // Cannot inline this in test.slow(...) because Playwright needs the
    // slower budget applied before the poll begins.
    test.setTimeout(RUN_TERMINAL_TIMEOUT_MS + 60_000);

    await dispatchAutomation(
      baseUrl,
      ownerFixture.provisioned.api_key,
      ownerFixture.automation.id,
    );

    const run = await waitForRunTerminal(
      baseUrl,
      ownerFixture.provisioned.api_key,
      ownerFixture.automation.id,
      RUN_TERMINAL_TIMEOUT_MS,
    );

    // Accept either terminal status: the goal is to prove the plumbing
    // (dispatch -> queue -> sandbox -> callback -> status persistence),
    // not to assert on the agent's own success. When the run fails, log
    // the error_detail so the report is useful.
    if (run.status === "FAILED") {
      console.warn(
        `Automation run ${run.id} FAILED (plumbing worked): ` +
          `${run.error_detail ?? "no error_detail"}`,
      );
    }
    expect(["COMPLETED", "FAILED"]).toContain(run.status);
  });

  // Test 3: service account lifecycle in the owner's org from test 1a. The
  // delete asks the Automation Service whether an automation runs as the
  // account, so it belongs with this suite.
  test("owner creates, lists and deletes a service account", async ({
    baseURL,
    playwright,
  }, testInfo) => {
    test.skip(runUser(testInfo) !== "new-user", "new-user role only");
    const { owner } = fixtures;
    test.skip(!owner, "owner org must have been created by the prior test");
    const orgId = (owner as RoleFixture).org.id;

    // The session, not ``provisioned.api_key``: for an existing user that is
    // their existing key of the same name, which can be bound to another org.
    const api = await playwright.request.newContext({
      baseURL,
      ignoreHTTPSErrors: true,
      storageState: authNewUserFile,
    });
    cleanup.defer("dispose service account request context", () =>
      api.dispose(),
    );

    const config = await fetchWebClientConfig(api);
    test.skip(
      config.feature_flags?.enable_service_accounts !== true,
      "service accounts are disabled (feature_flags.enable_service_accounts is falsy)",
    );

    const collection = `/api/organizations/${orgId}/service-accounts`;
    const name = `e2e service account ${e2eTimestamp()}`;
    const created = await api.post(collection, { data: { name } });
    expect(created.status(), await created.text()).toBe(201);
    const account = (await created.json()) as ServiceAccount;
    cleanup.defer(`delete service account ${account.id}`, async () => {
      const response = await api.delete(`${collection}/${account.id}`);
      if (!response.ok() && response.status() !== 404) {
        throw new Error(
          `${response.status()} ${response.statusText()}: ${await response.text()}`,
        );
      }
    });
    expect(account).toMatchObject({
      org_id: orgId,
      name,
      role: "service_account",
      is_disabled: false,
    });

    const listed = await api.get(`${collection}?limit=100`);
    expect(listed.status(), await listed.text()).toBe(200);
    const listing = (await listed.json()) as { items: ServiceAccount[] };
    expect(listing.items.map((item) => item.id)).toContain(account.id);

    const deleted = await api.delete(`${collection}/${account.id}`);
    expect(deleted.status(), await deleted.text()).toBe(204);

    const gone = await api.get(`${collection}/${account.id}`);
    expect(gone.status(), await gone.text()).toBe(404);
  });
});
