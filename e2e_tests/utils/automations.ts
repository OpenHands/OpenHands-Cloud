/**
 * REST API client for the OpenHands Automation Service.
 *
 * The automation service is co-located with the app under the same hostname
 * at ``/api/automation/v1/*`` (see the ``ingress-automation`` template in
 * ``charts/openhands``). It authenticates every request itself but delegates
 * identity resolution upstream to the enterprise server — a Bearer API key
 * that the enterprise server accepts is also accepted here, and the
 * automation is created under the API key's user + org.
 *
 * These helpers wrap the endpoints the ``011-automations`` suite exercises:
 *
 *  - ``POST /api/automation/v1/preset/prompt`` — create a prompt-preset
 *    automation. Returns ``201`` with an ``id`` and echoes the trigger.
 *  - ``POST /api/automation/v1/{id}/dispatch`` — start a run immediately,
 *    bypassing cron scheduling. Returns the run's initial state.
 *  - ``GET /api/automation/v1/{id}/runs?limit=N`` — list runs for an
 *    automation; used to poll the dispatched run to a terminal status.
 *  - ``DELETE /api/automation/v1/{id}`` — remove an automation (cleanup).
 *  - ``GET /api/automation/v1?limit=1`` — reachability probe; used to
 *    detect whether the service is deployed on the target cluster before
 *    the suite tries to exercise it.
 *
 * Nothing in this module elevates privilege: all calls use the caller's
 * per-org API key. The org-role permission check (``manage_automations``)
 * lives on the server and is what the regression test is designed to catch.
 */

/** A cron trigger, the only trigger shape this module builds. */
export interface CronTrigger {
  type: "cron";
  schedule: string;
  timezone?: string;
}

/** Payload for ``POST /api/automation/v1/preset/prompt``. */
export interface CreatePromptAutomationPayload {
  name: string;
  prompt: string;
  trigger: CronTrigger;
  timeout?: number;
}

/** Response shape of ``POST /api/automation/v1/preset/prompt``. */
export interface AutomationResponse {
  id: string;
  name: string;
  trigger: CronTrigger;
  enabled: boolean;
  created_at: string;
  [key: string]: unknown;
}

/** Terminal states for an automation run. */
export type RunStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";

/** Row shape of ``GET /api/automation/v1/{id}/runs``. */
export interface AutomationRun {
  id: string;
  status: RunStatus;
  error_detail?: string | null;
  created_at?: string;
  finished_at?: string | null;
  [key: string]: unknown;
}

function bearerHeaders(apiKey: string): Record<string, string> {
  return {
    Authorization: `Bearer ${apiKey}`,
    "Content-Type": "application/json",
  };
}

/**
 * Detect whether the automation service is reachable at ``baseUrl``.
 *
 * The list endpoint is cheap, idempotent, and requires a valid credential —
 * so an ``ok`` response proves both routing (the ingress path exists) and
 * auth (the API key is accepted by the automation service's authenticate
 * step). A 404/502 means the subchart is not deployed; the caller should
 * skip the whole suite rather than surfacing a misleading create failure.
 */
export async function isAutomationServiceReachable(
  baseUrl: string,
  apiKey: string,
): Promise<boolean> {
  const url = new URL("api/automation/v1?limit=1", baseUrl).toString();
  try {
    const response = await fetch(url, { headers: bearerHeaders(apiKey) });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Create a prompt-preset automation. Throws on non-2xx; returns the parsed
 * ``AutomationResponse`` on success. Server-side authorization (the
 * ``manage_automations`` permission check) surfaces as a 403 — the
 * regression this suite is designed to catch.
 */
export async function createPromptAutomation(
  baseUrl: string,
  apiKey: string,
  payload: CreatePromptAutomationPayload,
): Promise<AutomationResponse> {
  const url = new URL("api/automation/v1/preset/prompt", baseUrl).toString();
  const response = await fetch(url, {
    method: "POST",
    headers: bearerHeaders(apiKey),
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      `createPromptAutomation failed (${response.status} ${response.statusText}): ${body}`,
    );
  }
  return (await response.json()) as AutomationResponse;
}

/**
 * Dispatch a run for an existing automation. Bypasses cron so the suite
 * does not have to wait for the schedule to fire.
 */
export async function dispatchAutomation(
  baseUrl: string,
  apiKey: string,
  automationId: string,
): Promise<void> {
  const url = new URL(
    `api/automation/v1/${encodeURIComponent(automationId)}/dispatch`,
    baseUrl,
  ).toString();
  const response = await fetch(url, {
    method: "POST",
    headers: bearerHeaders(apiKey),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      `dispatchAutomation failed (${response.status} ${response.statusText}): ${body}`,
    );
  }
}

/** List the most recent runs for an automation. */
export async function listRuns(
  baseUrl: string,
  apiKey: string,
  automationId: string,
  limit = 5,
): Promise<AutomationRun[]> {
  const url = new URL(
    `api/automation/v1/${encodeURIComponent(automationId)}/runs?limit=${limit}`,
    baseUrl,
  ).toString();
  const response = await fetch(url, { headers: bearerHeaders(apiKey) });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      `listRuns failed (${response.status} ${response.statusText}): ${body}`,
    );
  }
  const parsed = (await response.json()) as
    | AutomationRun[]
    | { items: AutomationRun[] };
  // Some endpoints wrap collections in ``{items: [...]}``; accept both shapes.
  return Array.isArray(parsed) ? parsed : parsed.items;
}

/**
 * Poll the automation's runs until the most recent one reaches a terminal
 * status (``COMPLETED`` or ``FAILED``), or until ``timeoutMs`` elapses.
 *
 * The automation service assigns a fresh run id on dispatch; when the first
 * poll finds any run at all we adopt its id and keep polling that specific
 * run so a later cron tick can't confuse the assertion.
 */
function isTerminal(status: RunStatus): boolean {
  return status === "COMPLETED" || status === "FAILED";
}

export async function waitForRunTerminal(
  baseUrl: string,
  apiKey: string,
  automationId: string,
  timeoutMs: number,
): Promise<AutomationRun> {
  const deadline = Date.now() + timeoutMs;
  let trackedRunId: string | undefined;

  while (Date.now() < deadline) {
    const runs = await listRuns(baseUrl, apiKey, automationId);
    // Snapshot into a per-iteration const so the closure below does not
    // capture the mutable loop-scoped ``trackedRunId`` (eslint no-loop-func).
    const trackedId = trackedRunId;
    // Runs sort newest-first by convention; the first entry is the one
    // dispatch just created.
    const current = trackedId ? runs.find((r) => r.id === trackedId) : runs[0];
    if (current && !trackedRunId) {
      trackedRunId = current.id;
    }
    if (current && isTerminal(current.status)) {
      return current;
    }
    await new Promise((resolve) => setTimeout(resolve, 3_000));
  }

  throw new Error(
    `waitForRunTerminal: run for automation ${automationId} did not reach ` +
      `a terminal status within ${timeoutMs}ms`,
  );
}

/**
 * Delete an automation. Returns silently when the automation is already
 * gone (404) so cleanup is safe to call unconditionally.
 */
export async function deleteAutomation(
  baseUrl: string,
  apiKey: string,
  automationId: string,
): Promise<void> {
  const url = new URL(
    `api/automation/v1/${encodeURIComponent(automationId)}`,
    baseUrl,
  ).toString();
  const response = await fetch(url, {
    method: "DELETE",
    headers: bearerHeaders(apiKey),
  });
  if (response.ok || response.status === 404) {
    return;
  }
  const body = await response.text();
  throw new Error(
    `deleteAutomation failed (${response.status} ${response.statusText}): ${body}`,
  );
}
