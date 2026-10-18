import { expect, test } from "@playwright/test";

import {
  isPreviewLoginOrigin,
  resolvePreviewAuthOrigins,
} from "../utils/preview-auth";

test("resolves the exact approved preview origins for a pr-N staging host", () => {
  expect(
    resolvePreviewAuthOrigins("https://pr-12.staging.all-hands.dev"),
  ).toEqual([
    "https://pr-12.staging.all-hands.dev",
    "https://pr-12.auth.staging.all-hands.dev",
  ]);
});

test("resolves no origins for hosts outside the preview allowlist", () => {
  expect(resolvePreviewAuthOrigins("https://app.all-hands.dev")).toEqual([]);
  expect(resolvePreviewAuthOrigins("http://localhost:5173")).toEqual([]);
});

test("allows only exact approved origins inside the allowlist", () => {
  const allowed = [
    "https://pr-12.staging.all-hands.dev",
    "https://pr-12.auth.staging.all-hands.dev",
  ];

  expect(
    isPreviewLoginOrigin("https://pr-12.staging.all-hands.dev/canvas", allowed),
  ).toBe(true);
  expect(
    isPreviewLoginOrigin(
      "https://pr-12.auth.staging.all-hands.dev/realms/allhands/protocol/openid-connect/auth?kc_idp_hint=github",
      allowed,
    ),
  ).toBe(true);
  expect(
    isPreviewLoginOrigin("https://staging.all-hands.dev/login", allowed),
  ).toBe(false);
  expect(
    isPreviewLoginOrigin(
      "https://evil-pr-12.staging.all-hands.dev/login",
      allowed,
    ),
  ).toBe(false);
  expect(
    isPreviewLoginOrigin(
      "https://pr-12.staging.all-hands.dev.evil.example.com/login",
      allowed,
    ),
  ).toBe(false);
});
