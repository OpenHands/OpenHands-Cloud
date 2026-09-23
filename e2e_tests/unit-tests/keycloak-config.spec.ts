import { expect, test } from "@playwright/test";

import { keycloakAdminConfig } from "../utils/config";

const configEnvKeys = [
  "AUTH_BASE_URL",
  "BASE_URL",
  "KEYCLOAK_ADMIN_USERNAME",
  "KEYCLOAK_ADMIN_PASSWORD",
  "KEYCLOAK_NEW_USER_EMAIL",
] as const;

function withConfigEnv(
  overrides: Partial<Record<(typeof configEnvKeys)[number], string>>,
  run: () => void,
): void {
  const original = Object.fromEntries(
    configEnvKeys.map((key) => [key, process.env[key]]),
  );

  try {
    configEnvKeys.forEach((key) => delete process.env[key]);
    Object.assign(process.env, {
      KEYCLOAK_ADMIN_USERNAME: "admin",
      KEYCLOAK_ADMIN_PASSWORD: "password",
      KEYCLOAK_NEW_USER_EMAIL: "new-user@example.com",
      ...overrides,
    });
    run();
  } finally {
    configEnvKeys.forEach((key) => {
      const value = original[key];
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    });
  }
}

test("uses the derived Keycloak URL when AUTH_BASE_URL is empty", () => {
  withConfigEnv(
    {
      AUTH_BASE_URL: "   ",
      BASE_URL: "https://staging.all-hands.dev",
    },
    () => {
      expect(keycloakAdminConfig().keycloakUrl).toBe(
        "https://auth.staging.all-hands.dev",
      );
    },
  );
});

test("uses the derived Keycloak URL when AUTH_BASE_URL is unset", () => {
  withConfigEnv({ BASE_URL: "https://staging.all-hands.dev" }, () => {
    expect(keycloakAdminConfig().keycloakUrl).toBe(
      "https://auth.staging.all-hands.dev",
    );
  });
});

test("normalizes an explicit AUTH_BASE_URL", () => {
  withConfigEnv({ AUTH_BASE_URL: "  https://auth.example.com/  " }, () => {
    expect(keycloakAdminConfig().keycloakUrl).toBe("https://auth.example.com");
  });
});

test("accepts an explicit HTTP AUTH_BASE_URL", () => {
  withConfigEnv({ AUTH_BASE_URL: "http://localhost:8080/" }, () => {
    expect(keycloakAdminConfig().keycloakUrl).toBe("http://localhost:8080");
  });
});

test("rejects a malformed AUTH_BASE_URL", () => {
  withConfigEnv({ AUTH_BASE_URL: "not a URL" }, () => {
    expect(() => keycloakAdminConfig()).toThrow(
      "AUTH_BASE_URL must be a valid HTTP(S) URL.",
    );
  });
});

test("rejects a non-HTTP AUTH_BASE_URL", () => {
  withConfigEnv({ AUTH_BASE_URL: "ftp://auth.example.com" }, () => {
    expect(() => keycloakAdminConfig()).toThrow(
      "AUTH_BASE_URL must be a valid HTTP(S) URL.",
    );
  });
});
