"""Deterministic automation entrypoint for the COMPLETED-run journey.

Runs inside the sandbox with the standard library only. It starts a stub
OpenAI-compatible LLM on loopback whose single reply calls the ``finish`` tool
with EXPECTED_OUTPUT, drives one conversation on the sandbox's agent server
against that stub, then reports COMPLETED with the conversation id. Any
failure reports FAILED with the reason, so the run can only be COMPLETED when
the conversation really finished with the expected output.
"""

import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EXPECTED_OUTPUT = __EXPECTED_OUTPUT__
AGENT_URL = "http://127.0.0.1:" + os.environ.get("AGENT_SERVER_PORT", "8000")
SESSION_KEY = os.environ.get("SESSION_API_KEY") or os.environ.get(
    "OH_SESSION_API_KEYS_0", ""
)
CONVERSATION_TIMEOUT_S = 90


class StubLlm(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        reply = json.dumps(
            {
                "id": "chatcmpl-qa-stub",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model", "qa-stub"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_qa_finish",
                                    "type": "function",
                                    "function": {
                                        "name": "finish",
                                        "arguments": json.dumps(
                                            {"message": EXPECTED_OUTPUT}
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


def agent_request(method, path, payload=None):
    request = urllib.request.Request(
        AGENT_URL + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"X-Session-API-Key": SESSION_KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def run_conversation(stub_port):
    started = agent_request(
        "POST",
        "/api/conversations",
        {
            "agent_settings": {
                "llm": {
                    "usage_id": "qa-stub",
                    "model": "openai/qa-stub",
                    "base_url": f"http://127.0.0.1:{stub_port}/v1",
                    "api_key": "qa-stub-key",
                }
            },
            "workspace": {"working_dir": "/tmp"},
            "initial_message": {
                "role": "user",
                "content": [{"type": "text", "text": "Report the QA output."}],
                "run": True,
            },
        },
    )
    conversation_id = started["id"]
    deadline = time.time() + CONVERSATION_TIMEOUT_S
    while time.time() < deadline:
        state = agent_request("GET", f"/api/conversations/{conversation_id}")
        status = state.get("execution_status")
        if status == "finished":
            return conversation_id
        if status in ("error", "stuck"):
            raise RuntimeError(f"conversation {conversation_id} ended {status}")
        time.sleep(1)
    raise RuntimeError(f"conversation {conversation_id} did not finish in time")


def fire_callback(status, conversation_id=None, error=None):
    body = {"status": status, "run_id": os.environ["AUTOMATION_RUN_ID"]}
    if conversation_id:
        body["conversation_id"] = conversation_id
    if error:
        body["error"] = error
    request = urllib.request.Request(
        os.environ["AUTOMATION_CALLBACK_URL"],
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.environ["AUTOMATION_CALLBACK_API_KEY"],
        },
    )
    urllib.request.urlopen(request, timeout=30).read()


def main():
    stub = ThreadingHTTPServer(("127.0.0.1", 0), StubLlm)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    try:
        conversation_id = run_conversation(stub.server_address[1])
        print(EXPECTED_OUTPUT, flush=True)
        fire_callback("COMPLETED", conversation_id)
    except Exception as exc:
        print(f"fixture failed: {exc}", file=sys.stderr, flush=True)
        fire_callback("FAILED", error=str(exc))
        sys.exit(1)
    finally:
        stub.shutdown()


main()
