import { assertE2eOnlyEmail, type KeycloakAdminConfig } from "./config";

/**
 * Keycloak Admin REST API helper used to delete and recreate the New User
 * before each run.
 *
 * At the start of a test run the Keycloak admin deletes any user whose email
 * matches the New User's email, then creates a fresh, Keycloak-native user
 * with a password credential. The setup project then drives that user through
 * Keycloak's local login form (no GitHub involved), exercising the app's
 * first-login onboarding path deterministically.
 *
 * We deliberately do not federate the New User to GitHub. That leg is covered
 * by the returning-user path; the new-user path exists to exercise TOS
 * acceptance, the onboarding form, org bootstrap, and the app's home landing
 * — none of which involve github.com, and all of which were regularly broken
 * by GitHub product changes when we drove real OAuth. See PR history around
 * ``dismissTwoFactorReminder`` for the shape of that fragility.
 *
 * Assumptions:
 *  - The admin credentials authenticate against the ``master`` realm using
 *    the ``admin-cli`` client (password grant). This is the default for
 *    Keycloak admins.
 *  - Users are looked up by exact email match in the target realm (default
 *    ``allhands``) and every match is deleted.
 *  - The email under ``config.newUserEmail`` is under a reserved e2e-only
 *    TLD (see ``assertE2eOnlyEmail``), so the delete-by-email call cannot
 *    ever match a real user.
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
 *
 * The email is required to be under an e2e-only TLD (see
 * ``assertE2eOnlyEmail``); this is a defense-in-depth guard against a config
 * typo pointing the delete at a real account.
 */
export async function deleteNewUsersByEmail(
  config: KeycloakAdminConfig,
): Promise<number> {
  assertE2eOnlyEmail(config.newUserEmail);
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

/**
 * Credentials needed to create a fresh Keycloak-native new user in the target
 * realm. The user is created ``enabled: true``, ``emailVerified: true``, with a
 * single non-temporary password credential — everything the app's OIDC login
 * flow needs to treat them as a fully-provisioned identity on first login.
 */
export interface NewRealmUser {
  email: string;
  username: string;
  password: string;
  firstName?: string;
  lastName?: string;
}

/**
 * Create a synthetic new user directly in the target realm.
 *
 * This is the counterpart to ``deleteNewUsersByEmail``: the run's setup
 * project deletes any prior synthetic user, then creates a fresh one with a
 * known password so Playwright can drive Keycloak's local login form
 * deterministically. No GitHub identity provider link is created; the user is
 * a native Keycloak account.
 *
 * The email is required to be under an e2e-only TLD, matching the delete
 * guard, so this helper cannot be pointed at a namespace that overlaps with
 * real users.
 */
export async function createRealmUser(
  config: KeycloakAdminConfig,
  user: NewRealmUser,
): Promise<void> {
  assertE2eOnlyEmail(user.email);
  const token = await getAdminToken(config);

  const res = await fetch(
    `${config.keycloakUrl}/admin/realms/${config.realm}/users`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        username: user.username,
        email: user.email,
        emailVerified: true,
        enabled: true,
        firstName: user.firstName,
        lastName: user.lastName,
        credentials: [
          {
            type: "password",
            value: user.password,
            temporary: false,
          },
        ],
      }),
    },
  );

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Keycloak create user ${user.username} failed (${res.status} ${res.statusText}): ${text}`,
    );
  }

  console.log(
    `[keycloak-cleanup] Created realm user ${user.username} (${user.email}) in realm "${config.realm}".`,
  );
}
