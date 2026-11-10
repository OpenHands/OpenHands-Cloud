import { assertE2eOnlyEmail, e2eRunId } from "./config";

/**
 * REST API client for the org-management e2e specs.
 *
 * These helpers drive the OpenHands Cloud org/provisioning REST surface
 * directly (outside the browser) using a super-admin API key. They wrap the
 * two endpoints exercised by the org-management suite:
 *
 *  - ``POST /api/organizations`` (see enterprise/server/routes/orgs.py)
 *    Requires the ``CREATE_ORGANIZATION`` permission, held only by the
 *    instance-level ``superadmin`` role. The creator is *not* added as a
 *    member.
 *
 *  - ``POST /api/organizations/provision-user``
 *    (see enterprise/server/routes/user_provisioning.py)
 *    Requires the ``PROVISION_USER`` permission. The target org is supplied
 *    via the ``X-Org-Id`` header (resolved by ``EFFECTIVE_ORG_ID``) because the
 *    superadmin is not a member of the org it just created. The call is
 *    idempotent: re-running for an existing user only ensures org membership
 *    and returns the existing API key.
 *
 *  - ``GET`` and ``DELETE /api/admin/organizations`` (enterprise
 *    server/routes/instance_admin.py), for teardown. Mounted only when the
 *    Super Admin dashboard is enabled.
 *
 *  - ``GET`` and ``DELETE /api/admin/directory/users`` (same module), to
 *    delete the New User's orphaned app accounts.
 *
 * Authentication: the super-admin API key is sent as ``Authorization: Bearer
 * <key>``. The key must be *unbound* (no org binding) so the server resolves
 * the target org per-request from the ``X-Org-Id`` header instead of pinning
 * it to the key's org.
 */

/** Response shape of ``POST /api/organizations`` (OrgResponse). */
export interface OrgResponse {
  id: string;
  name: string;
  contact_name: string;
  contact_email: string;
  [key: string]: unknown;
}

/** Response shape of ``POST /api/organizations/provision-user``. */
export interface ProvisionUserResponse {
  email: string;
  password: string | null;
  api_key: string;
  user_id: string;
  org_id: string;
  role: string;
  created: boolean;
  action: "created" | "added_to_org" | "reprovisioned";
}

/** Payload for creating an organization. */
export interface CreateOrgPayload {
  name: string;
  contact_name: string;
  contact_email: string;
}

/** Payload for provisioning a user into an org. */
export interface ProvisionUserPayload {
  email: string;
  password?: string;
  api_key_name?: string;
  role?: "member" | "admin" | "owner";
  reissue_api_key?: boolean;
}

/**
 * Build the auth headers for a super-admin REST call. The key is sent as a
 * Bearer token; when ``orgId`` is supplied it is also sent as ``X-Org-Id`` so
 * the server targets the org the superadmin is not a member of.
 */
function superAdminHeaders(
  apiKey: string,
  orgId?: string,
): Record<string, string> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${apiKey}`,
    "Content-Type": "application/json",
  };
  if (orgId) {
    headers["X-Org-Id"] = orgId;
  }
  return headers;
}

/**
 * Create an organization via ``POST /api/organizations``.
 *
 * The caller (superadmin) is not added as a member; the returned ``id`` is
 * used to target subsequent calls (e.g. provisioning users) via ``X-Org-Id``.
 */
export async function createOrg(
  baseUrl: string,
  apiKey: string,
  payload: CreateOrgPayload,
): Promise<OrgResponse> {
  const url = new URL("api/organizations", baseUrl).toString();
  const response = await fetch(url, {
    method: "POST",
    headers: superAdminHeaders(apiKey),
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      `createOrg failed (${response.status} ${response.statusText}): ${body}`,
    );
  }
  return (await response.json()) as OrgResponse;
}

/**
 * Provision a user into an organization via
 * ``POST /api/organizations/provision-user``.
 *
 * The target org is supplied via the ``X-Org-Id`` header. The call is
 * idempotent: re-running for an existing user only ensures org membership and
 * returns the existing API key.
 */
export async function provisionUser(
  baseUrl: string,
  apiKey: string,
  orgId: string,
  payload: ProvisionUserPayload,
): Promise<ProvisionUserResponse> {
  const url = new URL("api/organizations/provision-user", baseUrl).toString();
  const response = await fetch(url, {
    method: "POST",
    headers: superAdminHeaders(apiKey, orgId),
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      `provisionUser failed (${response.status} ${response.statusText}): ${body}`,
    );
  }
  return (await response.json()) as ProvisionUserResponse;
}

/** Delete any non-personal organization as a superadmin. A 404 counts as already deleted. */
export async function deleteOrg(
  baseUrl: string,
  apiKey: string,
  orgId: string,
): Promise<void> {
  const url = new URL(`api/admin/organizations/${orgId}`, baseUrl).toString();
  const response = await fetch(url, {
    method: "DELETE",
    headers: superAdminHeaders(apiKey),
  });
  if (!response.ok && response.status !== 404) {
    const body = await response.text();
    throw new Error(
      `deleteOrg ${orgId} failed (${response.status} ${response.statusText}): ${body}`,
    );
  }
}

/** Every organization on the instance, as the Super Admin directory lists them. */
export async function listAllOrgs(
  baseUrl: string,
  apiKey: string,
): Promise<Array<{ id: string; name: string }>> {
  const url = new URL("api/admin/organizations", baseUrl).toString();
  const response = await fetch(url, { headers: superAdminHeaders(apiKey) });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      `listAllOrgs failed (${response.status} ${response.statusText}): ${body}`,
    );
  }
  const { organizations } = (await response.json()) as {
    organizations: Array<{ id: string; name: string }>;
  };
  return organizations;
}

/** Filesystem- and name-safe UTC timestamp, e.g. ``2026-11-04T20-03-58-435Z``. */
export function e2eTimestamp(now: Date = new Date()): string {
  return now.toISOString().replace(/[:.]/g, "-");
}

/** Name for an org a spec creates. The global setup and teardown find leftover orgs by this shape. */
export function e2eOrgName(
  label: string,
  timestamp = e2eTimestamp(),
  runId = e2eRunId(),
): string {
  return `e2e ${label} ${timestamp} run-${runId}`;
}

// The run suffix is optional: orgs created before it existed are still swept.
const E2E_ORG_NAME =
  /^e2e .+ (\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})-(\d{3})Z(?: run-([a-z0-9]{1,32}))?$/;

export interface E2eOrgName {
  createdAt: Date;
  runId: string | null;
}

/** Parse an ``e2eOrgName``, or null for any other name. */
export function parseE2eOrgName(name: string): E2eOrgName | null {
  const match = E2E_ORG_NAME.exec(name);
  if (!match) {
    return null;
  }
  const [, date, hours, minutes, seconds, millis, runId] = match;
  const createdAt = new Date(
    `${date}T${hours}:${minutes}:${seconds}.${millis}Z`,
  );
  if (Number.isNaN(createdAt.getTime())) {
    return null;
  }
  return { createdAt, runId: runId ?? null };
}

/**
 * Delete every ``e2eOrgName`` org that ``select`` picks. Never throws: a sweep
 * that fails must not fail or skip the run, so problems are logged.
 */
export async function sweepE2eOrgs(
  baseUrl: string,
  apiKey: string,
  select: (org: E2eOrgName) => boolean,
  logPrefix: string,
): Promise<void> {
  let orgs: Array<{ id: string; name: string }>;
  try {
    orgs = await listAllOrgs(baseUrl, apiKey);
  } catch (err) {
    console.warn(`${logPrefix}: could not list orgs, skipping: ${err}`);
    return;
  }

  const targets = orgs.filter((org) => {
    const parsed = parseE2eOrgName(org.name);
    return parsed !== null && select(parsed);
  });
  let deleted = 0;
  for (const org of targets) {
    try {
      await deleteOrg(baseUrl, apiKey, org.id);
      deleted += 1;
    } catch (err) {
      console.warn(`${logPrefix}: could not delete "${org.name}": ${err}`);
    }
  }
  console.log(`${logPrefix}: deleted ${deleted} of ${targets.length} e2e orgs`);
}

/**
 * Delete every app account with ``email`` except ``keepUserId``. Never throws:
 * problems are logged, and the login that follows fails on its own if they
 * matter.
 *
 * The New User setup once recreated the realm user on every run, and the
 * legacy login made a new app account for each new Keycloak ``sub``. Those
 * accounts can no longer sign in, and OAuth v2 login refuses an email that
 * more than one account shares (409 "Multiple users share this email").
 */
export async function deleteOrphanedAccounts(
  baseUrl: string,
  apiKey: string,
  email: string,
  keepUserId: string,
  logPrefix: string,
): Promise<void> {
  assertE2eOnlyEmail(email);
  const url = new URL("api/admin/directory/users", baseUrl).toString();
  let users: Array<{ user_id: string | null; email: string | null }>;
  try {
    const response = await fetch(url, { headers: superAdminHeaders(apiKey) });
    if (!response.ok) {
      throw new Error(`${response.status} ${await response.text()}`);
    }
    ({ users } = await response.json());
  } catch (err) {
    console.warn(`${logPrefix}: could not list users, skipping: ${err}`);
    return;
  }

  const orphans = users.filter(
    (user) =>
      user.user_id !== null &&
      user.user_id !== keepUserId &&
      user.email?.toLowerCase() === email.toLowerCase(),
  );
  let deleted = 0;
  for (const { user_id: userId } of orphans) {
    const response = await fetch(
      new URL(`api/admin/directory/users/${userId}`, baseUrl).toString(),
      { method: "DELETE", headers: superAdminHeaders(apiKey) },
    ).catch((err: unknown) => err);
    if (response instanceof Response && response.ok) {
      deleted += 1;
    } else {
      const detail =
        response instanceof Response
          ? `${response.status} ${await response.text()}`
          : response;
      console.warn(`${logPrefix}: could not delete ${userId}: ${detail}`);
    }
  }
  console.log(
    `${logPrefix}: deleted ${deleted} of ${orphans.length} orphaned accounts for ${email}`,
  );
}
