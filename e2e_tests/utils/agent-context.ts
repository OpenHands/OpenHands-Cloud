import type { APIRequestContext } from "@playwright/test";

/**
 * Read and cleanup access to the member's persistent agent memory (the Agent
 * Context page's setting), through the browser session's cookies
 * (``page.request``). The Agent Context page and the Canvas SPA call the same
 * endpoints, so the actor needs no API key.
 *
 * The page holds one switch, ``enable_memory_context``. The memory content
 * (``memory_context``) has no editor and no write API: only the agent writes
 * it, by editing ``.openhands/memory/MEMORY.md`` with its file editor.
 */

export interface MemorySettings {
  enable_memory_context: boolean;
  memory_context: string | null;
  agent_settings: unknown;
}

/** Path the agent edits to write persistent memory, relative to its working directory. */
export const MEMORY_FILE_PATH = ".openhands/memory/MEMORY.md";

async function failure(
  label: string,
  response: Awaited<ReturnType<APIRequestContext["get"]>>,
) {
  return new Error(
    `${label} failed (${response.status()} ${response.statusText()}): ${await response.text()}`,
  );
}

/** The member's stored memory switch and content, plus their agent settings for leak checks. */
export async function getMemorySettings(
  request: APIRequestContext,
): Promise<MemorySettings> {
  const response = await request.get("/api/v1/settings");
  if (!response.ok()) throw await failure("getMemorySettings", response);
  const body = (await response.json()) as Partial<MemorySettings>;
  return {
    enable_memory_context: !!body.enable_memory_context,
    memory_context: body.memory_context ?? null,
    agent_settings: body.agent_settings ?? null,
  };
}

/** Set the member's memory switch the way the Agent Context page saves it. */
export async function setMemoryEnabled(
  request: APIRequestContext,
  enabled: boolean,
): Promise<void> {
  const response = await request.post("/api/v1/settings", {
    data: { enable_memory_context: enabled },
  });
  if (!response.ok()) throw await failure("setMemoryEnabled", response);
}

/** The conversation id in a ``/conversations/<id>`` URL. */
export function conversationIdFromUrl(url: string): string {
  const match = /\/conversations\/([^/?#]+)/.exec(url);
  if (!match) throw new Error(`No conversation id in URL: ${url}`);
  return match[1];
}

/** The one-line memory entry that binds ``key`` to ``value``. */
export function memoryEntryLine(key: string, value: string): string {
  return `${key}: ${value}`;
}

/** Delete a conversation record. A 404 counts as already deleted. */
export async function deleteConversation(
  request: APIRequestContext,
  conversationId: string,
): Promise<void> {
  const response = await request.delete(
    `/api/v1/app-conversations/${encodeURIComponent(conversationId)}`,
  );
  if (!response.ok() && response.status() !== 404) {
    throw await failure("deleteConversation", response);
  }
}
