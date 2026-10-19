import type { Page } from "@playwright/test";

import { loginWithKeycloakPassword } from "./auth-helpers";

export interface PreviewLoginCredentials {
  username: string;
  password: string;
  email: string;
}

const PREVIEW_ORIGIN_PATTERN =
  /^https:\/\/pr-([1-9][0-9]*)\.staging\.all-hands\.dev$/;

/** Resolve the two approved preview origins for a trusted pr-N base URL. */
export function resolvePreviewAuthOrigins(baseUrl: string): string[] {
  const match = PREVIEW_ORIGIN_PATTERN.exec(baseUrl);
  if (!match) {
    return [];
  }
  const pr = match[1];
  const origin = `https://pr-${pr}.staging.all-hands.dev`;
  return [origin, `https://pr-${pr}.auth.staging.all-hands.dev`];
}

/** True when the URL is one of the approved preview origins and stays on an allowed shape. */
export function isPreviewLoginOrigin(
  urlString: string,
  allowedOrigins: readonly string[],
): boolean {
  let url: URL;
  try {
    url = new URL(urlString);
  } catch {
    return false;
  }
  const candidate = `${url.protocol}//${url.host}`;
  if (!allowedOrigins.includes(candidate)) {
    return false;
  }
  if (candidate.includes(".auth.")) {
    const pathAndQuery = `${url.pathname}${url.search}`;
    if (
      !pathAndQuery.startsWith("/realms/") &&
      !pathAndQuery.startsWith("/resources/")
    ) {
      return false;
    }
  }
  return true;
}

function isHttpUrl(urlString: string): boolean {
  try {
    const url = new URL(urlString);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

export async function loginToPreviewOrigin(
  page: Page,
  baseUrl: string,
  creds: PreviewLoginCredentials,
  allowedOrigins?: readonly string[],
): Promise<void> {
  const origins = allowedOrigins ?? resolvePreviewAuthOrigins(baseUrl);
  if (origins.length === 0) {
    throw new Error(
      `Refusing preview login: "${baseUrl}" is not an approved preview origin. Expected e.g. https://pr-12.staging.all-hands.dev.`,
    );
  }
  const currentUrl = page.url();
  let currentAllowed = true;
  if (currentUrl && !/^about:/i.test(currentUrl)) {
    currentAllowed =
      isHttpUrl(currentUrl) && isPreviewLoginOrigin(currentUrl, origins);
  }
  if (!currentAllowed) {
    throw new Error(
      `Refusing preview login: current browser URL "${currentUrl}" is outside the approved preview origins.`,
    );
  }
  if (
    allowedOrigins &&
    !origins.every((origin) => {
      const url = new URL(origin);
      return url.protocol === "http:" && url.hostname === "127.0.0.1";
    })
  ) {
    throw new Error("Test origin overrides must use loopback HTTP");
  }
  let blocked = false;
  const blockedError = () =>
    new Error("Preview login blocked an unapproved origin");
  // Rejects the moment a navigation leaves the approved origins, so the caller
  // fails fast with the real reason instead of the login helper's form timeout.
  let rejectBlocked: (error: Error) => void = () => {};
  const blockedFailure = new Promise<never>((_, reject) => {
    rejectBlocked = reject;
  });
  blockedFailure.catch(() => {});
  const guard = async (route: import("@playwright/test").Route) => {
    const request = route.request();
    if (!isPreviewLoginOrigin(request.url(), origins)) {
      // Every off-origin request is aborted, but only a navigation counts as
      // the flow escaping the approved origins. A real preview pulls
      // third-party subresources (fonts, analytics) on every page view, and
      // counting those aborts as a violation rejected every successful login.
      if (request.isNavigationRequest()) {
        blocked = true;
        rejectBlocked(blockedError());
      }
      await route.abort("blockedbyclient");
      return;
    }
    await route.fallback();
  };
  // Context routing also covers popup requests and cross-origin form submissions.
  await page.context().route("**/*", guard);
  try {
    await page.goto(origins[0]);
    await Promise.race([
      loginWithKeycloakPassword(page, creds),
      blockedFailure,
    ]);
    if (blocked) throw blockedError();
  } finally {
    await page.context().unroute("**/*", guard);
  }
}
