import { test as setup } from "@playwright/test";

import { isEnvironment } from "../../utils/config";
import {
  deleteOrg,
  e2eOrgCreatedAt,
  listAllOrgs,
} from "../../utils/org-management";

// Older than any run still in progress, so concurrent runs keep their orgs.
const STALE_AFTER_MS = 2 * 60 * 60 * 1000;

// Never fails: a failed setup project would skip every new-user test.
setup("delete e2e orgs left behind by earlier runs", async ({ baseURL }) => {
  const apiKey = process.env.SUPER_ADMIN_API_KEY;
  setup.skip(!apiKey, "SUPER_ADMIN_API_KEY is not set");
  setup.skip(isEnvironment("production"), "never sweeps production");

  const base = baseURL || "";
  let orgs: Array<{ id: string; name: string }>;
  try {
    orgs = await listAllOrgs(base, apiKey!);
  } catch (err) {
    console.warn(`Housekeeping: could not list orgs, skipping: ${err}`);
    return;
  }

  const cutoff = Date.now() - STALE_AFTER_MS;
  const stale = orgs.filter((org) => {
    const created = e2eOrgCreatedAt(org.name);
    return created !== null && created.getTime() < cutoff;
  });

  let deleted = 0;
  for (const org of stale) {
    try {
      await deleteOrg(base, apiKey!, org.id);
      deleted += 1;
    } catch (err) {
      console.warn(`Housekeeping: could not delete "${org.name}": ${err}`);
    }
  }
  console.log(
    `Housekeeping: deleted ${deleted} of ${stale.length} stale e2e orgs`,
  );
});
