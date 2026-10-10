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
                               "status": state["current_status"], "versionLabel": "0.67.0"},
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
            state["config_paths"].append(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            state["config_bodies"].append(json.loads(self.rfile.read(n) or b"null"))
            total = len(state["config_paths"])
            if state["config_502_then"] and total <= state["config_502_then"]:
                return self.reply(502, None)
            if state["config_status"] != 200:
                return self.reply(state["config_status"], state["config_body"])
            return self.reply(200, {"success": True})

        def do_POST(self):
            if self.path.endswith("/start-upgrade-service"):
                state["boot_paths"].append(self.path)
                total = len(state["boot_paths"])
                if state["boot_502_then"] and total <= state["boot_502_then"]:
                    return self.reply(502, None)
                if state["boot_status"] != 200:
                    return self.reply(state["boot_status"], state["boot_body"])
                return self.reply(200, {"success": True})
            if self.path.endswith("/deploy"):
                state["deploy_paths"].append(self.path)
                total = len(state["deploy_paths"])
                if state["deploy_502_then"] and total <= state["deploy_502_then"]:
                    return self.reply(502, None)
                if state["deploy_status"] != 200:
                    return self.reply(state["deploy_status"], state["deploy_body"])
                state["deployed"] = CURSOR
                # Simulate the console going dark right after the deploy lands, so
                # wait_deployed() reaches its loop and then can't read /apps.
                if state["apps_fail_after_deploy"]:
                    state["apps_dark"] = True
            self.reply(200, {"success": True})

        def do_GET(self):
            p = self.path
            if p.endswith("/api/v1/apps"):
                if state["apps_dark"]:
                    # kotsadm restarting: a bare 5xx with no body, so the read
                    # yields nothing and the loop falls back to the sentinel.
                    return self.reply(503, None)
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
            if p.endswith("/upgrade-service/app/openhands/config"):
                return self.reply(200, {"configGroups": state["config_groups"]})
            if "/task/upgrade-service" in p:
                return self.reply(200, {"status": ""})
            if p.endswith("/upgrade-service/app/openhands"):
                return self.reply(200, {"success": True, "isConfigurable": state["configurable"],
                                        "hasPreflight": True})
            if p.endswith("/status"):
                return self.reply(200, {"appstatus": {"state": "ready", "sequence": 110,
                                                      "resourceStates": []}})
            return self.reply(200, {"success": True})

    return H


@pytest.fixture
def kots():
    state = {"deployed": "489", "pending": False, "past": [], "polls": 0,
             "placeholders": 0, "strict_fail": False, "deploy_status": 200,
             "deploy_body": None, "deploy_502_then": 0, "deploy_paths": [],
             "config_status": 200, "config_body": None, "config_502_then": 0,
             "config_paths": [], "config_bodies": [], "config_groups": None,
             "boot_status": 200, "boot_body": None,
             "boot_502_then": 0, "boot_paths": [], "apps_status": 200,
             "configurable": False, "apps_fail_after_deploy": False,
             "apps_dark": False, "current_status": "deployed", "extra_env": {}}
    port = free_port()
    server = HTTPServer(("127.0.0.1", port), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_env = {"PATH": "/usr/bin:/bin:/usr/local/bin",
                "KOTS_BASE": f"http://127.0.0.1:{port}", "KOTS_PASSWORD": "x",
                "KOTS_CURSOR": CURSOR, "TIMEOUT_MINUTES": "1"}
    state["run"] = lambda: subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, timeout=180,
        env={**base_env, **state["extra_env"]})
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


def test_a_failed_deploy_names_the_cursor_and_sequence(kots):
    kots["current_status"] = "failed"
    kots["deployed"] = CURSOR
    r = kots["run"]()
    assert r.returncode == 1
    assert f"cursor {CURSOR} (sequence 110) failed" in r.stdout


def test_a_timeout_reports_the_last_observed_downstream_state(kots):
    """A cursor that stalls at a non-terminal status never reaches deployed or
    failed, so wait_deployed() times out. The verdict must carry the state it
    last saw rather than dropping it — that state is the triage signal."""
    # Deploy succeeds, but the target cursor stays "deploying" forever.
    kots["current_status"] = "deploying"
    # A seconds-granularity budget and a 1s poll exercise the timeout in ~5s
    # instead of the whole-minute budget the other tests run under.
    kots["extra_env"] = {"TIMEOUT_SECONDS": "5", "DEPLOY_POLL_SECONDS": "1"}
    r = kots["run"]()
    assert r.returncode == 1
    assert f"timed out waiting for cursor {CURSOR} to deploy" in r.stdout
    assert "last state:" in r.stdout
    assert '"status":"deploying"' in r.stdout


def test_a_timeout_with_a_dark_console_reports_the_sentinel(kots):
    """If /apps is unreachable on the last loop, the last-state suffix falls back
    to the sentinel rather than an empty string."""
    kots["apps_fail_after_deploy"] = True
    kots["extra_env"] = {"TIMEOUT_SECONDS": "5", "DEPLOY_POLL_SECONDS": "1"}
    r = kots["run"]()
    assert r.returncode == 1
    assert f"timed out waiting for cursor {CURSOR} to deploy" in r.stdout
    assert "last state: <console unreachable, waiting>" in r.stdout


def test_a_transient_502_on_the_deploy_post_is_retried(kots):
    """kotsadm's gateway blips a bare 502 while it restarts mid-upgrade."""
    kots["deploy_502_then"] = 1
    r = kots["run"]()
    assert r.returncode == 0, r.stderr
    assert "KOTS gateway hit a transient error" in r.stderr
    assert "retrying" in r.stderr
    assert kots["deploy_paths"] == [
        "/api/v1/upgrade-service/app/openhands/deploy",
        "/api/v1/upgrade-service/app/openhands/deploy",
    ]


def test_a_transient_502_on_the_config_put_is_retried(kots):
    """The config write hits the same restart window; it must not redden the job."""
    kots["config_502_then"] = 1
    kots["configurable"] = True
    r = kots["run"]()
    assert r.returncode == 0, r.stderr
    assert "KOTS gateway hit a transient error" in r.stderr
    assert len(kots["config_paths"]) == 2
    assert kots["deploy_paths"], "a retried config must not block the deploy"


def test_the_config_write_back_enables_user_provisioning_and_keeps_other_values(kots):
    """The E2E org specs call the provision-user endpoint, which this item registers."""
    kots["configurable"] = True
    kots["config_groups"] = [{"name": "oem", "items": [
        {"name": "oem_user_creation_flow_enabled", "value": "", "default": "0"},
        {"name": "other_item", "value": "kept", "default": ""}]}]
    r = kots["run"]()
    assert r.returncode == 0, r.stderr
    assert kots["config_bodies"][-1] == {"configGroups": [{"name": "oem", "items": [
        {"name": "oem_user_creation_flow_enabled", "value": "1", "default": "0"},
        {"name": "other_item", "value": "kept", "default": ""}]}]}


def test_a_transient_502_on_booting_the_upgrade_service_is_retried(kots):
    """start-upgrade-service goes through the same gateway restart window."""
    kots["boot_502_then"] = 1
    r = kots["run"]()
    assert r.returncode == 0, r.stderr
    assert "KOTS gateway hit a transient error" in r.stderr
    assert len(kots["boot_paths"]) == 2
    assert kots["deploy_paths"], "a retried boot must not block the deploy"


def test_a_real_deploy_rejection_is_not_retried(kots):
    """retry_gateway is deliberately narrow: only a bare 502/503/504 is transient.
    A real .success:false / non-5xx must fail at once, or a broadened classifier
    silently turns a fast, clear rejection into a retry-until-deadline hang."""
    kots["deploy_status"] = 400
    kots["deploy_body"] = {"error": "preflight checks have not completed"}
    r = kots["run"]()
    assert r.returncode == 1
    assert "preflight checks have not completed" in r.stdout
    assert kots["deploy_paths"] == [
        "/api/v1/upgrade-service/app/openhands/deploy"
    ], "a non-5xx rejection must fail at once, not be retried"


def test_a_real_config_rejection_is_not_retried(kots):
    """The config PUT this PR routes through retry_gateway must still fail fast on a
    real rejection — the narrowness is unexercised for this callsite otherwise."""
    kots["configurable"] = True
    kots["config_status"] = 400
    kots["config_body"] = {"error": "a required item has no default"}
    r = kots["run"]()
    assert r.returncode == 1
    assert "config rejected: a required item has no default" in r.stdout
    assert kots["config_paths"] == [
        "/api/v1/upgrade-service/app/openhands/config"
    ], "a non-5xx config rejection must fail at once, not be retried"


def test_a_real_boot_rejection_is_not_retried(kots):
    """start-upgrade-service now goes through retry_gateway too; a real rejection
    (a cursor/license-channel mismatch) must still fail at once, not be retried."""
    kots["boot_status"] = 400
    kots["boot_body"] = {"error": "license channel mismatch"}
    r = kots["run"]()
    assert r.returncode == 1
    assert "start-upgrade-service rejected: license channel mismatch" in r.stdout
    assert kots["boot_paths"] == [
        "/api/v1/app/openhands/start-upgrade-service"
    ], "a non-5xx boot rejection must fail at once, not be retried"


def test_a_permanent_gateway_5xx_gives_up_at_the_deadline(kots):
    """The `&& waiting` conjunct keeps retries inside the run deadline. Without it a
    gateway stuck at 5xx would retry forever and surface as an opaque CI timeout
    instead of the script's own "gave up" failure. Drive a gateway that never
    recovers and assert the job retries, then still terminates at the deadline."""
    kots["deploy_502_then"] = 999  # never recovers
    # A seconds-granularity budget makes the deadline fire after one ~10s backoff
    # rather than the whole-minute budget the other tests run under.
    kots["extra_env"] = {"TIMEOUT_SECONDS": "3"}
    r = kots["run"]()
    assert r.returncode == 1, r.stderr
    assert "KOTS gateway hit a transient error" in r.stderr
    assert len(kots["deploy_paths"]) >= 2, "must retry, then give up at the deadline"
