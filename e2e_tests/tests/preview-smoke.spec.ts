import { test, expect } from "@playwright/test";
import { HomePage } from "../pages";
import { loginToPreviewOrigin } from "../utils/preview-auth";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required preview setting: ${name}`);
  return value;
}

test("preview member login survives reload and logs out", async ({ page }) => {
  const baseUrl = required("BASE_URL");
  const orgId = required("PREVIEW_TEST_ORG_ID");
  const email = required("PREVIEW_TEST_EMAIL");
  await loginToPreviewOrigin(page, baseUrl, {
    username: required("PREVIEW_TEST_USERNAME"),
    password: required("PREVIEW_TEST_PASSWORD"),
    email,
  });
  const home = new HomePage(page);
  await expect(home.homeScreen).toBeVisible({ timeout: 30_000 });
  const member = await page.request.get(
    `${baseUrl}/api/organizations/${encodeURIComponent(orgId)}/me`,
  );
  expect(member.status()).toBe(200);
  const identity = await member.json();
  expect(identity.email?.toLowerCase()).toBe(email.toLowerCase());
  expect(identity.role).toBe("member");
  await page.reload();
  await expect(home.homeScreen).toBeVisible();
  await home.logout();
  await expect(
    page.getByRole("button", { name: "Log in with GitHub" }),
  ).toBeVisible();
  expect(
    (await page.context().cookies()).some(
      (cookie) => cookie.name === "keycloak_auth",
    ),
  ).toBe(false);
});
