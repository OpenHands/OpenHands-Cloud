import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

import {
  completeLoginAndOnboard,
  loginFlow,
  loginWithKeycloakPassword,
  type LoginFlow,
} from "../utils/auth-helpers";
import { fetchWebClientConfig } from "../utils/billing";
import { newUserCredentials, runUser } from "../utils/config";

/**
 * Login page and sign-in round trip, once per login flow.
 *
 * The app has two login flows, chosen by `feature_flags.enable_oauth_v2_login`
 * (see `LoginFlow` in `utils/auth-helpers.ts`). Each describe below covers one
 * and skips on an instance running the other, so a run's report shows which
 * flow it exercised.
 *
 * Both sign in from a signed-out browser as the New User through Keycloak's
 * native form, so GitHub is not involved. The setup project has already
 * onboarded that user, so sign-in lands on the home screen.
 */

interface IdpProvider {
  id: number;
  title: string;
}

const GITHUB_LOGIN = "Log in with GitHub";

test.use({ storageState: { cookies: [], origins: [] } });

async function skipUnlessFlow(
  request: APIRequestContext,
  flow: LoginFlow,
): Promise<void> {
  const actual = loginFlow(await fetchWebClientConfig(request));
  test.skip(
    actual !== flow,
    `the instance uses the ${actual} login flow (feature_flags.enable_oauth_v2_login)`,
  );
}

/** Paths of the app-origin top-level navigations, redirect hops included. */
function recordAppNavigations(page: Page, appOrigin: string): string[] {
  const paths: string[] = [];
  page.on("request", (request) => {
    if (
      !request.isNavigationRequest() ||
      request.frame() !== page.mainFrame()
    ) {
      return;
    }
    const url = new URL(request.url());
    if (url.origin === appOrigin) {
      paths.push(url.pathname);
    }
  });
  return paths;
}

test.describe("legacy login @login @oauth-v1", () => {
  test.beforeEach(async ({ request }, testInfo) => {
    test.skip(runUser(testInfo) !== "new-user", "new-user role only");
    await skipUnlessFlow(request, "legacy");
  });

  test("GitHub button goes straight to Keycloak and signs in", async ({
    page,
  }) => {
    const creds = newUserCredentials();
    await page.goto("/");
    await expect(page.getByRole("button", { name: GITHUB_LOGIN })).toBeVisible({
      timeout: 30_000,
    });

    const appOrigin = new URL(page.url()).origin;
    const keycloakAuth = page.waitForRequest((request) =>
      /\/realms\/[^/]+\/protocol\/openid-connect\/auth$/.test(
        new URL(request.url()).pathname,
      ),
    );
    const appPaths = recordAppNavigations(page, appOrigin);

    await loginWithKeycloakPassword(page, creds, "legacy");
    await completeLoginAndOnboard(page, creds.username);

    const authUrl = new URL((await keycloakAuth).url());
    expect(authUrl.searchParams.get("redirect_uri")).toBe(
      `${appOrigin}/oauth/keycloak/callback`,
    );
    expect(appPaths).toContain("/oauth/keycloak/callback");
  });
});

test.describe("OAuth V2 login @login @oauth-v2", () => {
  test.beforeEach(async ({ request }, testInfo) => {
    test.skip(runUser(testInfo) !== "new-user", "new-user role only");
    await skipUnlessFlow(request, "oauth-v2");
  });

  test("lists each login provider as a link to /oauth/{id}/login", async ({
    page,
  }) => {
    const response = await page.request.get("/oauth/idp-providers");
    expect(response.ok()).toBe(true);
    const providers = (await response.json()) as IdpProvider[];
    expect(providers.length).toBeGreaterThan(0);

    await page.goto("/");
    for (const provider of providers) {
      const option = page.getByTestId(`v2-idp-provider-${provider.id}`);
      await expect(option).toBeVisible({ timeout: 30_000 });
      await expect(option).toHaveText(provider.title);
      await expect(option).toHaveAttribute(
        "href",
        new RegExp(`^/oauth/${provider.id}/login(\\?|$)`),
      );
    }
    await expect(page.getByRole("button", { name: GITHUB_LOGIN })).toHaveCount(
      0,
    );
  });

  test("signs in through /oauth/{id}/login and /oauth/{id}/callback", async ({
    page,
  }) => {
    const creds = newUserCredentials();
    await page.goto("/");
    const option = page.getByRole("link", { name: GITHUB_LOGIN });
    await expect(option).toBeVisible({ timeout: 30_000 });
    const href = (await option.getAttribute("href")) ?? "";
    const providerId = /^\/oauth\/([^/?]+)\/login/.exec(href)?.[1];
    expect(providerId, `GitHub login link href: "${href}"`).toBeDefined();

    const appPaths = recordAppNavigations(page, new URL(page.url()).origin);

    await loginWithKeycloakPassword(page, creds, "oauth-v2");
    await completeLoginAndOnboard(page, creds.username);

    expect(appPaths).toContain(`/oauth/${providerId}/login`);
    expect(appPaths).toContain(`/oauth/${providerId}/callback`);
  });
});
