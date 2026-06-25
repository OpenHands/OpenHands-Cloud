#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyGithub", "requests", "fastapi", "uvicorn", "pytest", "httpx"]
# ///
"""Unit tests for create_github_app.py."""

import os
import requests
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import httpx
import pytest
from fastapi.testclient import TestClient

# Add the script's directory to sys.path so we can import it directly
sys.path.insert(0, str(Path(__file__).parent))

import create_github_app
from create_github_app import (
    SCRIPT_DIR,
    build_app_manifest,
    create_callback_app,
    create_github_app as create_github_app_func,
    exchange_code_for_credentials,
    generate_manifest_html,
    main,
    open_manifest_in_browser,
    parse_args,
    start_callback_server,
    stop_callback_server,
    wait_for_app_installation,
)


# --- Test Helpers ---

def make_mock_code_holder(code: str = "test-code") -> MagicMock:
    """Create a mock code holder that simulates receiving an OAuth code."""
    code_holder = MagicMock()
    code_holder.code = code
    code_holder.code_received = threading.Event()
    code_holder.code_received.set()
    code_holder.installation_url = None
    return code_holder


def make_mock_response(response_data: dict) -> MagicMock:
    """Create a mock HTTP response with the given JSON data."""
    mock_response = MagicMock()
    mock_response.json.return_value = response_data
    mock_response.raise_for_status = MagicMock()
    return mock_response


def wait_for_server(url: str, timeout: float = 2.0, interval: float = 0.05) -> bool:
    """Poll until server at URL responds, or timeout.

    Returns True if server responded, False if timeout.
    More reliable than fixed time.sleep() for server startup tests.
    """
    start = time.time()
    while time.time() - start < timeout:
        try:
            httpx.get(url, timeout=0.1)
            return True
        except (httpx.ConnectError, httpx.TimeoutException):
            time.sleep(interval)
    return False


def wait_for_server_shutdown(url: str, timeout: float = 2.0, interval: float = 0.05) -> bool:
    """Poll until server at URL stops responding, or timeout.

    Returns True if server stopped, False if still running after timeout.
    """
    start = time.time()
    while time.time() - start < timeout:
        try:
            httpx.get(url, timeout=0.1)
            time.sleep(interval)
        except (httpx.ConnectError, httpx.TimeoutException):
            return True
    return False


@contextmanager
def temporary_pem_file(pem_path: Path):
    """Context manager that ensures a pem file is cleaned up after test.

    Removes the file before and after the test to ensure clean state.
    """
    if pem_path.exists():
        pem_path.unlink()
    try:
        yield pem_path
    finally:
        if pem_path.exists():
            pem_path.unlink()


@contextmanager
def mock_main_dependencies(response_data: dict, code: str = "test-code", installation_found: bool = True):
    """Context manager that mocks all external dependencies for main().

    Mocks: start_callback_server, open_manifest_in_browser, stop_callback_server,
           requests.post, wait_for_app_installation, and webbrowser.open.

    Yields a dict with references to all mocks for inspection.
    """
    code_holder = make_mock_code_holder(code)
    server_handle = MagicMock()
    mock_response = make_mock_response(response_data)

    with patch("create_github_app.start_callback_server", return_value=(server_handle, code_holder)) as mock_start:
        with patch("create_github_app.open_manifest_in_browser") as mock_browser:
            with patch("create_github_app.stop_callback_server") as mock_stop:
                with patch("create_github_app.requests.post", return_value=mock_response) as mock_post:
                    with patch("create_github_app.wait_for_app_installation", return_value=installation_found) as mock_wait:
                        with patch("create_github_app.webbrowser.open") as mock_wb:
                            yield {
                                "start_server": mock_start,
                                "open_browser": mock_browser,
                                "stop_server": mock_stop,
                                "post": mock_post,
                                "wait_for_installation": mock_wait,
                                "webbrowser": mock_wb,
                                "code_holder": code_holder,
                                "server_handle": server_handle,
                                "response": mock_response,
                            }


class TestNoChangesOutsideScriptFolder:
    """Tests to verify all file changes are contained within script folder."""

    def test_keys_saved_relative_to_script(self):
        """Test that keys are saved in keys/ subdirectory of script location."""
        pem_path = SCRIPT_DIR / "keys" / "test-app.pem"

        with temporary_pem_file(pem_path):
            with mock_main_dependencies({
                "id": 123,
                "pem": "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----",
            }):
                main(base_domain="example.com", dry_run=False, app_name="test-app")

            # Verify the pem was saved inside script dir/keys/
            assert pem_path.exists()
            assert pem_path.parent.name == "keys"
            assert pem_path.parent.parent == SCRIPT_DIR


class FakeGithubClient:
    """Fake GitHub client for testing without hitting real API."""

    def __init__(self):
        self.created_apps = []

    def create_app_from_manifest(self, manifest: dict) -> dict:
        """Record the app creation request and return fake result."""
        self.created_apps.append(manifest)
        return {
            "id": 12345,
            "name": manifest["name"],
            "html_url": f"https://github.com/apps/{manifest['name']}",
        }


class TestBuildAppManifest:
    """Tests for build_app_manifest function."""

    def test_manifest_contains_app_name_when_provided(self):
        """Test that manifest includes the app name when provided."""
        manifest = build_app_manifest(app_name="my-app", base_domain="example.com")
        assert manifest["name"] == "my-app"

    def test_manifest_app_name_defaults_to_unique_name(self):
        """Test that default app name is unique (has random suffix)."""
        manifest = build_app_manifest(base_domain="example.com")
        assert manifest["name"].startswith("openhands-")
        suffix = manifest["name"].split("-", 1)[1]
        assert len(suffix) == 8
        int(suffix, 16)  # Should be valid hex

    def test_default_app_names_are_different(self):
        """Test that multiple calls generate different default names."""
        manifest1 = build_app_manifest(base_domain="example.com")
        manifest2 = build_app_manifest(base_domain="example.com")
        assert manifest1["name"] != manifest2["name"]

    def test_manifest_url_uses_app_subdomain(self):
        """Test that manifest URL is https://app.BASE_DOMAIN."""
        manifest = build_app_manifest(base_domain="mycompany.com")
        assert manifest["url"] == "https://app.mycompany.com"

    def test_manifest_callback_url_format(self):
        """Test that callback URL is https://auth.app.BASE_DOMAIN/realms/allhands/broker/github/endpoint."""
        manifest = build_app_manifest(base_domain="mycompany.com")
        assert manifest["callback_urls"][0] == "https://auth.app.mycompany.com/realms/allhands/broker/github/endpoint"

    @pytest.mark.parametrize(
        "permission,expected_level",
        [
            ("actions", "write"),
            ("contents", "write"),
            ("emails", "read"),
            ("issues", "write"),
            ("metadata", "read"),
            ("organization_events", "read"),
            ("pull_requests", "write"),
            ("repository_hooks", "write"),
            ("statuses", "write"),
            ("workflows", "write"),
        ],
    )
    def test_manifest_permission(self, permission, expected_level):
        """Test that manifest has correct permission level."""
        manifest = build_app_manifest(base_domain="example.com")
        assert manifest["default_permissions"][permission] == expected_level

    def test_manifest_has_only_expected_permissions(self):
        """Test that manifest has exactly the expected permissions, no more."""
        manifest = build_app_manifest(base_domain="example.com")
        expected = {"actions", "contents", "emails", "issues", "metadata", "organization_events", "pull_requests", "repository_hooks", "statuses", "workflows"}
        assert set(manifest["default_permissions"].keys()) == expected

    def test_manifest_webhook_url(self):
        """Test that hook_attributes webhook URL is https://app.BASE_DOMAIN/integration/github/events."""
        manifest = build_app_manifest(base_domain="mycompany.com")
        assert manifest["hook_attributes"]["url"] == "https://app.mycompany.com/integration/github/events"

    def test_manifest_redirect_url(self):
        """Test that redirect_url is set for GitHub to redirect after app creation."""
        manifest = build_app_manifest(base_domain="example.com")
        assert "redirect_url" in manifest
        assert manifest["redirect_url"] == "http://localhost:9876/callback"

    def test_manifest_does_not_request_oauth_on_install(self):
        """Test that OAuth on install is disabled; Keycloak handles user OAuth at login time."""
        manifest = build_app_manifest(base_domain="example.com")
        assert manifest["request_oauth_on_install"] is False

    def test_manifest_callback_urls_has_no_localhost(self):
        """Test that callback_urls never contains localhost so no temporary URL persists in app settings."""
        manifest = build_app_manifest(base_domain="example.com")
        for url in manifest["callback_urls"]:
            assert "localhost" not in url

    @pytest.mark.parametrize("event", [
        "issue_comment",
        "pull_request",
        "pull_request_review_comment",
    ])
    def test_manifest_subscribes_to_event(self, event):
        """Test that manifest subscribes to required GitHub events."""
        manifest = build_app_manifest(base_domain="example.com")
        assert event in manifest["default_events"]

    def test_manifest_has_only_expected_events(self):
        """Test that manifest subscribes to exactly the expected events, no more."""
        manifest = build_app_manifest(base_domain="example.com")
        expected = {"issue_comment", "pull_request", "pull_request_review_comment"}
        assert set(manifest["default_events"]) == expected


class TestGenerateManifestHtml:
    """Tests for generate_manifest_html function."""

    def test_html_contains_post_form_to_github(self):
        """Test that HTML form POSTs to GitHub settings."""
        manifest = build_app_manifest(base_domain="example.com")
        html = generate_manifest_html(manifest)
        assert 'action="https://github.com/settings/apps/new"' in html
        assert 'method="post"' in html

    def test_html_contains_manifest_with_app_name(self):
        """Test that HTML form contains manifest with app name."""
        manifest = build_app_manifest(base_domain="example.com", app_name="test-app")
        html = generate_manifest_html(manifest)
        # The app name should be in the HTML (HTML-escaped)
        assert "test-app" in html

    def test_html_escapes_manifest_json_for_attribute(self):
        """Test that manifest JSON is HTML-escaped for safe embedding in attribute."""
        manifest = build_app_manifest(base_domain="example.com", app_name="test-app")
        html = generate_manifest_html(manifest)
        # Double quotes in JSON should be escaped as &quot; for HTML attribute
        assert "&quot;" in html or 'value="' in html

    def test_html_auto_submits_form(self):
        """Test that HTML includes auto-submit script."""
        manifest = build_app_manifest(base_domain="example.com")
        html = generate_manifest_html(manifest)
        assert "submit()" in html


class TestOpenManifestInBrowser:
    """Tests for open_manifest_in_browser function."""

    def test_writes_html_to_temp_file(self):
        """Test that HTML is written to a temp file."""
        with patch("create_github_app.webbrowser.open"):
            filepath = open_manifest_in_browser(base_domain="example.com")
            assert os.path.exists(filepath)
            assert filepath.endswith(".html")
            with open(filepath) as f:
                content = f.read()
            assert "https://github.com/settings/apps/new" in content
            os.unlink(filepath)

    def test_opens_browser_with_file_url(self):
        """Test that browser is opened with file:// URL."""
        with patch("create_github_app.webbrowser.open") as mock_open:
            filepath = open_manifest_in_browser(base_domain="example.com")
            mock_open.assert_called_once_with(f"file://{filepath}")
            os.unlink(filepath)

    def test_cleans_up_temp_file_when_browser_open_fails(self):
        """Test that a headless/browser failure does not leave the manifest temp file behind."""
        captured = {}

        def fail_open(url):
            captured["url"] = url
            raise RuntimeError("no browser available")

        with patch("create_github_app.webbrowser.open", side_effect=fail_open):
            with pytest.raises(RuntimeError):
                open_manifest_in_browser(base_domain="example.com")

        path = captured["url"].removeprefix("file://")
        assert not os.path.exists(path)


class TestExchangeCodeForCredentials:
    """Tests for exchange_code_for_credentials function."""

    def test_posts_to_github_api(self):
        """Test that it posts to the correct GitHub API endpoint."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 123, "client_secret": "secret"}
        mock_response.raise_for_status = MagicMock()

        with patch("create_github_app.requests.post", return_value=mock_response) as mock_post:
            exchange_code_for_credentials("test-code")
            mock_post.assert_called_once_with(
                "https://api.github.com/app-manifests/test-code/conversions",
                headers={"Accept": "application/vnd.github+json"},
                timeout=create_github_app.GITHUB_API_TIMEOUT_SECONDS,
            )

    def test_returns_credentials(self):
        """Test that it returns the credentials from the API response."""
        expected = {
            "id": 123,
            "client_id": "client-123",
            "client_secret": "secret-456",
            "pem": "-----BEGIN RSA PRIVATE KEY-----",
            "webhook_secret": "webhook-secret",
        }
        mock_response = MagicMock()
        mock_response.json.return_value = expected
        mock_response.raise_for_status = MagicMock()

        with patch("create_github_app.requests.post", return_value=mock_response):
            result = exchange_code_for_credentials("test-code")
            assert result == expected


class TestCreateGithubApp:
    """Tests for create_github_app function."""

    def test_creates_app_via_client(self):
        """Test that create_github_app calls the client with manifest."""
        client = FakeGithubClient()

        result = create_github_app_func(
            base_domain="test.com",
            github_client=client,
            app_name="test-app",
        )

        assert len(client.created_apps) == 1
        assert client.created_apps[0]["name"] == "test-app"
        assert result["id"] == 12345

    def test_returns_app_details(self):
        """Test that create_github_app returns the created app details."""
        client = FakeGithubClient()

        result = create_github_app_func(
            base_domain="example.com",
            github_client=client,
            app_name="my-app",
        )

        assert result["name"] == "my-app"
        assert "html_url" in result


class TestDryRun:
    """Tests for dry-run functionality."""

    def test_dry_run_does_not_create_app(self, capsys):
        """Test that dry-run mode does not create a GitHub app."""
        client = FakeGithubClient()

        main(
            base_domain="example.com",
            dry_run=True,
            github_client=client,
            app_name="test-app",
        )

        assert len(client.created_apps) == 0

    def test_dry_run_prints_what_would_be_created(self, capsys):
        """Test that dry-run mode prints intent message."""
        client = FakeGithubClient()

        main(
            base_domain="example.com",
            dry_run=True,
            github_client=client,
            app_name="test-app",
        )

        captured = capsys.readouterr()
        assert "test-app" in captured.out
        assert "example.com" in captured.out
        assert "Would create" in captured.out


class TestMainInteractiveFlow:
    """Tests for main() interactive flow using callback server and user's default browser."""

    def test_opens_browser_with_callback_server(self, capsys):
        """Test that main opens browser after starting callback server."""
        with mock_main_dependencies({"id": 123}) as mocks:
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        mocks["open_browser"].assert_called_once()

    @pytest.mark.parametrize("expected_text,description", [
        ("Click", "instructs user to click"),
        ("Create GitHub App for", "mentions button text with username placeholder"),
        ("Waiting", "indicates waiting for callback"),
    ])
    def test_console_output_guides_user(self, capsys, expected_text, description):
        """Test that main() prints helpful guidance messages to the user."""
        with mock_main_dependencies({"id": 123}):
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        captured = capsys.readouterr()
        assert expected_text in captured.out, f"Output should contain '{expected_text}' ({description})"

    def test_exchanges_code_and_prints_credentials(self, capsys):
        """Test that main exchanges code and prints the credentials."""
        with mock_main_dependencies({
            "id": 123,
            "name": "my-app",
            "client_id": "Iv1.abc123",
            "client_secret": "secret456",
            "webhook_secret": "whsec789",
        }):
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        captured = capsys.readouterr()
        # Verify labels
        assert "GitHub App Client ID: Iv1.abc123" in captured.out
        assert "GitHub App Client Secret: secret456" in captured.out
        assert "GitHub App ID: 123" in captured.out
        assert "GitHub App Webhook Secret: whsec789" in captured.out

    def test_slug_is_displayed_when_present_in_credentials(self, capsys):
        """Test that GitHub App Slug is displayed when present in credentials."""
        with mock_main_dependencies({
            "id": 123,
            "slug": "my-openhands-app",
            "client_id": "Iv1.abc123",
            "client_secret": "secret456",
            "webhook_secret": "whsec789",
        }):
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        captured = capsys.readouterr()
        assert "GitHub App Slug: my-openhands-app" in captured.out

    def test_slug_appears_after_app_id_and_before_webhook_secret(self, capsys):
        """Test that GitHub App Slug is displayed immediately after GitHub App ID."""
        with mock_main_dependencies({
            "id": 123,
            "slug": "my-openhands-app",
            "client_id": "Iv1.abc123",
            "client_secret": "secret456",
            "webhook_secret": "whsec789",
        }):
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        captured = capsys.readouterr()
        id_pos = captured.out.index("GitHub App ID:")
        slug_pos = captured.out.index("GitHub App Slug:")
        webhook_pos = captured.out.index("GitHub App Webhook Secret:")
        assert id_pos < slug_pos < webhook_pos

    def test_slug_is_omitted_when_absent_from_credentials(self, capsys):
        """Test that GitHub App Slug line is not printed when absent from credentials."""
        with mock_main_dependencies({
            "id": 123,
            "client_id": "Iv1.abc123",
            "client_secret": "secret456",
            "webhook_secret": "whsec789",
        }):
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        captured = capsys.readouterr()
        assert "GitHub App Slug" not in captured.out

    def test_saves_pem_to_keys_directory_in_script_folder(self, capsys, monkeypatch, tmp_path):
        """Test that pem is saved to keys/ directory in the script's folder, not cwd."""
        # Change to a different directory to verify keys are NOT created in cwd
        monkeypatch.chdir(tmp_path)

        # Get the script directory (where create_github_app.py lives)
        script_dir = Path(create_github_app.__file__).parent
        pem_path = script_dir / "keys" / "my-app.pem"

        with temporary_pem_file(pem_path):
            with mock_main_dependencies({
                "id": 123,
                "name": "my-app",
                "client_id": "Iv1.abc123",
                "client_secret": "secret456",
                "pem": "-----BEGIN RSA PRIVATE KEY-----\ntest-key-content\n-----END RSA PRIVATE KEY-----",
            }):
                main(base_domain="example.com", dry_run=False, app_name="my-app")

            # Verify pem file was NOT created in cwd
            assert not (tmp_path / "keys" / "my-app.pem").exists()

            # Verify pem file was created in keys/ directory relative to script
            assert pem_path.exists()
            assert pem_path.read_text() == "-----BEGIN RSA PRIVATE KEY-----\ntest-key-content\n-----END RSA PRIVATE KEY-----"

            # Verify output shows the full path from repo root
            captured = capsys.readouterr()
            assert "GitHub App Private Key: ./scripts/create_github_app/keys/my-app.pem" in captured.out


class TestParseArgs:
    """Tests for parse_args function."""

    def test_dry_run_argument(self, monkeypatch):
        """Test that --dry-run argument works."""
        monkeypatch.setattr(sys, "argv", ["script", "--dry-run", "--base-domain", "example.com"])
        args = parse_args()
        assert args.dry_run is True

    def test_app_name_defaults_to_none(self, monkeypatch):
        """Test that app_name defaults to None when not specified (unique name generated later)."""
        monkeypatch.setattr(sys, "argv", ["script", "--base-domain", "example.com"])
        args = parse_args()
        assert args.app_name is None

    def test_app_name_can_be_overridden(self, monkeypatch):
        """Test that --app-name argument allows custom value."""
        monkeypatch.setattr(sys, "argv", ["script", "--app-name", "custom-app", "--base-domain", "example.com"])
        args = parse_args()
        assert args.app_name == "custom-app"

    def test_base_domain_is_required(self, monkeypatch):
        """Test that --base-domain is required and errors when missing."""
        monkeypatch.setattr(sys, "argv", ["script"])
        with pytest.raises(SystemExit) as exc_info:
            parse_args()
        assert exc_info.value.code == 2  # argparse exits with 2 for missing required args

    def test_base_domain_accepts_value(self, monkeypatch):
        """Test that --base-domain argument accepts a value."""
        monkeypatch.setattr(sys, "argv", ["script", "--base-domain", "mycompany.com"])
        args = parse_args()
        assert args.base_domain == "mycompany.com"

    def test_org_defaults_to_none(self, monkeypatch):
        """Test that apps are created in the personal account unless --org is set."""
        monkeypatch.setattr(sys, "argv", ["script", "--base-domain", "example.com"])
        args = parse_args()
        assert args.org is None

    def test_org_can_be_set(self, monkeypatch):
        """Test that --org directs manifest creation to an organization."""
        monkeypatch.setattr(
            sys,
            "argv",
            ["script", "--base-domain", "example.com", "--org", "OpenHands"],
        )
        args = parse_args()
        assert args.org == "OpenHands"

    def test_callback_port_defaults_to_9876(self, monkeypatch):
        """Test that the local callback port defaults to the manifest redirect port."""
        monkeypatch.setattr(sys, "argv", ["script", "--base-domain", "example.com"])
        args = parse_args()
        assert args.callback_port == 9876

    def test_callback_port_can_be_overridden(self, monkeypatch):
        """Test that --callback-port lets operators avoid a busy local port."""
        monkeypatch.setattr(
            sys,
            "argv",
            ["script", "--base-domain", "example.com", "--callback-port", "18080"],
        )
        args = parse_args()
        assert args.callback_port == 18080


class TestCallbackServer:
    """Tests for the FastAPI callback server that captures the GitHub OAuth code."""

    def test_callback_endpoint_extracts_code_from_query_param(self):
        """Test that /callback extracts the code from query parameter."""
        app, code_holder = create_callback_app()
        client = TestClient(app)

        response = client.get("/callback?code=test-auth-code-123")

        assert response.status_code == 200
        assert code_holder.code == "test-auth-code-123"

    def test_callback_endpoint_guides_user_to_install(self):
        """Test that /callback tells the user to continue to install, not to close the window."""
        app, _ = create_callback_app()
        client = TestClient(app)

        response = client.get("/callback?code=some-code")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Created app! Navigating to install page..." in response.text

    def test_callback_endpoint_polls_installation_url_endpoint(self):
        """Test that /callback HTML polls /installation-url to get the install URL."""
        app, _ = create_callback_app()
        client = TestClient(app)

        response = client.get("/callback?code=some-code")

        assert "/installation-url" in response.text

    def test_callback_endpoint_limits_install_url_polling_attempts(self):
        """Test that /callback stops polling after a bounded number of attempts."""
        app, _ = create_callback_app()
        client = TestClient(app)

        response = client.get("/callback?code=some-code")

        assert "var attempts = 0;" in response.text
        assert "var maxAttempts = 60;" in response.text
        assert "if (attempts++ >= maxAttempts)" in response.text

    def test_callback_endpoint_shows_timeout_error_message(self):
        """Test that /callback renders a helpful message if install URL polling times out."""
        app, _ = create_callback_app()
        client = TestClient(app)

        response = client.get("/callback?code=some-code")

        assert "Installation URL not available. Check the terminal for instructions." in response.text


    def test_callback_endpoint_redirects_same_tab(self):
        """Test that /callback redirects in the same tab (window.location.href) not a new window."""
        app, _ = create_callback_app()
        client = TestClient(app)

        response = client.get("/callback?code=some-code")

        assert "window.location.href" in response.text
        assert "window.open" not in response.text

    def test_installation_url_endpoint_returns_null_when_not_set(self):
        """Test that /installation-url returns null before the slug is known from credentials."""
        app, _ = create_callback_app()
        client = TestClient(app)

        response = client.get("/installation-url")

        assert response.status_code == 200
        assert response.json() == {"url": None}

    def test_installation_url_endpoint_returns_url_when_set(self):
        """Test that /installation-url returns the URL once main() sets it from the credentials slug."""
        app, code_holder = create_callback_app()
        client = TestClient(app)

        code_holder.installation_url = "https://github.com/apps/real-slug/installations/new"
        response = client.get("/installation-url")

        assert response.json() == {"url": "https://github.com/apps/real-slug/installations/new"}

    def test_callback_endpoint_handles_missing_code(self):
        """Test that /callback handles missing code parameter gracefully."""
        app, code_holder = create_callback_app()
        client = TestClient(app)

        response = client.get("/callback")

        assert response.status_code == 400
        assert code_holder.code is None

    def test_callback_endpoint_signals_code_received(self):
        """Test that /callback sets an event when code is received."""
        app, code_holder = create_callback_app()
        client = TestClient(app)

        assert not code_holder.code_received.is_set()
        client.get("/callback?code=test-code")
        assert code_holder.code_received.is_set()


class TestCallbackServerLifecycle:
    """Tests for starting and stopping the callback server."""

    def test_start_callback_server_runs_on_specified_port(self):
        """Test that the callback server runs on localhost:port."""
        server_handle, code_holder = start_callback_server(port=18234)
        try:
            # Poll until server is ready (more reliable than fixed sleep)
            assert wait_for_server("http://localhost:18234/callback?code=test-code")
            assert code_holder.code == "test-code"
        finally:
            stop_callback_server(server_handle)

    def test_stop_callback_server_shuts_down_cleanly(self):
        """Test that stop_callback_server shuts down the server."""
        server_handle, _ = start_callback_server(port=18235)
        # Wait for server to start
        assert wait_for_server("http://localhost:18235/callback?code=init")
        stop_callback_server(server_handle)
        # Wait for server to shutdown
        assert wait_for_server_shutdown("http://localhost:18235/callback?code=test")

        with pytest.raises(httpx.ConnectError):
            httpx.get("http://localhost:18235/callback?code=test", timeout=0.1)

    def test_start_callback_server_fails_fast_when_server_thread_exits(self):
        """Test that callback startup failure is reported immediately instead of timing out later."""
        fake_server = MagicMock()
        fake_server.started = False
        fake_server.run.return_value = None

        with patch("create_github_app.uvicorn.Server", return_value=fake_server):
            with pytest.raises(RuntimeError) as exc_info:
                start_callback_server(port=18080)

        assert "failed to start" in str(exc_info.value).lower()
        assert "18080" in str(exc_info.value)

    def test_stop_callback_server_warns_if_thread_does_not_exit(self, capsys):
        """Test that cleanup trouble is visible when the callback thread keeps running."""
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        handle = MagicMock()
        handle.thread = mock_thread

        stop_callback_server(handle)

        mock_thread.join.assert_called_once()
        mock_thread.is_alive.assert_called()
        captured = capsys.readouterr()
        assert "Warning" in captured.out
        assert "did not exit within 5s" in captured.out


class TestManifestRedirectUrl:
    """Tests for manifest redirect_url with callback port."""

    def test_manifest_redirect_url_uses_callback_port(self):
        """Test that manifest redirect_url points to localhost with specified port."""
        manifest = build_app_manifest(base_domain="example.com", callback_port=18080)
        assert manifest["redirect_url"] == "http://localhost:18080/callback"

    def test_manifest_redirect_url_defaults_to_port_9876(self):
        """Test that manifest redirect_url defaults to port 9876 when no port specified."""
        manifest = build_app_manifest(base_domain="example.com")
        assert manifest["redirect_url"] == "http://localhost:9876/callback"


class TestMainWithCallbackServer:
    """Tests for main() integration with callback server."""

    def test_main_starts_callback_server_before_opening_browser(self):
        """Test that main starts callback server before opening browser."""
        call_order = []

        def track_start_server(*args, **kwargs):
            call_order.append("start_server")
            return MagicMock(), make_mock_code_holder()

        def track_open_browser(*args, **kwargs):
            call_order.append("open_browser")
            return "/tmp/test.html"

        mock_response = make_mock_response({"id": 123})

        with patch("create_github_app.start_callback_server", side_effect=track_start_server):
            with patch("create_github_app.open_manifest_in_browser", side_effect=track_open_browser):
                with patch("create_github_app.stop_callback_server"):
                    with patch("create_github_app.requests.post", return_value=mock_response):
                        with patch("create_github_app.wait_for_app_installation", return_value=True):
                            with patch("create_github_app.webbrowser.open"):
                                main(base_domain="example.com", dry_run=False, app_name="my-app")

        assert call_order == ["start_server", "open_browser"]

    def test_main_waits_for_code_from_callback_server(self, capsys):
        """Test that main waits for code from callback server instead of prompting."""
        with mock_main_dependencies(
            {"id": 123, "client_id": "test-client"},
            code="received-code-from-callback"
        ) as mocks:
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        # Verify the code from callback was used
        mocks["post"].assert_called_once()
        call_url = mocks["post"].call_args[0][0]
        assert "received-code-from-callback" in call_url

    def test_main_stops_callback_server_after_receiving_code(self):
        """Test that main stops callback server after receiving the code."""
        with mock_main_dependencies({"id": 123}) as mocks:
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        mocks["stop_server"].assert_called_once_with(mocks["server_handle"])

    def test_main_no_longer_prompts_for_code_input(self):
        """Test that main does not prompt for manual code input."""
        with mock_main_dependencies({"id": 123}):
            with patch("builtins.input") as mock_input:
                main(base_domain="example.com", dry_run=False, app_name="my-app")

        # input() should never be called
        mock_input.assert_not_called()

    @pytest.mark.parametrize(
        "exc",
        [requests.RequestException("boom"), ValueError("non-JSON body")],
    )
    def test_main_exits_nonzero_on_credential_exchange_failure(self, capsys, exc):
        """Test that a failed manifest-code exchange is a clean CLI failure."""
        code_holder = make_mock_code_holder()
        with patch("create_github_app.start_callback_server", return_value=(MagicMock(), code_holder)):
            with patch("create_github_app.open_manifest_in_browser", return_value="/tmp/_manifest.html"):
                with patch("create_github_app.stop_callback_server") as mock_stop:
                    with patch("create_github_app.exchange_code_for_credentials", side_effect=exc):
                        with pytest.raises(SystemExit) as exit_info:
                            main(base_domain="example.com", dry_run=False, app_name="my-app")

        assert exit_info.value.code == 1
        assert "failed to exchange" in capsys.readouterr().out.lower()
        mock_stop.assert_called_once()

    def test_main_exits_nonzero_on_auth_code_timeout(self, capsys):
        """Test that never receiving GitHub's callback fails the command."""
        code_holder = make_mock_code_holder(code=None)
        with patch("create_github_app.start_callback_server", return_value=(MagicMock(), code_holder)):
            with patch("create_github_app.open_manifest_in_browser", return_value="/tmp/_manifest.html"):
                with patch("create_github_app.stop_callback_server") as mock_stop:
                    with pytest.raises(SystemExit) as exit_info:
                        main(base_domain="example.com", dry_run=False, app_name="my-app")

        assert exit_info.value.code == 1
        assert "timed out" in capsys.readouterr().out.lower()
        mock_stop.assert_called_once()

    @pytest.mark.parametrize("bad", ["", "../evil", "a/b", r"a\b", "..", "."])
    def test_main_rejects_unsafe_app_name_before_io(self, capsys, bad):
        """Test that app names cannot escape the keys directory when saved."""
        with patch("create_github_app.start_callback_server") as mock_start:
            with pytest.raises(SystemExit) as exit_info:
                main(base_domain="example.com", dry_run=True, app_name=bad)

        assert exit_info.value.code == 1
        assert "invalid" in capsys.readouterr().out.lower()
        mock_start.assert_not_called()

    def test_main_passes_org_and_callback_port_to_browser_manifest(self):
        """Test that main creates org-owned apps and keeps callback URLs in sync."""
        with mock_main_dependencies({"id": 123}) as mocks:
            main(
                base_domain="example.com",
                dry_run=False,
                app_name="my-app",
                org="OpenHands",
                callback_port=18080,
            )

        mocks["start_server"].assert_called_once_with(port=18080)
        mocks["open_browser"].assert_called_once_with(
            "example.com",
            "my-app",
            callback_port=18080,
            org="OpenHands",
        )

    def test_main_deletes_temp_manifest_file_after_flow(self, tmp_path):
        """Test that the temporary manifest page is removed once it has been loaded."""
        manifest = tmp_path / "manifest.html"
        manifest.write_text("<html></html>")

        with mock_main_dependencies({"id": 123}) as mocks:
            mocks["open_browser"].return_value = str(manifest)
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        assert not manifest.exists()


class TestOrgManifestHtml:
    """Tests for creating apps under an organization account."""

    def test_html_posts_to_personal_account_by_default(self):
        """Test that the default manifest form creates a user-owned app."""
        page = generate_manifest_html(build_app_manifest(base_domain="example.com"))
        assert 'action="https://github.com/settings/apps/new"' in page

    def test_html_posts_to_org_when_org_set(self):
        """Test that --org switches GitHub's manifest endpoint to the org path."""
        page = generate_manifest_html(
            build_app_manifest(base_domain="example.com"),
            org="OpenHands",
        )
        assert 'action="https://github.com/organizations/OpenHands/settings/apps/new"' in page

    def test_html_escapes_org_in_action_url(self):
        """Test that an org name cannot break the HTML form attribute."""
        page = generate_manifest_html(
            build_app_manifest(base_domain="example.com"),
            org='ev"il',
        )
        assert 'organizations/ev"il/' not in page
        assert "ev&quot;il" in page


class TestWaitForAppInstallation:
    """Tests for wait_for_app_installation()."""

    def test_returns_false_when_github_authentication_fails(self, capsys):
        """Test that authentication errors are surfaced as warnings instead of crashing."""
        with patch("create_github_app.Auth.AppAuth", side_effect=RuntimeError("bad auth")):
            assert wait_for_app_installation(app_id=123, private_key="pem", timeout=1) is False

        captured = capsys.readouterr()
        assert "could not authenticate with github api" in captured.out.lower()
        assert "bad auth" in captured.out

    def test_retries_when_installation_check_fails_once(self, capsys):
        """Test that transient post-creation API errors do not abort installation polling."""
        mock_integration = MagicMock()
        mock_integration.get_installations.side_effect = [
            RuntimeError("api unavailable"),
            iter([MagicMock()]),
        ]

        with patch("create_github_app.Auth.AppAuth"):
            with patch("create_github_app.GithubIntegration", return_value=mock_integration):
                with patch("create_github_app.time.time", side_effect=[0, 0, 0]):
                    with patch("create_github_app.time.sleep") as mock_sleep:
                        assert wait_for_app_installation(app_id=123, private_key="pem", timeout=1) is True

        mock_sleep.assert_called_once()
        captured = capsys.readouterr()
        assert "error checking installations" in captured.out.lower()
        assert "api unavailable" in captured.out

    def test_returns_false_when_installation_check_keeps_failing_until_timeout(self, capsys):
        """Test that repeated installation polling errors eventually time out."""
        mock_integration = MagicMock()
        mock_integration.get_installations.side_effect = RuntimeError("api unavailable")

        with patch("create_github_app.Auth.AppAuth"):
            with patch("create_github_app.GithubIntegration", return_value=mock_integration):
                with patch("create_github_app.time.time", side_effect=[0, 0, 2]):
                    with patch("create_github_app.time.sleep") as mock_sleep:
                        assert wait_for_app_installation(app_id=123, private_key="pem", timeout=1) is False

        mock_sleep.assert_called_once()
        captured = capsys.readouterr()
        assert "error checking installations" in captured.out.lower()
        assert "api unavailable" in captured.out


class TestMainInstallationFlow:
    """Tests for main() guiding the user to install the app and detecting completion via API polling."""

    def test_main_sets_installation_url_on_code_holder_from_slug(self):
        """Test that main() sets code_holder.installation_url so the browser can redirect to it."""
        with mock_main_dependencies({"id": 123, "slug": "openhands-abc123"}) as mocks:
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        assert mocks["code_holder"].installation_url == "https://github.com/apps/openhands-abc123/installations/new"

    def test_main_does_not_set_installation_url_when_slug_absent(self):
        """Test that main() leaves installation_url unset when credentials have no slug."""
        with mock_main_dependencies({"id": 123}) as mocks:
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        assert mocks["code_holder"].installation_url is None

    def test_main_polls_for_installation_when_pem_present(self):
        """Test that main() calls wait_for_app_installation() when credentials include pem."""
        with mock_main_dependencies({"id": 123, "slug": "my-slug", "pem": "fake-pem"}) as mocks:
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        mocks["wait_for_installation"].assert_called_once_with(
            app_id=123, private_key="fake-pem"
        )

    def test_main_skips_polling_when_no_pem(self):
        """Test that main() skips wait_for_app_installation() when credentials have no pem."""
        with mock_main_dependencies({"id": 123, "slug": "my-slug"}) as mocks:
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        mocks["wait_for_installation"].assert_not_called()

    def test_main_prints_success_when_installation_detected(self, capsys):
        """Test that main() prints a success message when installation is detected."""
        with mock_main_dependencies({"id": 123, "slug": "s", "pem": "p"}, installation_found=True):
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        captured = capsys.readouterr()
        assert "install" in captured.out.lower()

    def test_main_prints_warning_when_installation_times_out(self, capsys):
        """Test that main() prints a warning when installation polling times out."""
        with mock_main_dependencies({"id": 123, "slug": "s", "pem": "p"}, installation_found=False):
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        captured = capsys.readouterr()
        assert "timed out" in captured.out.lower() or "warning" in captured.out.lower()

    def test_main_prints_credentials_regardless_of_installation_outcome(self, capsys):
        """Test that main() prints credentials even if installation polling times out."""
        with mock_main_dependencies({
            "id": 123,
            "client_id": "Iv1.abc",
            "slug": "s",
            "pem": "p",
        }, installation_found=False):
            main(base_domain="example.com", dry_run=False, app_name="my-app")

        captured = capsys.readouterr()
        assert "GitHub App Client ID: Iv1.abc" in captured.out


class TestSavesPrivateKeySecurely:
    """Tests for private key file handling."""

    def test_private_key_is_written_with_owner_only_permissions(self, capsys, monkeypatch, tmp_path):
        """Test that the generated pem file is not group/world-readable."""
        monkeypatch.chdir(tmp_path)
        pem_path = SCRIPT_DIR / "keys" / "secure-app.pem"
        pem_content = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"

        with temporary_pem_file(pem_path):
            with mock_main_dependencies({"id": 123, "pem": pem_content}):
                main(base_domain="example.com", dry_run=False, app_name="secure-app")

            assert pem_path.exists()
            assert pem_path.read_text() == pem_content
            assert (pem_path.stat().st_mode & 0o777) == 0o600


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
