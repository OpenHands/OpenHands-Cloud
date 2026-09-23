import { test as base, expect } from "@playwright/test";

/**
 * Billing feature-flag gating.
 *
 * When the app loads it makes a background request to
 * `/api/v1/web-client/config` and exposes its `feature_flags` to the client.
 * If `feature_flags.enable_billing` is falsy, the billing UI (and therefore
 * any billing e2e spec) cannot run, so every spec that imports the `test`
 * below is skipped automatically.
 *
 * The config is fetched once per worker and cached; the skip is applied
 * through an auto fixture so individual specs don't have to repeat the check.
 */

/** Shape of the `feature_flags` object returned by the config endpoint. */
export interface FeatureFlags {
  enable_billing?: boolean;
  [key: string]: boolean | undefined;
}

/** Parsed body of `/api/v1/web-client/config`. */
export interface WebClientConfig {
  feature_flags?: FeatureFlags;
  [key: string]: unknown;
}

const CONFIG_PATH = "/api/v1/web-client/config";

let cachedConfig: WebClientConfig | undefined;

/**
 * Fetch `/api/v1/web-client/config` (relative to BASE_URL) and cache the
 * parsed body for the lifetime of the worker. Returns the full body so callers
 * can read other fields besides the feature flags.
 */
export async function fetchWebClientConfig(
  request: import("@playwright/test").APIRequestContext,
): Promise<WebClientConfig> {
  if (cachedConfig) {
    return cachedConfig;
  }

  const response = await request.get(CONFIG_PATH);
  if (!response.ok()) {
    throw new Error(
      `Failed to fetch web-client config (${response.status()} ${response.statusText()})`,
    );
  }

  cachedConfig = (await response.json()) as WebClientConfig;
  return cachedConfig;
}

/** True when billing is enabled in the fetched feature flags. */
export function billingEnabled(config: WebClientConfig | undefined): boolean {
  return Boolean(config?.feature_flags?.enable_billing);
}

/**
 * Test object for billing specs. An auto fixture fetches the web-client config
 * (cached, so only the first test in a worker pays for the request) and skips
 * every test using this object when `feature_flags.enable_billing` is falsy.
 * Billing specs should import `{ test, expect }` from here instead of from
 * `@playwright/test` directly.
 */
export const test = base.extend<{ billingConfig: WebClientConfig }>({
  billingConfig: [
    async ({ request }, use) => {
      const config = await fetchWebClientConfig(request);
      test.skip(
        !billingEnabled(config),
        "billing is disabled (feature_flags.enable_billing is falsy)",
      );
      await use(config);
    },
    { auto: true },
  ],
});

export { expect };
