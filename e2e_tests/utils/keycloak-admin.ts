import type { KeycloakAdminConfig } from "./config";

/**
 * Keycloak Admin REST API helper used to delete the New User before each run.
 *
 * At the start of a test run the Keycloak admin logs in and deletes any user
 * whose email matches the New User's email. This guarantees the New User gets
 * a fresh account (and a fresh user id) when they next log in via GitHub,
 * exercising the "new user" onboarding path.
 *
 * Assumptions:
 *  - The admin credentials authenticate against the `master` realm using the
 *    `admin-cli` client (password grant). This is the default for Keycloak
 *    admins.
 *  - Users are looked up by exact email match in the target realm (default
 *    "allhands") and every match is deleted.
 */

interface KeycloakTokenResponse {
  access_token: string;
}

async function getAdminToken(config: KeycloakAdminConfig): Promise<string> {
  const tokenUrl = `${config.keycloakUrl}/realms/master/protocol/openid-connect/token`;
  const body = new URLSearchParams({
    grant_type: "password",
    client_id: "admin-cli",
    username: config.username,
    password: config.password,
  });

  const res = await fetch(tokenUrl, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Keycloak admin token request failed (${res.status} ${res.statusText}): ${text}`,
    );
  }

  const data = (await res.json()) as KeycloakTokenResponse;
  return data.access_token;
}

interface KeycloakUser {
  id: string;
  username: string;
  email: string;
}

async function findUsersByEmail(
  config: KeycloakAdminConfig,
  token: string,
): Promise<KeycloakUser[]> {
  const url = new URL(
    `${config.keycloakUrl}/admin/realms/${config.realm}/users`,
  );
  url.searchParams.set("email", config.newUserEmail);
  url.searchParams.set("exact", "true");

  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Keycloak user lookup failed (${res.status} ${res.statusText}): ${text}`,
    );
  }

  return (await res.json()) as KeycloakUser[];
}

async function deleteUser(
  config: KeycloakAdminConfig,
  token: string,
  userId: string,
): Promise<void> {
  const res = await fetch(
    `${config.keycloakUrl}/admin/realms/${config.realm}/users/${userId}`,
    { method: "DELETE", headers: { Authorization: `Bearer ${token}` } },
  );

  if (!res.ok && res.status !== 404) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Keycloak delete user ${userId} failed (${res.status} ${res.statusText}): ${text}`,
    );
  }
}

/**
 * Delete every user in the target realm whose email matches the New User's
 * email. Returns the number of users deleted.
 */
export async function deleteNewUsersByEmail(
  config: KeycloakAdminConfig,
): Promise<number> {
  const token = await getAdminToken(config);
  const users = await findUsersByEmail(config, token);

  if (users.length === 0) {
    console.log(
      `[keycloak-cleanup] No existing user found for ${config.newUserEmail} in realm "${config.realm}".`,
    );
    return 0;
  }

  for (const user of users) {
    console.log(
      `[keycloak-cleanup] Deleting user ${user.username} (${user.id}) with email ${user.email}.`,
    );
    await deleteUser(config, token, user.id);
  }

  console.log(
    `[keycloak-cleanup] Deleted ${users.length} user(s) matching ${config.newUserEmail}.`,
  );
  return users.length;
}
