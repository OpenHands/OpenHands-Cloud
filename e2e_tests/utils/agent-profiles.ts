import type { APIRequestContext } from "@playwright/test";

/**
 * Read and cleanup access to Agent Profiles and the conversations they launch,
 * through the browser session's cookies (``page.request``). The settings page
 * and the Canvas SPA call the same endpoints, so the actor needs no API key.
 */

export interface StoredAgentProfile {
  id: string;
  name: string;
  revision: number;
  agent_kind: string;
  persona?: string | null;
  system_message_suffix?: string | null;
  [key: string]: unknown;
}

export interface AgentProfileList {
  profiles: Array<{ id: string | null; name: string; scope: string }>;
  active_agent_profile_id: string | null;
}

export interface LaunchedAgentProfile {
  agent_profile_id: string;
  revision: number;
}

interface ConversationEvent {
  kind?: string;
  source?: string;
  tool_name?: string;
  llm_message?: unknown;
  tool_call?: { arguments?: string };
  [key: string]: unknown;
}

async function failure(
  label: string,
  response: Awaited<ReturnType<APIRequestContext["get"]>>,
) {
  return new Error(
    `${label} failed (${response.status()} ${response.statusText()}): ${await response.text()}`,
  );
}

const profileUrl = (name: string) =>
  `/api/agent-profiles/${encodeURIComponent(name)}`;

export async function listAgentProfiles(
  request: APIRequestContext,
): Promise<AgentProfileList> {
  const response = await request.get("/api/agent-profiles");
  if (!response.ok()) throw await failure("listAgentProfiles", response);
  return (await response.json()) as AgentProfileList;
}

/**
 * The LLM profile an Agent Profile can be built on: the member's active one, or
 * else the first they have. ``null`` when they have none, for example when the
 * managed LLM lives only in the account's agent settings.
 */
export async function getUsableLlmProfileName(
  request: APIRequestContext,
): Promise<string | null> {
  const response = await request.get("/api/v1/settings/profiles");
  if (!response.ok()) throw await failure("getUsableLlmProfileName", response);
  const { active_profile: active, profiles } = (await response.json()) as {
    active_profile: string | null;
    profiles?: Array<{ name: string }>;
  };
  return active ?? profiles?.[0]?.name ?? null;
}

/** The member's own profile ``name`` as stored, or ``null`` when there is none. */
export async function getPersonalAgentProfile(
  request: APIRequestContext,
  name: string,
): Promise<StoredAgentProfile | null> {
  const response = await request.get(profileUrl(name), {
    params: { scope: "personal" },
  });
  if (response.status() === 404) return null;
  if (!response.ok()) throw await failure("getPersonalAgentProfile", response);
  const { profile } = (await response.json()) as {
    profile: StoredAgentProfile;
  };
  return profile;
}

/** Delete the member's own profile. A 404 counts as already deleted. */
export async function deletePersonalAgentProfile(
  request: APIRequestContext,
  name: string,
): Promise<void> {
  const response = await request.delete(profileUrl(name), {
    params: { scope: "personal" },
  });
  if (!response.ok() && response.status() !== 404) {
    throw await failure("deletePersonalAgentProfile", response);
  }
}

export async function activateAgentProfile(
  request: APIRequestContext,
  id: string,
): Promise<void> {
  const response = await request.post(`${profileUrl(id)}/activate`);
  if (!response.ok()) throw await failure("activateAgentProfile", response);
}

/** Clear the member's default when it points at ``id``. */
export async function deactivateAgentProfile(
  request: APIRequestContext,
  id: string,
): Promise<void> {
  const response = await request.post(`${profileUrl(id)}/deactivate`);
  if (!response.ok()) throw await failure("deactivateAgentProfile", response);
}

/** The Agent Profile the app server recorded as launching ``conversationId``, or ``null``. */
export async function getLaunchedAgentProfile(
  request: APIRequestContext,
  conversationId: string,
): Promise<LaunchedAgentProfile | null> {
  const response = await request.get("/api/v1/app-conversations", {
    params: { ids: conversationId },
  });
  if (!response.ok()) throw await failure("getLaunchedAgentProfile", response);
  const [conversation] = (await response.json()) as Array<{
    launched_agent_profile?: LaunchedAgentProfile | null;
  } | null>;
  if (!conversation) {
    throw new Error(
      `Conversation ${conversationId} is unknown to the app server`,
    );
  }
  return conversation.launched_agent_profile ?? null;
}

/**
 * Text of every message the agent sent in the conversation: its chat messages
 * and the ``message`` of its ``finish`` calls. The system prompt is left out
 * on purpose, so a match is behavior and not the configured instruction.
 */
export async function listAgentReplies(
  request: APIRequestContext,
  conversationId: string,
): Promise<string[]> {
  const response = await request.get(
    `/api/v1/conversation/${encodeURIComponent(conversationId)}/events/search`,
    { params: { limit: "100" } },
  );
  if (!response.ok()) throw await failure("listAgentReplies", response);
  const { items } = (await response.json()) as { items: ConversationEvent[] };
  return items
    .filter((event) => event.source === "agent")
    .flatMap((event) => {
      if (event.kind === "MessageEvent") {
        return [JSON.stringify(event.llm_message ?? "")];
      }
      if (event.tool_name === "finish" && event.tool_call) {
        const args = JSON.parse(event.tool_call.arguments ?? "{}") as {
          message?: string;
        };
        return [args.message ?? ""];
      }
      return [];
    });
}

/**
 * Ids of the conversations the profile ``profileId`` launched, found by the
 * launch provenance tag the app server puts on the conversation record.
 */
export async function listConversationsLaunchedBy(
  request: APIRequestContext,
  profileId: string,
): Promise<string[]> {
  const response = await request.get("/api/v1/app-conversations/search", {
    params: { limit: "100" },
  });
  if (!response.ok())
    throw await failure("listConversationsLaunchedBy", response);
  const { items } = (await response.json()) as {
    items: Array<{ id: string; tags?: Record<string, string> }>;
  };
  return items
    .filter(({ tags }) => tags?.agentprofileid === profileId)
    .map(({ id }) => id);
}

/** Delete every conversation record the profile ``profileId`` launched. */
export async function deleteConversationsLaunchedBy(
  request: APIRequestContext,
  profileId: string,
): Promise<void> {
  for (const id of await listConversationsLaunchedBy(request, profileId)) {
    const response = await request.delete(
      `/api/v1/app-conversations/${encodeURIComponent(id)}`,
    );
    if (!response.ok() && response.status() !== 404) {
      throw await failure("deleteConversationsLaunchedBy", response);
    }
  }
}
