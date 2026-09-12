import { expect, test, type Page } from "@playwright/test";

import { dismissTwoFactorReminder } from "../utils/auth-helpers";

/**
 * Unit tests for dismissTwoFactorReminder.
 *
 * GitHub interrupts the login flow with a one-time "Verify your two-factor
 * authentication (2FA) settings" reminder (served at /sessions/two-factor)
 * that sits between 2FA entry and the OAuth authorize page. These tests drive
 * the real helper against a route-intercepted fixture of that page.
 */

const REMINDER_URL = "https://github.com/sessions/two-factor";
const RESUMED_URL = "https://github.com/login/oauth/authorize?client_id=test";

/** Fixture of GitHub's "verify your 2FA settings" reminder page. */
const reminderHtml = `<!doctype html><html><body>
  <h2>Verify your two-factor authentication (2FA) settings</h2>
  <button type="button">Verify 2FA now</button>
  <button type="button" onclick="window.location.replace('${RESUMED_URL}')">skip 2FA verification</button>
</body></html>`;

async function routeReminder(page: Page): Promise<void> {
  await page.route("https://github.com/sessions/two-factor**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: reminderHtml,
    }),
  );
  await page.route("https://github.com/login/oauth/authorize**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><html><body><h1>Authorize</h1></body></html>",
    }),
  );
}

test("dismisses the reminder and resumes the interrupted flow", async ({
  page,
}) => {
  await routeReminder(page);
  await page.goto(REMINDER_URL);

  const dismissed = await dismissTwoFactorReminder(page);

  expect(dismissed).toBe(true);
  expect(page.url()).toBe(RESUMED_URL);
});

test("is a no-op when the reminder is not shown", async ({ page }) => {
  await page.route("https://github.com/login/oauth/authorize**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: '<!doctype html><html><body><form action="/login/oauth/authorize"><button name="authorize" value="1">Authorize</button></form></body></html>',
    }),
  );
  await page.goto(RESUMED_URL);

  const dismissed = await dismissTwoFactorReminder(page);

  expect(dismissed).toBe(false);
  expect(page.url()).toBe(RESUMED_URL);
});
