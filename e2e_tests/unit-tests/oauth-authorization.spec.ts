import { expect, test, type Page } from "@playwright/test";

import { handleOAuthAuthorization } from "../utils/auth-helpers";

/**
 * Unit tests for handleOAuthAuthorization.
 *
 * These drive the real helper against a simulated GitHub OAuth flow served
 * entirely from route interception (no network), so they exercise the actual
 * retry/navigation logic rather than a mock of it.
 *
 * The scenario mirrors the new-user regression: GitHub renders the first-time
 * consent page, the harness submits the authorize form, and GitHub redirects
 * back to the app. The helper must keep retrying until the page actually
 * leaves the authorize URL — a missing form is a retryable state, not proof
 * of success.
 */

const AUTHORIZE_URL = "https://github.com/login/oauth/authorize?client_id=test";
const APP_URL = "https://app.oauth-test.local/";

/** HTML for GitHub's authorize page, with an optional delay before the form. */
function authorizePage(formDelayMs = 0): string {
  const injectForm = `
    var form = document.createElement('form');
    form.method = 'POST';
    form.action = '/login/oauth/authorize';
    var btn = document.createElement('button');
    btn.name = 'authorize';
    btn.value = '1';
    btn.textContent = 'Authorize';
    form.appendChild(btn);
    document.body.appendChild(form);
  `;
  const script =
    formDelayMs > 0
      ? `setTimeout(function () { ${injectForm} }, ${formDelayMs});`
      : injectForm;
  return `<!doctype html><html><body><h1>Authorize application</h1><script>${script}</script></body></html>`;
}

/**
 * Route github.com and the fake app so the OAuth flow runs offline.
 *
 * @param authorizeHtml HTML served for the authorize GET.
 * @param grant When true, the authorize POST 302-redirects to the app (grant
 *   succeeds); when false it re-serves the authorize page (grant never
 *   completes), reproducing the stuck new-user state.
 */
async function routeOAuthFlow(
  page: Page,
  authorizeHtml: string,
  grant: boolean,
): Promise<void> {
  await page.route("https://github.com/login/oauth/authorize**", (route) => {
    // The authorize form POSTs back to this URL. On a successful grant GitHub
    // redirects to the app; simulate that with a client-side redirect (a
    // route-fulfilled 302 on a POST navigation renders as a Chrome error
    // page, so a JS redirect models the "left the authorize URL" transition
    // more faithfully). On a failed grant, re-serve the authorize page so the
    // helper stays stuck — reproducing the new-user regression.
    if (route.request().method() === "POST" && grant) {
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: `<!doctype html><html><body><script>window.location.replace(${JSON.stringify(
          APP_URL,
        )});</script></body></html>`,
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "text/html",
      body: authorizeHtml,
    });
  });

  await page.route(`${APP_URL}**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><html><body><h1>App home</h1></body></html>",
    }),
  );
}

test("grants access on the first-time consent page", async ({ page }) => {
  await routeOAuthFlow(page, authorizePage(), true);
  await page.goto(AUTHORIZE_URL);

  await handleOAuthAuthorization(page);

  expect(page.url()).toBe(APP_URL);
});

test("retries when the consent form renders after the URL settles", async ({
  page,
}) => {
  // Form appears 500ms late: the first submit attempt sees no form and must
  // retry rather than assume the page already redirected.
  await routeOAuthFlow(page, authorizePage(500), true);
  await page.goto(AUTHORIZE_URL);

  await handleOAuthAuthorization(page);

  expect(page.url()).toBe(APP_URL);
});

test("returns without error when the app was already authorized", async ({
  page,
}) => {
  // No authorize page is shown — GitHub redirected straight back to the app.
  await routeOAuthFlow(page, authorizePage(), true);
  await page.goto(APP_URL);

  await handleOAuthAuthorization(page);

  expect(page.url()).toBe(APP_URL);
});

test("throws instead of silently passing when the grant never completes", async ({
  page,
}) => {
  // Authorize page never has a submittable form and never redirects: the
  // helper must fail loudly rather than return as a false "bypass".
  const noFormPage =
    "<!doctype html><html><body><h1>Authorize application</h1></body></html>";
  await routeOAuthFlow(page, noFormPage, false);
  await page.goto(AUTHORIZE_URL);

  await expect(
    handleOAuthAuthorization(page, { timeoutMs: 2_000 }),
  ).rejects.toThrow(/OAuth authorization did not complete/);
});
