"""Contract tests for scripts/replicated_deploy.sh.

Each test runs the real script against a stub KOTS API on localhost, so the
assertions cover the shell's control flow, not a reimplementation of it.
"""

import json
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/replicated_deploy.sh"
CURSOR = "491"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_handler(state):
    def downstream():
        c = state["deployed"]
        pend = [{"updateCursor": CURSOR, "sequence": 110}] if state["pending"] and c != CURSOR else []
        return {
            "currentVersion": {"updateCursor": c, "sequence": 110 if c == CURSOR else 109,
                               "status": "deployed", "versionLabel": "0.67.0"},
            "pendingVersions": pend,
            "pastVersions": state["past"],
        }

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, code, obj):
            body = b"" if obj is None else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_PUT(self):
            self.reply(200, {"success": True})

        def do_POST(self):
            if self.path.endswith("/deploy"):
                state["deploy_paths"].append(self.path)
                if state["deploy_status"] != 200:
                    return self.reply(state["deploy_status"], state["deploy_body"])
                state["deployed"] = CURSOR
            self.reply(200, {"success": True})

        def do_GET(self):
            p = self.path
            if p.endswith("/api/v1/apps"):
                if state["apps_status"] != 200:
                    return self.reply(state["apps_status"],
                                      {"error": "missing authorization token", "success": False})
                return self.reply(200, {"apps": [{"downstream": downstream()}]})
            if "/updates" in p:
                ups = [{"updateCursor": CURSOR, "versionLabel": "0.67.0",
                        "channelId": "ch1", "isDeployable": True}]
                return self.reply(200, {"updates": [] if state["pending"] or state["past"] else ups})
            if p.endswith("/preflight/result"):
                state["polls"] += 1
                if state["polls"] <= state["placeholders"]:
                    # the empty result string KOTS writes before the run finishes
                    return self.reply(200, {"preflightResult": {"result": "",
                                                                "hasFailingStrictPreflights": False}})
                strict = state["strict_fail"]
                res = {"results": [{"isPass": not strict, "title": "mem"}]}
                return self.reply(200, {"preflightResult": {
                    "result": json.dumps(res), "hasFailingStrictPreflights": strict}})
            if "/task/upgrade-service" in p:
                return self.reply(200, {"status": ""})
            if p.endswith("/upgrade-service/app/openhands"):
                return self.reply(200, {"success": True, "isConfigurable": False, "hasPreflight": True})
            if p.endswith("/status"):
                return self.reply(200, {"appstatus": {"state": "ready", "sequence": 110,
                                                      "resourceStates": []}})
            return self.reply(200, {"success": True})

    return H


@pytest.fixture
def kots():
    state = {"deployed": "489", "pending": False, "past": [], "polls": 0,
             "placeholders": 0, "strict_fail": False, "deploy_status": 200,
             "deploy_body": None, "deploy_paths": [], "apps_status": 200}
    port = free_port()
    server = HTTPServer(("127.0.0.1", port), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["run"] = lambda: subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, timeout=180,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "KOTS_BASE": f"http://127.0.0.1:{port}",
             "KOTS_PASSWORD": "x", "KOTS_CURSOR": CURSOR, "TIMEOUT_MINUTES": "1"})
    yield state
    server.shutdown()


def test_a_cursor_already_deployed_verifies_and_succeeds(kots):
    """Re-running a finished deploy must be a no-op, not a red job."""
    kots["deployed"] = CURSOR
    r = kots["run"]()
    assert r.returncode == 0, r.stderr
    assert "already deployed" in r.stdout
    assert kots["deploy_paths"] == []


def test_a_downloaded_cursor_is_resumed_from_its_sequence(kots):
    """A pending version can't boot the upgrade service, so it deploys downstream."""
    kots["pending"] = True
    r = kots["run"]()
    assert r.returncode == 0, r.stderr
    assert kots["deploy_paths"] == ["/api/v1/app/openhands/sequence/110/deploy"]


def test_a_past_cursor_is_refused_as_a_rollback(kots):
    kots["past"] = [{"updateCursor": CURSOR, "sequence": 95}]
    r = kots["run"]()
    assert r.returncode == 1
    assert "rolling back needs the admin console" in r.stdout


def test_an_empty_preflight_result_does_not_claim_preflights_passed(kots):
    """Verified against unstable: .result is "" on every sequence, healthy ones
    included, so the gate must report that rather than printing a pass."""
    kots["placeholders"] = 10**6
    r = kots["run"]()
    assert r.returncode == 0, r.stderr
    assert "preflights: none recorded" in r.stdout
    assert kots["deploy_paths"], "an empty result must not block the deploy"


def test_a_strict_preflight_failure_blocks_the_deploy(kots):
    kots["strict_fail"] = True
    r = kots["run"]()
    assert r.returncode == 1
    assert "preflights failed" in r.stdout
    assert kots["deploy_paths"] == []


def test_a_rejection_with_no_body_reports_the_status_code(kots):
    """A bare 4xx used to surface as "deploy rejected: <empty response>"."""
    kots["deploy_status"] = 404
    r = kots["run"]()
    assert r.returncode == 1
    assert "HTTP 404" in r.stdout


def test_a_rejection_reason_is_surfaced_verbatim(kots):
    kots["deploy_status"] = 400
    kots["deploy_body"] = {"error": "preflight checks have not completed"}
    r = kots["run"]()
    assert r.returncode == 1
    assert "preflight checks have not completed" in r.stdout


def test_an_unreadable_app_state_names_the_reason(kots):
    """jq returns null with exit 0 on an error body, so this must not fail open."""
    kots["apps_status"] = 401
    r = kots["run"]()
    assert r.returncode == 1
    assert "could not read app state" in r.stdout
    assert "missing authorization token" in r.stdout
    assert "never became an available update" not in r.stdout
