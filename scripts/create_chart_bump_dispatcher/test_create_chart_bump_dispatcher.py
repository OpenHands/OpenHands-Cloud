#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyGithub", "requests", "fastapi", "uvicorn", "pytest", "httpx"]
# ///
"""Tests for create_chart_bump_dispatcher.py."""

from __future__ import annotations

import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import requests
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))

import create_chart_bump_dispatcher
from create_chart_bump_dispatcher import (
    APP_HOMEPAGE_URL,
    APP_PERMISSIONS,
    DEFAULT_APP_NAME_PREFIX,
    GITHUB_API_TIMEOUT_SECONDS,
    SCRIPT_DIR,
    build_app_manifest,
    create_callback_app,
    exchange_code_for_credentials,
    generate_manifest_html,
    main,
    open_manifest_in_browser,
    parse_args,
    start_callback_server,
    stop_callback_server,
    wait_for_app_installation,
)


REPO_ROOT = SCRIPT_DIR.parents[1]
STAGING_APP_NAME = "saas-deploy-staging-chart-dispatcher"
OLD_STAGING_APP_NAME = "saas-deploy-staging-chart-dispatcher-openhands"


def make_code_holder(code: str | None = "test-code") -> MagicMock:
    holder = MagicMock()
    holder.code = code
    holder.code_received = threading.Event()
    holder.code_received.set()
    holder.installation_url = None
    return holder


def make_response(data: dict) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = data
    return response


def wait_for_server(url: str, timeout: float = 2.0, interval: float = 0.05) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            httpx.get(url, timeout=0.1)
            return True
        except (httpx.ConnectError, httpx.TimeoutException):
            time.sleep(interval)
    return False


def wait_for_server_shutdown(url: str, timeout: float = 2.0, interval: float = 0.05) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            httpx.get(url, timeout=0.1)
            time.sleep(interval)
        except (httpx.ConnectError, httpx.TimeoutException):
            return True
    return False


def callback_url(server_handle, code: str = "test") -> str:
    port = server_handle.server.servers[0].sockets[0].getsockname()[1]
    return f"http://127.0.0.1:{port}/callback?code={code}"


@contextmanager
def temporary_pem_file(pem_path: Path):
    if pem_path.exists():
        pem_path.unlink()
    try:
        yield pem_path
    finally:
        if pem_path.exists():
            pem_path.unlink()


@contextmanager
def mocked_main_flow(data: dict, code: str | None = "test-code", installed: bool = True):
    holder = make_code_holder(code)
    handle = MagicMock()
    with patch("create_chart_bump_dispatcher.start_callback_server", return_value=(handle, holder)) as start:
        with patch("create_chart_bump_dispatcher.open_manifest_in_browser", return_value="/tmp/chart-bump-manifest.html") as browser:
            with patch("create_chart_bump_dispatcher.stop_callback_server") as stop:
                with patch("create_chart_bump_dispatcher.exchange_code_for_credentials", return_value=data) as exchange:
                    with patch("create_chart_bump_dispatcher.wait_for_app_installation", return_value=installed) as wait:
                        yield {
                            "code_holder": holder,
                            "exchange": exchange,
                            "open_browser": browser,
                            "server_handle": handle,
                            "start_server": start,
                            "stop_server": stop,
                            "wait_for_installation": wait,
                        }


class TestManifest:
    def test_uses_provided_app_name(self):
        assert build_app_manifest(app_name=STAGING_APP_NAME)["name"] == STAGING_APP_NAME

    def test_default_app_name_is_generic_and_unique(self):
        first = build_app_manifest()["name"]
        second = build_app_manifest()["name"]

        assert first.startswith(f"{DEFAULT_APP_NAME_PREFIX}-")
        assert second.startswith(f"{DEFAULT_APP_NAME_PREFIX}-")
        assert first != second

    def test_homepage_points_at_saas_deploy(self):
        assert build_app_manifest()["url"] == APP_HOMEPAGE_URL

    def test_redirect_url_uses_callback_port(self):
        assert build_app_manifest(callback_port=18080)["redirect_url"] == "http://localhost:18080/callback"

    def test_permissions_are_only_dispatch_minimum(self):
        manifest = build_app_manifest()

        assert manifest["default_permissions"] == APP_PERMISSIONS
        assert manifest["default_permissions"] == {
            "contents": "write",
            "metadata": "read",
        }
        assert "pull_requests" not in manifest["default_permissions"]
        assert "workflows" not in manifest["default_permissions"]

    def test_manifest_has_no_webhook_or_events(self):
        manifest = build_app_manifest()

        assert manifest["default_events"] == []
        assert "hook_attributes" not in manifest

    def test_manifest_does_not_request_oauth_on_install(self):
        assert build_app_manifest()["request_oauth_on_install"] is False


class TestManifestHtml:
    def test_posts_manifest_to_user_app_page_by_default(self):
        html = generate_manifest_html(build_app_manifest(app_name="my-app"))

        assert 'action="https://github.com/settings/apps/new"' in html
        assert 'method="post"' in html
        assert "my-app" in html
        assert "&quot;" in html

    def test_posts_manifest_to_org_when_requested(self):
        html = generate_manifest_html(build_app_manifest(), org="OpenHands")

        assert 'action="https://github.com/organizations/OpenHands/settings/apps/new"' in html

    def test_escapes_org_in_form_action(self):
        html = generate_manifest_html(build_app_manifest(), org='ev"il')

        assert 'organizations/ev"il/' not in html
        assert "ev&quot;il" in html

    def test_temp_manifest_is_removed_when_browser_open_fails(self):
        captured = {}

        def fail_open(url: str):
            captured["url"] = url
            raise RuntimeError("headless")

        with patch("create_chart_bump_dispatcher.webbrowser.open", side_effect=fail_open):
            with pytest.raises(RuntimeError):
                open_manifest_in_browser(app_name="my-app")

        assert not Path(captured["url"].removeprefix("file://")).exists()


class TestCallbackServer:
    def test_callback_captures_code_and_redirects_same_tab_to_install_url(self):
        app, holder = create_callback_app()
        response = TestClient(app).get("/callback?code=abc123")

        assert response.status_code == 200
        assert holder.code == "abc123"
        assert holder.code_received.is_set()
        assert "/installation-url" in response.text
        assert "window.location.href" in response.text
        assert "window.open" not in response.text

    def test_callback_rejects_missing_code(self):
        app, holder = create_callback_app()
        response = TestClient(app).get("/callback")

        assert response.status_code == 400
        assert holder.code is None

    def test_installation_url_endpoint_returns_url_when_known(self):
        app, holder = create_callback_app()
        holder.installation_url = "https://github.com/apps/x/installations/new"

        assert TestClient(app).get("/installation-url").json() == {
            "url": "https://github.com/apps/x/installations/new"
        }

    def test_server_lifecycle(self):
        handle, holder = start_callback_server(port=0)
        try:
            shutdown_url = callback_url(handle, "after-stop")
            assert wait_for_server(callback_url(handle, "first"))
            assert holder.code == "first"
        finally:
            stop_callback_server(handle)

        assert wait_for_server_shutdown(shutdown_url)

    def test_server_start_failure_is_visible(self):
        fake_server = MagicMock()
        fake_server.started = False
        fake_server.run.return_value = None

        with patch("create_chart_bump_dispatcher.uvicorn.Server", return_value=fake_server):
            with pytest.raises(RuntimeError, match="failed to start"):
                start_callback_server(port=18080)


class TestCredentialExchange:
    def test_exchange_posts_to_manifest_conversion_endpoint(self):
        with patch("create_chart_bump_dispatcher.requests.post", return_value=make_response({"id": 123})) as post:
            assert exchange_code_for_credentials("code") == {"id": 123}

        post.assert_called_once_with(
            "https://api.github.com/app-manifests/code/conversions",
            headers={"Accept": "application/vnd.github+json"},
            timeout=GITHUB_API_TIMEOUT_SECONDS,
        )


class TestMainFlow:
    def test_dry_run_does_not_start_interactive_flow(self):
        with patch("create_chart_bump_dispatcher.start_callback_server") as start:
            main(dry_run=True, app_name="my-app", org="OpenHands")

        start.assert_not_called()

    def test_main_passes_org_and_callback_port_to_browser_manifest(self):
        with mocked_main_flow({"id": 123}) as mocks:
            main(dry_run=False, app_name="my-app", org="OpenHands", callback_port=18080)

        mocks["start_server"].assert_called_once_with(port=18080)
        mocks["open_browser"].assert_called_once_with(
            app_name="my-app",
            callback_port=18080,
            org="OpenHands",
        )

    @pytest.mark.parametrize("bad", ["", "../evil", "a/b", r"a\b", "..", "."])
    def test_rejects_unsafe_app_name_before_io(self, bad, capsys):
        with patch("create_chart_bump_dispatcher.start_callback_server") as start:
            with pytest.raises(SystemExit) as exc:
                main(dry_run=True, app_name=bad)

        assert exc.value.code == 1
        assert "invalid" in capsys.readouterr().out.lower()
        start.assert_not_called()

    def test_main_exits_cleanly_on_missing_callback_code(self, capsys):
        with mocked_main_flow({"id": 123}, code=None) as mocks:
            with pytest.raises(SystemExit) as exc:
                main(dry_run=False, app_name="my-app")

        assert exc.value.code == 1
        assert "timed out" in capsys.readouterr().out.lower()
        mocks["stop_server"].assert_called_once_with(mocks["server_handle"])

    @pytest.mark.parametrize(
        "exc",
        [requests.RequestException("boom"), ValueError("non-json")],
    )
    def test_main_reports_manifest_conversion_failures(self, exc, capsys):
        holder = make_code_holder()
        with patch("create_chart_bump_dispatcher.start_callback_server", return_value=(MagicMock(), holder)):
            with patch("create_chart_bump_dispatcher.open_manifest_in_browser", return_value="/tmp/chart-bump.html"):
                with patch("create_chart_bump_dispatcher.stop_callback_server") as stop:
                    with patch("create_chart_bump_dispatcher.exchange_code_for_credentials", side_effect=exc):
                        with pytest.raises(SystemExit) as exit_info:
                            main(dry_run=False, app_name="my-app")

        assert exit_info.value.code == 1
        assert "failed to exchange" in capsys.readouterr().out.lower()
        stop.assert_called_once()

    def test_main_sets_installation_url_and_polls_with_app_auth(self):
        with mocked_main_flow({"id": 123, "slug": STAGING_APP_NAME, "pem": "pem"}) as mocks:
            main(dry_run=False, app_name="my-app")

        assert mocks["code_holder"].installation_url == (
            f"https://github.com/apps/{STAGING_APP_NAME}/installations/new"
        )
        mocks["wait_for_installation"].assert_called_once_with(app_id=123, private_key="pem")

    def test_main_exits_nonzero_when_installation_is_not_detected(self, capsys):
        pem_path = SCRIPT_DIR / "keys" / "my-app.pem"
        with temporary_pem_file(pem_path):
            with mocked_main_flow({"id": 123, "slug": "s", "pem": "pem"}, installed=False):
                with pytest.raises(SystemExit) as exc:
                    main(dry_run=False, app_name="my-app")

            assert exc.value.code == 1
            assert pem_path.exists()

        out = capsys.readouterr().out.lower()
        assert "timed out waiting for app installation" in out
        assert "chart_bump_dispatcher_app_id" in out

    def test_main_removes_temporary_manifest_html_after_use(self, tmp_path):
        manifest = tmp_path / "manifest.html"
        manifest.write_text("<html></html>")

        with mocked_main_flow({"id": 123}) as mocks:
            mocks["open_browser"].return_value = str(manifest)
            main(dry_run=False, app_name="my-app")

        assert not manifest.exists()

    def test_main_writes_private_key_owner_only_and_does_not_leak_secrets(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        pem_path = SCRIPT_DIR / "keys" / "my-app.pem"
        pem = "-----BEGIN RSA PRIVATE KEY-----\nsecret-key\n-----END RSA PRIVATE KEY-----"

        with temporary_pem_file(pem_path):
            with mocked_main_flow(
                {
                    "id": 123,
                    "slug": STAGING_APP_NAME,
                    "client_secret": "client-secret",
                    "webhook_secret": "webhook-secret",
                    "pem": pem,
                }
            ):
                main(dry_run=False, app_name="my-app")

            assert pem_path.exists()
            assert pem_path.read_text() == pem
            assert (pem_path.stat().st_mode & 0o777) == 0o600
            assert not (tmp_path / "keys" / "my-app.pem").exists()

        out = capsys.readouterr().out
        assert "CHART_BUMP_DISPATCHER_APP_ID" in out
        assert "= 123" in out
        assert "./scripts/create_chart_bump_dispatcher/keys/my-app.pem" in out
        assert "client-secret" not in out
        assert "webhook-secret" not in out
        assert "secret-key" not in out

    def test_script_output_is_generic_not_dev_or_staging_specific(self, capsys):
        with mocked_main_flow({"id": 123, "slug": "s"}):
            main(dry_run=False, app_name="my-app")

        out = capsys.readouterr().out.lower()
        assert "staging-chart" not in out
        assert "dev-chart" not in out


class TestParseArgs:
    def test_usage_arguments(self, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "script",
                "--org",
                "OpenHands",
                "--app-name",
                STAGING_APP_NAME,
                "--callback-port",
                "18080",
                "--dry-run",
            ],
        )

        args = parse_args()

        assert args.org == "OpenHands"
        assert args.app_name == STAGING_APP_NAME
        assert args.callback_port == 18080
        assert args.dry_run is True


class TestOperatorDocs:
    def test_staging_examples_use_short_dispatcher_app_name(self):
        docs = [
            REPO_ROOT / "docs" / "staging-chart-bumps.md",
            SCRIPT_DIR / "README.md",
        ]

        for path in docs:
            text = path.read_text()
            assert f"--app-name {STAGING_APP_NAME}" in text
            assert OLD_STAGING_APP_NAME not in text


class TestWaitForInstallation:
    def test_returns_false_when_app_auth_fails(self, capsys):
        with patch("create_chart_bump_dispatcher.Auth.AppAuth", side_effect=RuntimeError("bad auth")):
            assert wait_for_app_installation(123, "pem", timeout=1) is False

        assert "could not authenticate" in capsys.readouterr().out.lower()

    def test_retries_transient_installation_lookup_failures(self, capsys):
        integration = MagicMock()
        integration.get_installations.side_effect = [
            RuntimeError("api unavailable"),
            iter([MagicMock()]),
        ]

        with patch("create_chart_bump_dispatcher.Auth.AppAuth"):
            with patch("create_chart_bump_dispatcher.GithubIntegration", return_value=integration):
                with patch("create_chart_bump_dispatcher.time.time", side_effect=[0, 0, 0]):
                    with patch("create_chart_bump_dispatcher.time.sleep") as sleep:
                        assert wait_for_app_installation(123, "pem", timeout=1) is True

        sleep.assert_called_once()
        assert "error checking installations" in capsys.readouterr().out.lower()
