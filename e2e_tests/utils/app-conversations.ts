/**
 * Read access to the app server's record of a conversation, as seen by the
 * API key that owns it. An automation run links its conversation by id, and
 * the app server keeps the record and its events after the run's sandbox is
 * deleted.
 */

export interface AppConversation {
  id: string;
  created_by_user_id: string;
  [key: string]: unknown;
}

interface ConversationEvent {
  tool_name?: string;
  tool_call?: { arguments?: string };
  [key: string]: unknown;
}

function authHeaders(apiKey: string): Record<string, string> {
  return { Authorization: `Bearer ${apiKey}` };
}

/** The conversation record for ``conversationId``, or ``null`` when the app server has none. */
export async function getAppConversation(
  baseUrl: string,
  apiKey: string,
  conversationId: string,
): Promise<AppConversation | null> {
  const url = new URL(
    `api/v1/app-conversations?ids=${encodeURIComponent(conversationId)}`,
    baseUrl,
  ).toString();
  const response = await fetch(url, { headers: authHeaders(apiKey) });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      `getAppConversation failed (${response.status} ${response.statusText}): ${body}`,
    );
  }
  const [conversation] =
    (await response.json()) as Array<AppConversation | null>;
  return conversation ?? null;
}

/** The ``message`` argument of every ``finish`` tool call the agent made in the conversation. */
export async function listFinishMessages(
  baseUrl: string,
  apiKey: string,
  conversationId: string,
): Promise<string[]> {
  const url = new URL(
    `api/v1/conversation/${encodeURIComponent(conversationId)}/events/search?limit=100`,
    baseUrl,
  ).toString();
  const response = await fetch(url, { headers: authHeaders(apiKey) });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      `listFinishMessages failed (${response.status} ${response.statusText}): ${body}`,
    );
  }
  const { items } = (await response.json()) as { items: ConversationEvent[] };
  return items
    .filter((event) => event.tool_name === "finish" && event.tool_call)
    .map(
      (event) =>
        JSON.parse(event.tool_call?.arguments ?? "{}") as { message?: string },
    )
    .map((args) => args.message ?? "");
}

/** Delete the conversation record. A 404 counts as already deleted. */
export async function deleteAppConversation(
  baseUrl: string,
  apiKey: string,
  conversationId: string,
): Promise<void> {
  const url = new URL(
    `api/v1/app-conversations/${encodeURIComponent(conversationId)}`,
    baseUrl,
  ).toString();
  const response = await fetch(url, {
    method: "DELETE",
    headers: authHeaders(apiKey),
  });
  if (response.ok || response.status === 404) {
    return;
  }
  const body = await response.text();
  throw new Error(
    `deleteAppConversation failed (${response.status} ${response.statusText}): ${body}`,
  );
}
