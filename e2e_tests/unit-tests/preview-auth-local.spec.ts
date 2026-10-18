import http from "http";
import type { Server } from "http";

import { expect, test } from "@playwright/test";

import {
  loginToPreviewOrigin,
  resolvePreviewAuthOrigins,
  type PreviewLoginCredentials,
} from "../utils/preview-auth";

/**
 * Local real-HTTP fixture for the preview login helper tests.
 *
 * Serves a tiny app + Keycloak login surface over a real loopback HTTP server
 * (no external hosts, no browser-network interception beyond Playwright's
 * browser network stack, which talks to the real local server).
 */

const submitted: URLSearchParams[] = [];

function startLocalAuthServer(formAction?: string): Promise<{
  server: Server;
  port: number;
  appOrigin: string;
  stop: () => Promise<void>;
}> {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    if (req.method === "GET" && url.pathname === "/") {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(`<!doctype html><html><body>
        <button type="button" id="login" onclick="window.location.href='/realms/allhands/protocol/openid-connect/auth?client_id=allhands&kc_idp_hint=github&response_type=code&redirect_uri=http%3A%2F%2Flocalhost%2Foauth%2Fkeycloak%2Fcallback&state=localstate&scope=openid'">Log in with GitHub</button>
      </body></html>`);
      return;
    }
    if (
      req.method === "GET" &&
      url.pathname === "/realms/allhands/protocol/openid-connect/auth"
    ) {
      const wantsSocial = url.searchParams.has("kc_idp_hint");
      if (wantsSocial) {
        res.writeHead(302, {
          Location:
            "https://github.com/login/oauth/authorize?client_id=allhands",
        });
        res.end();
        return;
      }
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(`<!doctype html><html><body>
        <form id="kc-form-login" action="${formAction ?? "/realms/allhands/protocol/openid-connect/auth?client_id=allhands"}" method="post">
          <input id="username" name="username" type="text" />
          <input id="password" name="password" type="password" />
          <button id="kc-login" name="login" type="submit">Sign In</button>
        </form>
      </body></html>`);
      return;
    }
    if (
      req.method === "POST" &&
      url.pathname === "/realms/allhands/protocol/openid-connect/auth"
    ) {
      let body = "";
      req.on("data", (chunk) => {
        body += chunk;
      });
      req.on("end", () => {
        submitted.push(new URLSearchParams(body));
        res.writeHead(302, { Location: "/oauth/keycloak/callback?code=local" });
        res.end();
      });
      return;
    }
    if (req.method === "GET" && url.pathname === "/oauth/keycloak/callback") {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(`<!doctype html><html><body><h1>Logged in</h1></body></html>`);
      return;
    }
    res.writeHead(404);
    res.end();
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      resolve({
        server,
        port: typeof address === "object" && address ? address.port : 0,
        appOrigin: `http://127.0.0.1:${typeof address === "object" && address ? address.port : 0}`,
        stop: () =>
          new Promise((done) => {
            server.close(() => done());
          }),
      });
    });
  });
}

test.afterEach(async () => {
  await activeServer?.stop();
});

let activeServer: Awaited<ReturnType<typeof startLocalAuthServer>> | null =
  null;

test("loginToPreviewOrigin drives a real local HTTP keycloak login", async ({
  page,
}) => {
  const fixture = await startLocalAuthServer();
  activeServer = fixture;
  submitted.length = 0;
  const creds: PreviewLoginCredentials = {
    username: "e2e-preview-user",
    password: "preview-password",
    email: "e2e-preview-user@e2e.test",
  };

  await loginToPreviewOrigin(page, fixture.appOrigin, creds, [
    fixture.appOrigin,
  ]);

  expect(submitted).toHaveLength(1);
  expect(submitted[0].get("username")).toBe("e2e-preview-user@e2e.test");
  expect(submitted[0].get("password")).toBe("preview-password");
  expect(page.url()).toContain("/oauth/keycloak/callback");
});

test("loginToPreviewOrigin refuses a base url outside the preview allowlist", async ({
  page,
}) => {
  await expect(
    loginToPreviewOrigin(page, "https://app.all-hands.dev", {
      username: "u",
      password: "p",
      email: "e2e-preview-user@e2e.test",
    }),
  ).rejects.toThrow(/not an approved preview origin/);
});

test("resolvePreviewAuthOrigins only accepts pr-N staging hosts", () => {
  expect(
    resolvePreviewAuthOrigins("https://pr-12.staging.all-hands.dev"),
  ).toEqual([
    "https://pr-12.staging.all-hands.dev",
    "https://pr-12.auth.staging.all-hands.dev",
  ]);
  expect(resolvePreviewAuthOrigins("https://staging.all-hands.dev")).toEqual(
    [],
  );
  expect(resolvePreviewAuthOrigins("https://app.all-hands.dev")).toEqual([]);
});

test("blocks a cross-origin credential form without submitting the password", async ({
  page,
}) => {
  const fixture = await startLocalAuthServer("http://127.0.0.1:9/steal");
  activeServer = fixture;
  submitted.length = 0;
  await expect(
    loginToPreviewOrigin(
      page,
      fixture.appOrigin,
      {
        username: "test",
        email: "test@e2e.test",
        password: "test-only-password",
      },
      [fixture.appOrigin],
    ),
  ).rejects.toThrow();
  expect(submitted).toHaveLength(0);
});
