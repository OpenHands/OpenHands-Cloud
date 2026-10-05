import { expect, test } from "@playwright/test";

import { isSettledAppUrl } from "../utils/auth-helpers";

const APP = "https://app.unstable.staging.all-hands-testing.dev";
const AUTH = "https://auth.unstable.staging.all-hands-testing.dev";

test("app landing page with a login_method query is settled", () => {
  expect(isSettledAppUrl(`${APP}/canvas?login_method=github`, false)).toBe(
    true,
  );
});

test("app home page is settled", () => {
  expect(isSettledAppUrl(`${APP}/`, false)).toBe(true);
});

for (const url of [
  "https://github.com/login/oauth/authorize?client_id=x",
  "https://github.com/sessions/verified-device",
  `${AUTH}/realms/allhands/protocol/openid-connect/auth?client_id=allhands`,
  `${AUTH}/realms/allhands/broker/github/endpoint?code=x`,
  `${APP}/oauth/keycloak/callback?code=x`,
  `${APP}/login`,
]) {
  test(`redirect step ${url} is not settled`, () => {
    expect(isSettledAppUrl(url, true)).toBe(false);
  });
}

test("onboarding pages settle only when onboarding is allowed", () => {
  expect(isSettledAppUrl(`${APP}/accept-tos`, true)).toBe(true);
  expect(isSettledAppUrl(`${APP}/accept-tos`, false)).toBe(false);
});
