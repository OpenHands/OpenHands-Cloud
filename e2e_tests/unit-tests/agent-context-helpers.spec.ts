import { expect, test } from "@playwright/test";

import { conversationIdFromUrl, memoryEntryLine } from "../utils/agent-context";

/**
 * The agent-context journey finds the conversation it started from the URL the
 * composer navigates to, and asserts on the exact line it asks the agent to
 * save.
 */

test("the conversation id is read from the conversation URL", () => {
  expect(
    conversationIdFromUrl(
      "https://app.example.test/conversations/0f8b1c52-aaaa-bbbb-cccc-123456789abc",
    ),
  ).toBe("0f8b1c52-aaaa-bbbb-cccc-123456789abc");
});

test("a query string or fragment is not part of the conversation id", () => {
  expect(
    conversationIdFromUrl("http://localhost:9043/conversations/abc123?x=1#top"),
  ).toBe("abc123");
});

test("a URL without a conversation id is rejected", () => {
  expect(() => conversationIdFromUrl("http://localhost:9043/settings")).toThrow(
    /No conversation id/,
  );
});

test("a memory entry binds the key to the value on one line", () => {
  expect(memoryEntryLine("qa-codeword-1a2b", "QA-CTX-3C4D")).toBe(
    "qa-codeword-1a2b: QA-CTX-3C4D",
  );
});
