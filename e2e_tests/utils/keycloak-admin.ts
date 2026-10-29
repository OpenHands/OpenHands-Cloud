import { assertE2eOnlyEmail, type KeycloakAdminConfig } from "./config";

/**
 * Keycloak Admin REST API helper used to provision the New User before each
 * run.
 *
 * At the start of a test run the Keycloak admin makes sure a Keycloak-native
 * user with the New User's email exists and sets this run's password on it:
 * the user is created on the first run against an instance and reused, with
 * its password reset, on every run after that. The setup project then drives
 * that user through Keycloak's local login form (no GitHub involved).
 *
 * The user is deliberately not deleted and recreated per run. A recreated
 * user gets a new Keycloak ``sub``, and the app's OAuth v2 login links
 * accounts by that subject: an unknown subject is matched by email instead,
 * which the app refuses (409 "Multiple users share this email") once more
 * than one account shares the email -- and the legacy Keycloak login, which
 * keys the app account on the ``sub``, created exactly such a duplicate on
 * every recreate. With one identity per instance, every login after the first
 * resolves by subject (OAuth v2) or by id (legacy) and no second account is
 * ever created.
 *
 * We deliberately do not federate the New User to GitHub. That leg is covered
 * by the returning-user path; the new-user path exists to exercise the app's
 * own sign-in and, on an instance's first run, TOS acceptance, the onboarding
 * form, org bootstrap, and the app's home landing — none of which involve
 * github.com, and all of which were regularly broken by GitHub product
 * changes when we drove real OAuth. See PR history around
 * ``dismissTwoFactorReminder`` for the shape of that fragility.
 *
 * Assumptions:
 *  - The admin credentials authenticate against the ``master`` realm using
 *    the ``admin-cli`` client (password grant). This is the default for
 *    Keycloak admins.
 *  - Users are looked up by exact email match in the target realm (default
 *    ``allhands``).
 *  - The email under ``config.newUserEmail`` is under a reserved e2e-only
 *    domain (see ``assertE2eOnlyEmail``), so the lookup and password reset
 *    can never touch a real user.
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

function postRealmUser(
  config: KeycloakAdminConfig,
  token: string,
  user: NewRealmUser,
): Promise<Response> {
  return fetch(`${config.keycloakUrl}/admin/realms/${config.realm}/users`, {
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
  });
}

/** Set a non-temporary password on an existing realm user. */
async function resetUserPassword(
  config: KeycloakAdminConfig,
  token: string,
  userId: string,
  password: string,
): Promise<void> {
  const res = await fetch(
    `${config.keycloakUrl}/admin/realms/${config.realm}/users/${userId}/reset-password`,
    {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        type: "password",
        value: password,
        temporary: false,
      }),
    },
  );

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Keycloak reset password for user ${userId} failed (${res.status} ${res.statusText}): ${text}`,
    );
  }
}

/**
 * Make sure the synthetic New User exists in the target realm with this run's
 * password.
 *
 * Looks the user up by email. When found, only its password is reset, so the
 * user keeps its Keycloak ``sub`` and the app keeps resolving it to the same
 * account (see the module docstring). Otherwise the user is created as a
 * native Keycloak account with that password; no GitHub identity provider
 * link is created.
 *
 * The email is required to be under an e2e-only domain, so this helper cannot
 * be pointed at a namespace that overlaps with real users.
 */
export async function ensureRealmUser(
  config: KeycloakAdminConfig,
  user: NewRealmUser,
): Promise<void> {
  assertE2eOnlyEmail(user.email);
  const token = await getAdminToken(config);

  const existing = await findUsersByEmail(config, token);
  if (existing.length > 0) {
    // The realm is email-as-username, so at most one user matches.
    const [found] = existing;
    await resetUserPassword(config, token, found.id, user.password);
    console.log(
      `[keycloak-setup] Reusing realm user ${found.username} (${found.email}) in realm "${config.realm}"; password reset for this run.`,
    );
    return;
  }

  const res = await postRealmUser(config, token, user);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Keycloak create user ${user.username} failed (${res.status} ${res.statusText}): ${text}`,
    );
  }

  console.log(
    `[keycloak-setup] Created realm user ${user.username} (${user.email}) in realm "${config.realm}".`,
  );
}
