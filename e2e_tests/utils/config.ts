import path from "path";

/**
 * Centralized configuration for the OpenHands Cloud e2e harness.
 *
 * The harness authenticates against Keycloak, which federates identities from
 * GitHub. A single test run exercises the same specs under two user roles:
 *
 *  - "returning" — a GitHub user whose OpenHands account already exists.
 *  - "new-user"  — a GitHub user whose OpenHands account is deleted at the
 *                  start of the run by the Keycloak admin, so they get a fresh
 *                  account (and a fresh user id) on next login.
 *
 * Three credential sets are therefore required:
 *
 *  1. Keycloak admin (username + password) — used to delete the New User by
 *     email before the New User logs in.
 *  2. Returning User (GitHub username + password + optional TOTP secret).
 *  3. New User (GitHub username + password + optional TOTP secret).
 *
 * Environment variables
 * ---------------------
 * Deployment:
 *  - BASE_URL                      (required) release environment under test.
 *
 * Keycloak admin (cleanup):
 *  - KEYCLOAK_REALM                realm to administer (default: "allhands").
 *  - KEYCLOAK_ADMIN_USERNAME       admin username.
 *  - KEYCLOAK_ADMIN_PASSWORD       admin password.
 *  - KEYCLOAK_NEW_USER_EMAIL       email of the New User to delete.
 *
 *  The Keycloak server URL is derived from BASE_URL by prefixing the subdomain
 *  with "auth." (e.g. https://staging.all-hands.dev →
 *  https://auth.staging.all-hands.dev).
 *
 * Returning User (GitHub):
 *  - RETURNING_GITHUB_USERNAME
 *  - RETURNING_GITHUB_PASSWORD
 *  - RETURNING_GITHUB_TOTP_SECRET  (optional) 2FA secret.
 *
 * New User (GitHub):
 *  - NEW_GITHUB_USERNAME
 *  - NEW_GITHUB_PASSWORD
 *  - NEW_GITHUB_TOTP_SECRET        (optional) 2FA secret.
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

/** Resolve and validate the GitHub credentials for a given user role. */
export function githubCredentialsFor(user: RunUser): GitHubCredentials {
  const prefix = user === "returning" ? "RETURNING_GITHUB" : "NEW_GITHUB";
  const username = required(`${prefix}_USERNAME`);
  const password = required(`${prefix}_PASSWORD`);
  const totpSecret = process.env[`${prefix}_TOTP_SECRET`] || undefined;
  return { username, password, totpSecret };
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
 * Derive the Keycloak URL from BASE_URL by prefixing the subdomain with "auth.".
 * e.g. https://staging.all-hands.dev → https://auth.staging.all-hands.dev
 */
function keycloakUrlFromBaseUrl(): string {
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
