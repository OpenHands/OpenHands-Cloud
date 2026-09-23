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
