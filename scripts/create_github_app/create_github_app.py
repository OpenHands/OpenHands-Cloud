#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyGithub", "requests", "fastapi", "uvicorn"]
# ///
"""CLI to create a GitHub app for OpenHands Enterprise (OHE)."""

import argparse
import html
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import requests
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from github import Auth, GithubIntegration

SCRIPT_DIR = Path(__file__).parent

APP_NAME_PREFIX = "openhands"
DEFAULT_CALLBACK_PORT = 9876  # Using high port that doesn't require root
GITHUB_API_TIMEOUT_SECONDS = 30


def generate_unique_app_name() -> str:
    """Generate a unique app name with random suffix."""
    return f"{APP_NAME_PREFIX}-{secrets.token_hex(4)}"


def is_safe_app_name(app_name: str) -> bool:
    """Check if app_name is safe for use as a filename (no path separators or special values)."""
    return bool(app_name) and "/" not in app_name and "\\" not in app_name and app_name not in (".", "..")


class GithubClient(Protocol):
    """Protocol for GitHub client to enable dependency injection."""

    def create_app_from_manifest(self, manifest: dict) -> dict:
        """Create a GitHub App from a manifest."""
        ...


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Create Github App for OpenHands Enterprise (OHE)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without making changes.",
    )
    parser.add_argument(
        "--app-name",
        default=None,
        help="Name of the GitHub App to create (default: openhands-<random>).",
    )
    parser.add_argument(
        "--base-domain",
        required=True,
        help="Base domain for the GitHub App (e.g., mycompany.com).",
    )
    parser.add_argument(
        "--org",
        default=None,
        help="Org to create the app in (default: your personal account).",
    )
    parser.add_argument(
        "--callback-port",
        type=int,
        default=DEFAULT_CALLBACK_PORT,
        help=f"Local port for the app-creation callback server "
        f"(default: {DEFAULT_CALLBACK_PORT}); use this if that port is in use.",
    )
    return parser.parse_args()


def build_app_manifest(
    base_domain: str,
    app_name: str | None = None,
    callback_port: int = DEFAULT_CALLBACK_PORT,
) -> dict[str, Any]:
    """Build the GitHub App manifest configuration."""
    if app_name is None:
        app_name = generate_unique_app_name()
    return {
        "name": app_name,
        "url": f"https://app.{base_domain}",
        "redirect_url": f"http://localhost:{callback_port}/callback",
        "callback_urls": [f"https://auth.app.{base_domain}/realms/allhands/broker/github/endpoint"],
        "public": False,
        "request_oauth_on_install": False,
        "default_permissions": {
            "actions": "write",
            "contents": "write",
            "emails": "read",
            "issues": "write",
            "metadata": "read",
            "organization_events": "read",
            "pull_requests": "write",
            "repository_hooks": "write",
            "statuses": "write",
            "workflows": "write",
        },
        "default_events": [
            "issue_comment",
            "pull_request",
            "pull_request_review_comment",
        ],
        "hook_attributes": {
            "url": f"https://app.{base_domain}/integration/github/events",
        },
    }


def generate_manifest_html(manifest: dict[str, Any], org: str | None = None) -> str:
    """Generate HTML form that POSTs to GitHub to create app from manifest."""
    manifest_json = json.dumps(manifest)
    # HTML-escape the JSON to safely embed in the value attribute
    escaped_json = html.escape(manifest_json)
    action = (
        f"https://github.com/organizations/{org}/settings/apps/new"
        if org
        else "https://github.com/settings/apps/new"
    )
    return f"""<!DOCTYPE html>
<html>
<head><title>Creating GitHub App...</title></head>
<body>
<p>Redirecting to GitHub to create your app...</p>
<form id="manifest-form" action="{html.escape(action)}" method="post">
<input type="hidden" name="manifest" value="{escaped_json}">
</form>
<script>document.getElementById('manifest-form').submit();</script>
</body>
</html>"""


def open_manifest_in_browser(
    base_domain: str,
    app_name: str | None = None,
    callback_port: int = DEFAULT_CALLBACK_PORT,
    org: str | None = None,
) -> str:
    """Write manifest HTML to temp file and open in browser. Returns file path."""
    manifest = build_app_manifest(base_domain, app_name, callback_port=callback_port)
    html = generate_manifest_html(manifest, org=org)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
        f.write(html)
        filepath = f.name
    try:
        webbrowser.open(f"file://{filepath}")
    except Exception:
        # No browser (for example, headless shell): do not leak the temp file.
        Path(filepath).unlink(missing_ok=True)
        raise
    return filepath


@dataclass
class CodeHolder:
    """Holds the OAuth code received from GitHub callback."""

    code: str | None = None
    code_received: threading.Event = field(default_factory=threading.Event)
    installation_url: str | None = None


def create_callback_app() -> tuple[FastAPI, CodeHolder]:
    """Create a FastAPI app with /callback and /installation-url endpoints."""
    app = FastAPI()
    code_holder = CodeHolder()

    @app.get("/callback", response_class=HTMLResponse)
    def callback(code: str | None = None):
        if code is None:
            return HTMLResponse(
                content="<html><body><h1>Error</h1><p>Missing code parameter.</p></body></html>",
                status_code=400,
            )
        code_holder.code = code
        code_holder.code_received.set()
        return HTMLResponse(
            content="""<!DOCTYPE html>
<html>
<head><title>GitHub App Created</title></head>
<body>
<p>Created app! Navigating to install page...</p>
<script>
var attempts = 0;
var maxAttempts = 60;

function showInstallUrlError() {
  document.body.innerHTML = '<h1>Error</h1><p>Installation URL not available. Check the terminal for instructions.</p>';
}

function checkInstallUrl() {
  if (attempts++ >= maxAttempts) {
    showInstallUrlError();
    return;
  }

  fetch('/installation-url')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.url) {
        window.location.href = data.url;
      } else {
        setTimeout(checkInstallUrl, 1000);
      }
    })
    .catch(function() {
      showInstallUrlError();
    });
}
setTimeout(checkInstallUrl, 500);
</script>
</body>
</html>""",
            status_code=200,
        )

    @app.get("/installation-url")
    def installation_url_endpoint():
        return JSONResponse({"url": code_holder.installation_url})

    return app, code_holder


class ServerHandle:
    """Handle for managing a running uvicorn server."""

    def __init__(self, server: uvicorn.Server, thread: threading.Thread):
        self.server = server
        self.thread = thread


def start_callback_server(
    port: int = DEFAULT_CALLBACK_PORT,
) -> tuple[ServerHandle, CodeHolder]:
    """Start the callback server in a background thread."""
    app, code_holder = create_callback_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Fail fast when the port is unavailable instead of waiting for the
    # 5-minute GitHub callback timeout.
    deadline = time.time() + 10
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError(
                f"Callback server failed to start on port {port} (already in use?)."
            )
        if time.time() > deadline:
            raise RuntimeError(
                f"Callback server did not start on port {port} within 10s (already in use?)."
            )
        time.sleep(0.05)

    return ServerHandle(server, thread), code_holder


def stop_callback_server(handle: ServerHandle) -> None:
    """Stop the callback server."""
    handle.server.should_exit = True
    handle.thread.join(timeout=5)
    if handle.thread.is_alive():
        print("Warning: Callback server thread did not exit within 5s; cleanup may be incomplete.")


def exchange_code_for_credentials(code: str) -> dict:
    """Exchange the temporary code for app credentials."""
    response = requests.post(
        f"https://api.github.com/app-manifests/{code}/conversions",
        headers={"Accept": "application/vnd.github+json"},
        timeout=GITHUB_API_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def wait_for_app_installation(
    app_id: int,
    private_key: str,
    timeout: float = 300,
    poll_interval: float = 5.0,
) -> bool:
    """Poll GitHub API until the app has at least one installation or timeout."""
    try:
        auth = Auth.AppAuth(app_id, private_key)
        gi = GithubIntegration(auth=auth)
    except Exception as exc:
        print(f"Warning: Could not authenticate with GitHub API: {exc}")
        return False

    start = time.time()
    deadline = start + timeout
    while time.time() < deadline:
        try:
            # get_installations() returns a PaginatedList: iterable, not always
            # an iterator, so wrap it before checking for the first result.
            if next(iter(gi.get_installations()), None) is not None:
                return True
        except Exception as exc:
            print(f"Warning: Error checking installations: {exc}")
        time.sleep(poll_interval)
    return False


def create_github_app(
    base_domain: str,
    github_client: GithubClient,
    app_name: str | None = None,
) -> dict:
    """Create a GitHub App using the provided client."""
    manifest = build_app_manifest(base_domain, app_name)
    return github_client.create_app_from_manifest(manifest)


def main(
    base_domain: str,
    dry_run: bool = False,
    github_client: GithubClient | None = None,
    app_name: str | None = None,
    callback_port: int = DEFAULT_CALLBACK_PORT,
    org: str | None = None,
) -> None:
    """Main entry point for creating a GitHub App."""
    if app_name is None:
        app_name = generate_unique_app_name()
    if not is_safe_app_name(app_name):
        print(f"Error: invalid --app-name '{app_name}': must be a plain name without '/', '\\', or '..'.")
        sys.exit(1)
    target = f"the {org} org" if org else "your personal account"
    if dry_run:
        print(f"Would create GitHub App '{app_name}' for domain '{base_domain}' on {target}")
        return

    # Start callback server to capture the code from GitHub redirect
    server_handle, code_holder = start_callback_server(port=callback_port)
    manifest_html_path = None

    try:
        # Open browser for user to create app (they're already logged into GitHub)
        print(f"\nOpening browser to create GitHub App '{app_name}' on {target}...")
        print("Click 'Create GitHub App for <your-username>' to continue.")
        print("Waiting for GitHub callback...\n")
        manifest_html_path = open_manifest_in_browser(
            base_domain,
            app_name,
            callback_port=callback_port,
            org=org,
        )

        # Wait for the code to be received via callback
        print("Waiting for authorization code...")
        code_holder.code_received.wait(timeout=300)  # 5 minute timeout
        code = code_holder.code

        if code is None:
            print("Error: Timed out waiting for authorization code.")
            sys.exit(1)

        print("Authorization code received!")

        try:
            credentials = exchange_code_for_credentials(code)
        except (requests.RequestException, ValueError) as exc:
            # ValueError covers json.JSONDecodeError (HTTP 200 with non-JSON body).
            print(f"Error: failed to exchange the code for app credentials: {exc}")
            sys.exit(1)
        print(f"\nGitHub App created successfully!")

        if "slug" in credentials:
            install_url = f"https://github.com/apps/{credentials['slug']}/installations/new"
            print(f"\nInstall URL: {install_url}")
            code_holder.installation_url = install_url

        if "pem" in credentials:
            print("\nWaiting for app installation...")
            installed = wait_for_app_installation(app_id=credentials["id"], private_key=credentials["pem"])
            if installed:
                print("GitHub App installed successfully!")
            else:
                print("Warning: Timed out waiting for app installation.")

        # Save pem to keys/ directory relative to script location
        pem_path = None
        if "pem" in credentials:
            keys_dir = SCRIPT_DIR / "keys"
            keys_dir.mkdir(exist_ok=True)
            pem_path = keys_dir / f"{app_name}.pem"
            # Create the private key 0o600 from the first byte; chmod too in
            # case the file already existed with looser permissions.
            fd = os.open(pem_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(credentials["pem"])
            pem_path.chmod(0o600)

        print(f"\nCredentials:")
        display_names = {
            "id": "GitHub App ID",
            "slug": "GitHub App Slug",
            "client_id": "GitHub App Client ID",
            "client_secret": "GitHub App Client Secret",
            "webhook_secret": "GitHub App Webhook Secret",
        }
        for key in ["client_id", "client_secret", "id", "slug", "webhook_secret"]:
            if key in credentials:
                display_key = display_names.get(key, key)
                print(f"  {display_key}: {credentials[key]}")
        if pem_path:
            display_path = f"./scripts/create_github_app/keys/{app_name}.pem"
            print(f"  GitHub App Private Key: {display_path}")
    finally:
        # Always stop the callback server
        stop_callback_server(server_handle)
        if manifest_html_path:
            Path(manifest_html_path).unlink(missing_ok=True)


if __name__ == "__main__":
    args = parse_args()
    main(
        base_domain=args.base_domain,
        dry_run=args.dry_run,
        app_name=args.app_name,
        callback_port=args.callback_port,
        org=args.org,
    )
