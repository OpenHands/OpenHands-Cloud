import { expect, test, type Page } from "@playwright/test";

import { loginWithKeycloakPassword } from "../utils/auth-helpers";

/**
 * Unit tests for loginWithKeycloakPassword.
 *
 * The helper drives Keycloak's native username/password Sign-In form for the
 * synthetic new-user role. Two behaviors matter and are easy to regress:
 *
 *  1. It intercepts the request to Keycloak's ``/protocol/openid-connect/auth``
 *     endpoint and strips ``kc_idp_hint`` before it reaches the server, so
 *     Keycloak renders its local form instead of 302-ing to GitHub. If this
 *     interception ever broke, the helper would silently fall back to real
 *     OAuth against github.com — the exact fragility this migration removes.
 *  2. It submits the ``#username`` / ``#password`` / ``#kc-login`` form.
 *
 * We fake the app's login page and Keycloak's authorize endpoint with
 * ``page.route`` fixtures and verify both behaviors on a single run.
 */

const APP_ORIGIN = "https://app.kc-login-test.local";
const APP_LOGIN_URL = `${APP_ORIGIN}/login`;
const KC_AUTHORIZE_URL =
  "https://auth.kc-login-test.local/realms/allhands/protocol/openid-connect/auth";
const KC_LOGIN_ACTION_URL =
  "https://auth.kc-login-test.local/realms/allhands/login-actions/authenticate?session_code=fake";

/** Minimal app login page: one button whose click navigates to Keycloak's
 * authorize endpoint with ``kc_idp_hint=github`` — exactly what the real app's
 * "Log in with GitHub" button does. */
const appLoginHtml = `<!doctype html><html><body>
  <button type="button" onclick="window.location.href='${KC_AUTHORIZE_URL}?client_id=allhands&kc_idp_hint=github&response_type=code&redirect_uri=${encodeURIComponent(
    `${APP_ORIGIN}/oauth/keycloak/callback`,
  )}&state=s&scope=openid'">Log in with GitHub</button>
</body></html>`;

/** Minimal Keycloak login form using the real element ids. */
const kcLoginHtml = `<!doctype html><html><body>
  <form id="kc-form-login" action="${KC_LOGIN_ACTION_URL}" method="post">
    <input id="username" name="username" type="text" />
    <input id="password" name="password" type="password" />
    <button id="kc-login" name="login" type="submit">Sign In</button>
  </form>
</body></html>`;

/** Fake the Keycloak GitHub-redirect that would occur if the hint were not
 * stripped: 302 straight to github.com. If ``kc_idp_hint`` reaches the server
 * the test lands here and both assertions fail loudly. */
const kcGithubRedirectHtml = `<!doctype html><html><body>
  <h1>REGRESSION: kc_idp_hint was not stripped, Keycloak forwarded to GitHub</h1>
</body></html>`;

async function routeKeycloak(page: Page): Promise<{
  submittedForm: () => URLSearchParams | undefined;
}> {
  await page.route(`${APP_LOGIN_URL}**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: appLoginHtml,
    }),
  );

  // Serve either the native form or the "regression" page depending on
  // whether the request still carries kc_idp_hint at the point it reaches
  // this handler. The helper's own page.route runs first (Playwright routes
  // are LIFO), so if it did its job the hint will be gone by now.
  await page.route(`${KC_AUTHORIZE_URL}**`, (route) => {
    const url = new URL(route.request().url());
    const body = url.searchParams.has("kc_idp_hint")
      ? kcGithubRedirectHtml
      : kcLoginHtml;
    return route.fulfill({ status: 200, contentType: "text/html", body });
  });

  let submitted: URLSearchParams | undefined;
  await page.route(`${KC_LOGIN_ACTION_URL}**`, (route) => {
    submitted = new URLSearchParams(route.request().postData() ?? "");
    return route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><html><body><h1>Logged in</h1></body></html>",
    });
  });

  return { submittedForm: () => submitted };
}

test("strips kc_idp_hint and submits the native Keycloak form", async ({
  page,
}) => {
  const { submittedForm } = await routeKeycloak(page);
  await page.goto(APP_LOGIN_URL);

  await loginWithKeycloakPassword(page, {
    username: "e2e-new-user",
    password: "hunter2",
    email: "e2e-new-user@e2e.test",
  });

  // The helper only reaches the form-submit step if it landed on the native
  // Sign-In form (the "regression" page has no ``#username`` input), so a
  // captured POST to the form action proves both the strip-hint and
  // fill-and-submit behaviors.
  const form = submittedForm();
  expect(form).toBeDefined();
  expect(form?.get("username")).toBe("e2e-new-user");
  expect(form?.get("password")).toBe("hunter2");
});
