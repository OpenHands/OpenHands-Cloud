import path from "path";

/**
 * Centralized configuration for the OpenHands Cloud e2e harness.
 *
 * The harness authenticates against Keycloak. A single test run exercises the
 * same specs under two user roles:
 *
 *  - "returning" — a real GitHub user whose OpenHands account already exists.
 *    Federated through Keycloak's GitHub identity provider; exercises the
 *    full app ↔ Keycloak ↔ GitHub round-trip on every run.
 *  - "new-user"  — a *synthetic*, Keycloak-native user (no GitHub link) that
 *    is deleted and recreated at the start of the run by the Keycloak admin,
 *    so they get a fresh account and land on the app's first-login onboarding
 *    path deterministically. GitHub is deliberately not involved: the value
 *    of this role is exercising the app's own new-user flow (TOS, onboarding
 *    form, org bootstrap), not GitHub's OAuth screens.
 *
 * Credential sets required:
 *
 *  1. Keycloak admin (username + password) — used to delete + recreate the
 *     New User in the realm before login.
 *  2. Returning User (GitHub username + password + optional TOTP secret).
 *  3. New User (Keycloak username + password) — a synthetic account managed
 *     entirely by the harness against a reserved e2e-only email domain.
 *
 * Environment variables
 * ---------------------
 * Deployment:
 *  - BASE_URL                      (required) release environment under test.
 *
 * Keycloak admin (cleanup + synthetic user provisioning):
 *  - KEYCLOAK_REALM                realm to administer (default: "allhands").
 *  - KEYCLOAK_ADMIN_USERNAME       admin username.
 *  - KEYCLOAK_ADMIN_PASSWORD       admin password.
 *  - KEYCLOAK_NEW_USER_EMAIL       email of the synthetic New User. Must be
 *                                  under an e2e-only TLD (see
 *                                  ``assertE2eOnlyEmail``).
 *  - AUTH_BASE_URL                 (optional) explicit HTTP(S) Keycloak server
 *                                  URL. When non-empty it is validated and used;
 *                                  otherwise the URL is derived from BASE_URL by
 *                                  prefixing the host with "auth." (e.g.
 *                                  https://staging.all-hands.dev →
 *                                  https://auth.staging.all-hands.dev). Set it for
 *                                  targets served under a subdomain (e.g. "app."),
 *                                  where the derivation is wrong.
 *
 *  The Keycloak server URL is derived from BASE_URL by prefixing the subdomain
 *  with "auth." (e.g. https://staging.all-hands.dev →
 *  https://auth.staging.all-hands.dev).
 *
 * Super admin (org management):
 *  - SUPER_ADMIN_API_KEY           API key of an instance-level superadmin,
 *                                  used by the org-management specs to create
 *                                  organizations and provision users directly
 *                                  via the REST API (outside the browser). The
 *                                  key must be unbound (no org binding) so the
 *                                  superadmin can target any org via the
 *                                  ``X-Org-Id`` header.
 *
 * Returning User (GitHub):
 *  - RETURNING_GITHUB_USERNAME
 *  - RETURNING_GITHUB_PASSWORD
 *  - RETURNING_GITHUB_TOTP_SECRET  (optional) 2FA secret.
 *
 * New User (synthetic Keycloak-native account):
 *  - KEYCLOAK_NEW_USER_USERNAME    **required to enable this role**; leave
 *                                  unset to skip the New User (and Keycloak
 *                                  cleanup + creation) entirely.
 *  - KEYCLOAK_NEW_USER_PASSWORD    password to set on the synthetic user and
 *                                  submit through Keycloak's local login form.
 *
 * Test fixtures (optional overrides):
 *  - TEST_REPO_URL                 repo used in conversations.
 *  - TEST_PROMPT                   prompt used in conversations.
 *  - TEST_ENV                      label for the environment.
 *
 * Auth behavior:
 *  - AUTH_METHOD                   "skip" to reuse existing storage-state
 *                                  files instead of logging in fresh. Each
 *                                  setup project only honors "skip" if its own
 *                                  storage-state file already exists.
 *  - SECONDARY_AUTH_STATE          path to a second storage-state file for
 *                                  isolation tests (mounted by Argo).
 */

export type RunUser = "returning" | "new-user";

export const DEFAULT_KEYCLOAK_REALM = "allhands";

/**
 * True when credentials are configured for the given user role.
 *
 * Each role is opt-in via its own username env var
 * (``RETURNING_GITHUB_USERNAME`` for the federated GitHub user,
 * ``KEYCLOAK_NEW_USER_USERNAME`` for the synthetic Keycloak-native user), so a
 * run can omit either role. When a role is disabled, its setup project skips
 * and the paired test projects match no specs, so the run stays green without
 * that role's credentials.
 */
export function isUserEnabled(user: RunUser): boolean {
  const envVar =
    user === "returning"
      ? "RETURNING_GITHUB_USERNAME"
      : "KEYCLOAK_NEW_USER_USERNAME";
  return Boolean(process.env[envVar]);
}

const fixturesDir = path.resolve(import.meta.dirname, "../fixtures");

/** Storage-state file produced by the Returning User setup project. */
export const authReturningFile = path.join(fixturesDir, "auth.returning.json");

/** Storage-state file produced by the New User setup project. */
export const authNewUserFile = path.join(fixturesDir, "auth.new-user.json");

/**
 * Backwards-compatible primary auth file. Maps to the Returning User, which is
 * the role a "normal" single-user run exercises.
 */
export const authPrimaryFile = authReturningFile;

export interface GitHubCredentials {
  username: string;
  password: string;
  totpSecret?: string;
}

export interface KeycloakAdminConfig {
  keycloakUrl: string;
  realm: string;
  username: string;
  password: string;
  newUserEmail: string;
}

function required(varName: string): string {
  const value = process.env[varName];
  if (!value) {
    throw new Error(
      `Missing required environment variable: ${varName}. See utils/config.ts for the full list.`,
    );
  }
  return value;
}

/**
 * Resolve and validate the GitHub credentials for the Returning User role.
 *
 * Only call this when the returning-user role is enabled (see
 * ``isUserEnabled``); it throws if the required env vars are missing, which
 * is the intended failure mode when a run claims to exercise the role
 * without supplying its credentials.
 *
 * The New User role does *not* use GitHub — see ``newUserCredentials``.
 */
export function returningGithubCredentials(): GitHubCredentials {
  const username = required("RETURNING_GITHUB_USERNAME");
  const password = required("RETURNING_GITHUB_PASSWORD");
  const totpSecret = process.env.RETURNING_GITHUB_TOTP_SECRET || undefined;
  return { username, password, totpSecret };
}

/**
 * Credentials for the synthetic Keycloak-native New User.
 *
 * The username and password are what the harness sets on the user via the
 * Keycloak Admin API and then submits through Keycloak's local login form.
 * The email is what the Admin API delete-and-recreate step keys on, and is
 * also what the app's ``provision-user`` endpoint uses to add the account to
 * an org (see ``006-org-management.spec.ts``).
 */
export interface NewUserCredentials {
  username: string;
  password: string;
  email: string;
}

/**
 * Resolve and validate the synthetic New User credentials.
 *
 * Only call this when the new-user role is enabled (see ``isUserEnabled``).
 * The email is validated against the e2e-only allowlist so a config typo
 * cannot point the harness at a namespace that overlaps with real users.
 */
export function newUserCredentials(): NewUserCredentials {
  const username = required("KEYCLOAK_NEW_USER_USERNAME");
  const password = required("KEYCLOAK_NEW_USER_PASSWORD");
  const email = required("KEYCLOAK_NEW_USER_EMAIL");
  assertE2eOnlyEmail(email);
  return { username, password, email };
}

/**
 * Resolve and validate the super-admin API key used by the org-management
 * specs to drive the REST API directly (outside the browser). The key must
 * belong to an instance-level superadmin and be unbound so it can target any
 * org via the ``X-Org-Id`` header.
 */
export function superAdminApiKey(): string {
  return required("SUPER_ADMIN_API_KEY");
}

/** Email of the New User (used by org-management to provision them into an org). */
export function newUserEmail(): string {
  const email = required("KEYCLOAK_NEW_USER_EMAIL");
  assertE2eOnlyEmail(email);
  return email;
}

/** Resolve and validate the Keycloak admin config used for New User cleanup. */
export function keycloakAdminConfig(): KeycloakAdminConfig {
  return {
    keycloakUrl: keycloakUrlFromBaseUrl(),
    realm: process.env.KEYCLOAK_REALM || DEFAULT_KEYCLOAK_REALM,
    username: required("KEYCLOAK_ADMIN_USERNAME"),
    password: required("KEYCLOAK_ADMIN_PASSWORD"),
    newUserEmail: required("KEYCLOAK_NEW_USER_EMAIL"),
  };
}

/**
 * Resolve the Keycloak server URL.
 *
 * Prefers an explicit AUTH_BASE_URL (the target's real auth host, e.g. resolved
 * from its published web-client config), falling back to deriving it from
 * BASE_URL by prefixing the host with "auth.".
 *
 * The derivation only holds when the app is served at the environment apex
 * (e.g. https://staging.all-hands.dev → https://auth.staging.all-hands.dev). It
 * is wrong when the app is served under a subdomain such as "app.", where the
 * auth host drops that label rather than gaining an "auth." prefix; for those
 * targets set AUTH_BASE_URL to the correct host.
 */
function keycloakUrlFromBaseUrl(): string {
  const explicit = process.env.AUTH_BASE_URL?.trim();
  if (explicit) {
    try {
      const url = new URL(explicit);
      if (url.protocol !== "http:" && url.protocol !== "https:") {
        throw new Error(`Unsupported protocol: ${url.protocol}`);
      }
      return url.toString().replace(/\/$/, "");
    } catch (error) {
      throw new Error("AUTH_BASE_URL must be a valid HTTP(S) URL.", {
        cause: error,
      });
    }
  }
  const baseUrl = process.env.BASE_URL;
  if (!baseUrl) {
    throw new Error("BASE_URL is required to derive the Keycloak URL.");
  }
  const url = new URL(baseUrl);
  return `${url.protocol}//auth.${url.host}`;
}

/** Storage-state file path for a given user role. */
export function authFileFor(user: RunUser): string {
  return user === "returning" ? authReturningFile : authNewUserFile;
}

/**
 * Read the user role a test is running under. Prefers Playwright project
 * metadata (`project.metadata.user`); falls back to the AUTH_RUN_USER env var
 * for ad-hoc runs of a single spec.
 */
export function runUser(
  testInfo: { project: { metadata?: Record<string, unknown> } } | undefined,
): RunUser {
  const fromMeta = testInfo?.project?.metadata?.user as string | undefined;
  const value = fromMeta || process.env.AUTH_RUN_USER || "returning";
  if (value !== "returning" && value !== "new-user") {
    throw new Error(
      `Unknown run user "${value}". Expected "returning" or "new-user".`,
    );
  }
  return value as RunUser;
}

/** When true, setup projects reuse existing storage-state files if present. */
export function skipAuth(): boolean {
  return process.env.AUTH_METHOD === "skip";
}

/**
 * Allow-list pattern for e2e-only email addresses. ``.test`` is reserved by
 * RFC 2606 and can never resolve to a real mailbox, so any address matching
 * this pattern is provably outside any customer or employee namespace.
 *
 * The synthetic New User's email must match this pattern; both the delete-
 * by-email and create-user paths in ``keycloak-admin.ts`` assert on it before
 * hitting the Keycloak Admin API. This is defense-in-depth against a
 * ``KEYCLOAK_NEW_USER_EMAIL`` typo pointing the harness at a real account.
 */
export const E2E_EMAIL_PATTERN = /^[^@\s]+@([a-z0-9-]+\.)*e2e\.test$/i;

/**
 * Throw if the given email is not under the e2e-only ``.test`` TLD.
 *
 * This guard runs before any destructive Keycloak Admin API call keyed on
 * email (delete-by-email, create-user).
 */
export function assertE2eOnlyEmail(email: string): void {
  if (!E2E_EMAIL_PATTERN.test(email)) {
    throw new Error(
      `Refusing to use "${email}" as an e2e user address: expected an ` +
        `address under the reserved .e2e.test namespace (see E2E_EMAIL_PATTERN). ` +
        `The synthetic new-user cleanup and creation deliberately reject any ` +
        `namespace that could overlap with real users.`,
    );
  }
}

/**
 * Throw if the run is targeting the production environment.
 *
 * The New User setup deletes and recreates a user in the target Keycloak
 * realm. That is only safe against non-production targets; production
 * customer data must never be touched. Call from every setup project that
 * performs realm-level writes.
 */
export function assertNotProduction(): void {
  if (isEnvironment("production")) {
    throw new Error(
      "Refusing to run destructive Keycloak setup against production " +
        `(BASE_URL=${process.env.BASE_URL}). This setup deletes and recreates ` +
        "realm users and is intended for staging / disposable clusters only.",
    );
  }
}

/** Shared, non-secret test fixture values with sensible defaults. */
export const env = {
  baseUrl: process.env.BASE_URL || "",
  testEnv: process.env.TEST_ENV || "staging",
  testRepoUrl:
    process.env.TEST_REPO_URL || "https://github.com/OpenHands/deploy",
  testPrompt: process.env.TEST_PROMPT || "Flip a coin!",
  isCI: process.env.CI === "true",

  getFeatureBranchUrl(branchName: string): string {
    const sanitized = branchName.replace(/[^a-zA-Z0-9-]/g, "-").toLowerCase();
    return `https://${sanitized}.staging.all-hands.dev`;
  },
};

/** True when BASE_URL points at the named environment. */
export function isEnvironment(
  target: "staging" | "production" | "local",
): boolean {
  const baseUrl = process.env.BASE_URL || "";
  switch (target) {
    case "staging":
      return baseUrl.includes("staging.all-hands.dev");
    case "production":
      return baseUrl.includes("app.all-hands.dev");
    case "local":
      return baseUrl.includes("localhost");
    default:
      return false;
  }
}

/** Skip the current test in the named environments. */
export function skipInEnvironment(
  test: { skip: (condition: boolean, message: string) => void },
  envs: ("staging" | "production" | "local")[],
  reason: string,
): void {
  const shouldSkip = envs.some(isEnvironment);
  test.skip(shouldSkip, `Skipped in ${envs.join(", ")}: ${reason}`);
}
