import http from "http";

import { expect, test, type Page } from "@playwright/test";

import { loginWithKeycloakPassword } from "../utils/auth-helpers";

/**
 * Unit tests for loginWithKeycloakPassword.
 *
 * The helper drives Keycloak's native username/password Sign-In form for the
 * synthetic new-user role. Two behaviors matter and are easy to regress:
 *
 *  1. It strips ``kc_idp_hint`` before Keycloak sees it, so Keycloak renders
 *     its local form instead of 302-ing to GitHub. If this ever broke, the
 *     helper would silently fall back to real OAuth against github.com — the
 *     exact fragility this migration removes. Where the hint is carried
 *     depends on the login flow: the legacy button navigates straight to
 *     Keycloak, while the OAuth V2 link reaches it through a redirect from the
 *     app's ``/oauth/{id}/login``.
 *  2. It submits the ``#username`` / ``#password`` / ``#kc-login`` form.
 *
 * The legacy tests fake the app and Keycloak with ``page.route`` fixtures. The
 * OAuth V2 test needs a real loopback server: the helper reads the app's
 * redirect with ``route.fetch``, which goes to the network.
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

/** Keycloak's Sign-In form re-rendered with the invalid-credentials error,
 * served under ``/realms/`` like the real server so the helper's failure path
 * (error shown, no redirect away) is exercised. */
const kcLoginErrorHtml = `<!doctype html><html><body>
  <span id="input-error">Invalid username or password.</span>
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
    // 302 off ``/realms/`` like real Keycloak so the helper's success signal
    // (leaving ``/realms/``) fires instead of the request hanging.
    return route.fulfill({
      status: 302,
      headers: { location: `${APP_ORIGIN}/oauth/keycloak/callback?code=fake` },
      body: "",
    });
  });

  await page.route(`${APP_ORIGIN}/oauth/keycloak/callback**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><html><body><h1>Logged in</h1></body></html>",
    }),
  );

  return { submittedForm: () => submitted };
}

test("legacy: strips kc_idp_hint and submits the native Keycloak form", async ({
  page,
}) => {
  const { submittedForm } = await routeKeycloak(page);
  await page.goto(APP_LOGIN_URL);

  await loginWithKeycloakPassword(
    page,
    {
      username: "e2e-new-user",
      password: "hunter2",
      email: "e2e-new-user@e2e.test",
    },
    "legacy",
  );

  // The helper only reaches the form-submit step if it landed on the native
  // Sign-In form (the "regression" page has no ``#username`` input), so a
  // captured POST to the form action proves both the strip-hint and
  // fill-and-submit behaviors.
  const form = submittedForm();
  expect(form).toBeDefined();
  // Email-as-username realm: the login identifier is the email, so the helper
  // must submit ``creds.email`` here, not the separate ``creds.username``.
  expect(form?.get("username")).toBe("e2e-new-user@e2e.test");
  expect(form?.get("password")).toBe("hunter2");
});

test("throws a clear error when Keycloak rejects the credentials", async ({
  page,
}) => {
  await page.route(`${APP_LOGIN_URL}**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: appLoginHtml,
    }),
  );
  await page.route(`${KC_AUTHORIZE_URL}**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: kcLoginHtml,
    }),
  );
  await page.route(`${KC_LOGIN_ACTION_URL}**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: kcLoginErrorHtml,
    }),
  );

  await page.goto(APP_LOGIN_URL);

  // Must throw on the visible error, not return and let the caller burn 60s.
  await expect(
    loginWithKeycloakPassword(
      page,
      {
        username: "e2e-new-user",
        password: "wrong-password",
        email: "e2e-new-user@e2e.test",
      },
      "legacy",
    ),
  ).rejects.toThrow(/invalid username or password/i);
});

interface OAuthV2Server {
  origin: string;
  keycloakAuthQueries: URLSearchParams[];
  submittedForms: URLSearchParams[];
  stop: () => Promise<void>;
}

/**
 * The OAuth V2 login surface: the app's login link goes to
 * ``/oauth/3/login``, which 307s to Keycloak with ``kc_idp_hint=github`` like
 * the real app, and Keycloak returns to ``/oauth/3/callback``.
 */
async function startOAuthV2Server(): Promise<OAuthV2Server> {
  const keycloakAuthQueries: URLSearchParams[] = [];
  const submittedForms: URLSearchParams[] = [];
  let origin = "";
  const keycloakAuthPath = "/realms/allhands/protocol/openid-connect/auth";
  const keycloakLoginActionPath = "/realms/allhands/login-actions/authenticate";

  const server = http.createServer((req, res) => {
    const url = new URL(req.url ?? "/", origin);
    const html = (body: string) => {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(`<!doctype html><html><body>${body}</body></html>`);
    };

    if (req.method === "GET" && url.pathname === "/") {
      html('<a href="/oauth/3/login">Log in with GitHub</a>');
    } else if (req.method === "GET" && url.pathname === "/oauth/3/login") {
      const target = new URL(`${origin}${keycloakAuthPath}`);
      target.search = new URLSearchParams({
        kc_idp_hint: "github",
        client_id: "allhands",
        redirect_uri: `${origin}/oauth/3/callback`,
        response_type: "code",
        state: "encrypted-state",
      }).toString();
      res.writeHead(307, { Location: target.toString() });
      res.end();
    } else if (req.method === "GET" && url.pathname === keycloakAuthPath) {
      keycloakAuthQueries.push(url.searchParams);
      if (url.searchParams.has("kc_idp_hint")) {
        res.writeHead(302, { Location: "/github-regression" });
        res.end();
        return;
      }
      html(`<form action="${keycloakLoginActionPath}" method="post">
        <input id="username" name="username" type="text" />
        <input id="password" name="password" type="password" />
        <button id="kc-login" name="login" type="submit">Sign In</button>
      </form>`);
    } else if (
      req.method === "POST" &&
      url.pathname === keycloakLoginActionPath
    ) {
      let body = "";
      req.on("data", (chunk) => {
        body += chunk;
      });
      req.on("end", () => {
        submittedForms.push(new URLSearchParams(body));
        res.writeHead(302, {
          Location: "/oauth/3/callback?code=fake&state=encrypted-state",
        });
        res.end();
      });
    } else if (req.method === "GET" && url.pathname === "/github-regression") {
      html("<h1>REGRESSION: kc_idp_hint reached Keycloak</h1>");
    } else if (req.method === "GET" && url.pathname === "/oauth/3/callback") {
      html("<h1>Logged in</h1>");
    } else {
      res.writeHead(404);
      res.end();
    }
  });

  await new Promise<void>((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("OAuth V2 fixture server has no TCP address");
  }
  origin = `http://127.0.0.1:${address.port}`;
  return {
    origin,
    keycloakAuthQueries,
    submittedForms,
    stop: () =>
      new Promise((resolve) => {
        server.close(() => resolve());
      }),
  };
}

test("oauth-v2: strips kc_idp_hint from the app's /oauth/{id}/login redirect", async ({
  page,
}) => {
  const server = await startOAuthV2Server();
  try {
    await page.goto(server.origin);

    await loginWithKeycloakPassword(
      page,
      {
        username: "e2e-new-user",
        password: "hunter2",
        email: "e2e-new-user@e2e.test",
      },
      "oauth-v2",
    );

    expect(server.keycloakAuthQueries).toHaveLength(1);
    const query = server.keycloakAuthQueries[0];
    expect(query.has("kc_idp_hint")).toBe(false);
    expect(query.get("state")).toBe("encrypted-state");
    expect(query.get("redirect_uri")).toBe(`${server.origin}/oauth/3/callback`);

    expect(server.submittedForms).toHaveLength(1);
    expect(server.submittedForms[0].get("username")).toBe(
      "e2e-new-user@e2e.test",
    );
    expect(server.submittedForms[0].get("password")).toBe("hunter2");
    expect(new URL(page.url()).pathname).toBe("/oauth/3/callback");
  } finally {
    await server.stop();
  }
});
