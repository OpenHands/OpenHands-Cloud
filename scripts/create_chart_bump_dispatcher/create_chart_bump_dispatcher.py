#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyGithub", "requests", "fastapi", "uvicorn"]
# ///
"""Create GitHub App credentials for chart-bump repository_dispatch senders."""

from __future__ import annotations

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
from typing import Any

import requests
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from github import Auth, GithubIntegration


SCRIPT_DIR = Path(__file__).parent

DEFAULT_APP_NAME_PREFIX = "saas-deploy-chart-dispatcher"
APP_HOMEPAGE_URL = "https://github.com/OpenHands/saas-deploy"
DEFAULT_CALLBACK_PORT = 9876
GITHUB_API_TIMEOUT_SECONDS = 30

APP_PERMISSIONS = {
    "contents": "write",
    "metadata": "read",
}


def generate_unique_app_name() -> str:
    """Generate a globally unique-ish App name with the generic dispatcher prefix."""
    return f"{DEFAULT_APP_NAME_PREFIX}-{secrets.token_hex(4)}"


def is_safe_app_name(app_name: str | None) -> bool:
    """Return True when app_name can safely become keys/<app_name>.pem."""
    return bool(app_name) and "/" not in app_name and "\\" not in app_name and app_name not in (".", "..")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a GitHub App for chart-bump repository_dispatch senders."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without making changes.",
    )
    parser.add_argument(
        "--app-name",
        default=None,
        help=f"Name of the GitHub App to create (default: {DEFAULT_APP_NAME_PREFIX}-<random>).",
    )
    parser.add_argument(
        "--org",
        default=None,
        help="Org to create the App in (for this flow, usually OpenHands).",
    )
    parser.add_argument(
        "--callback-port",
        type=int,
        default=DEFAULT_CALLBACK_PORT,
        help=(
            "Local port for the manifest callback server "
            f"(default: {DEFAULT_CALLBACK_PORT}); use this if that port is busy."
        ),
    )
    return parser.parse_args()


def build_app_manifest(
    app_name: str | None = None,
    callback_port: int = DEFAULT_CALLBACK_PORT,
) -> dict[str, Any]:
    """Build the least-privilege GitHub App manifest."""
    if app_name is None:
        app_name = generate_unique_app_name()
    return {
        "name": app_name,
        "url": APP_HOMEPAGE_URL,
        "redirect_url": f"http://localhost:{callback_port}/callback",
        "public": False,
        "request_oauth_on_install": False,
        "default_permissions": dict(APP_PERMISSIONS),
        "default_events": [],
    }


def generate_manifest_html(manifest: dict[str, Any], org: str | None = None) -> str:
    """Generate an auto-submitting manifest form for GitHub App creation."""
    manifest_json = json.dumps(manifest)
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
<input type="hidden" name="manifest" value="{html.escape(manifest_json)}">
</form>
<script>document.getElementById('manifest-form').submit();</script>
</body>
</html>"""


def open_manifest_in_browser(
    app_name: str | None = None,
    callback_port: int = DEFAULT_CALLBACK_PORT,
    org: str | None = None,
) -> str:
    """Write the manifest form to a temp file and open it in the browser."""
    manifest = build_app_manifest(app_name=app_name, callback_port=callback_port)
    page = generate_manifest_html(manifest, org=org)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
        f.write(page)
        path = f.name
    try:
        webbrowser.open(f"file://{path}")
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise
    return path


@dataclass
class CodeHolder:
    """Mutable state shared between the callback server and main flow."""

    code: str | None = None
    code_received: threading.Event = field(default_factory=threading.Event)
    installation_url: str | None = None


def create_callback_app() -> tuple[FastAPI, CodeHolder]:
    """Create the local callback server app."""
    app = FastAPI()
    holder = CodeHolder()

    @app.get("/callback", response_class=HTMLResponse)
    def callback(code: str | None = None):
        if code is None:
            return HTMLResponse(
                content="<html><body><h1>Error</h1><p>Missing code parameter.</p></body></html>",
                status_code=400,
            )
        holder.code = code
        holder.code_received.set()
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
        return JSONResponse({"url": holder.installation_url})

    return app, holder


class ServerHandle:
    """Handle for stopping the background uvicorn server."""

    def __init__(self, server: uvicorn.Server, thread: threading.Thread):
        self.server = server
        self.thread = thread


def start_callback_server(port: int = DEFAULT_CALLBACK_PORT) -> tuple[ServerHandle, CodeHolder]:
    """Start the local callback server and fail fast if it cannot bind."""
    app, holder = create_callback_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

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

    return ServerHandle(server, thread), holder


def stop_callback_server(handle: ServerHandle) -> None:
    """Stop the callback server."""
    handle.server.should_exit = True
    handle.thread.join(timeout=5)
    if handle.thread.is_alive():
        print("Warning: Callback server thread did not exit within 5s; cleanup may be incomplete.")


def exchange_code_for_credentials(code: str) -> dict[str, Any]:
    """Exchange the manifest code for GitHub App credentials."""
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
    """Poll with App auth until GitHub reports at least one installation."""
    try:
        auth = Auth.AppAuth(app_id, private_key)
        integration = GithubIntegration(auth=auth)
    except Exception as exc:
        print(f"Warning: Could not authenticate with GitHub API: {exc}")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if next(iter(integration.get_installations()), None) is not None:
                return True
        except Exception as exc:
            print(f"Warning: Error checking installations: {exc}")
        time.sleep(poll_interval)
    return False


def write_private_key(app_name: str, pem: str) -> Path:
    """Write the App private key under this script's keys/ directory with 0600 mode."""
    keys_dir = SCRIPT_DIR / "keys"
    keys_dir.mkdir(exist_ok=True)
    pem_path = keys_dir / f"{app_name}.pem"
    fd = os.open(pem_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(pem)
    pem_path.chmod(0o600)
    return pem_path


def main(
    dry_run: bool = False,
    app_name: str | None = None,
    org: str | None = None,
    callback_port: int = DEFAULT_CALLBACK_PORT,
) -> None:
    """Create the chart-bump dispatcher App through GitHub's manifest flow."""
    if app_name is None:
        app_name = generate_unique_app_name()
    if not is_safe_app_name(app_name):
        print(
            f"Error: invalid --app-name '{app_name}': must be a plain name without '/', '\\', or '..'."
        )
        sys.exit(1)

    target = f"the {org} org" if org else "your personal account"
    if dry_run:
        print(f"Would create GitHub App '{app_name}' on {target}")
        print(f"  permissions: {APP_PERMISSIONS}")
        print("  events: none")
        return

    server_handle, holder = start_callback_server(port=callback_port)
    manifest_path: str | None = None
    installation_failed = False

    try:
        print(f"\nOpening browser to create GitHub App '{app_name}' on {target}...")
        print("Click the 'Create GitHub App for ...' button to continue.")
        print("Waiting for GitHub callback...\n")
        manifest_path = open_manifest_in_browser(
            app_name=app_name,
            callback_port=callback_port,
            org=org,
        )

        print("Waiting for authorization code...")
        holder.code_received.wait(timeout=300)
        code = holder.code
        if code is None:
            print("Error: Timed out waiting for authorization code.")
            sys.exit(1)

        print("Authorization code received!")
        try:
            credentials = exchange_code_for_credentials(code)
        except (requests.RequestException, ValueError) as exc:
            print(f"Error: failed to exchange the code for app credentials: {exc}")
            sys.exit(1)

        print("\nGitHub App created successfully!")

        if "slug" in credentials:
            install_url = f"https://github.com/apps/{credentials['slug']}/installations/new"
            holder.installation_url = install_url
            print(f"\nInstall URL: {install_url}")
            print("Install it on the OpenHands/saas-deploy repository only.")

        if "pem" in credentials:
            print("\nWaiting for app installation...")
            installed = wait_for_app_installation(
                app_id=credentials["id"],
                private_key=credentials["pem"],
            )
            if installed:
                print("GitHub App installed successfully!")
            else:
                installation_failed = True
                print("Error: Timed out waiting for app installation.")

        pem_path = None
        if "pem" in credentials:
            pem_path = write_private_key(app_name, credentials["pem"])

        print("\nCredentials:")
        print(f"  CHART_BUMP_DISPATCHER_APP_ID          = {credentials['id']}")
        if pem_path:
            print(
                "  CHART_BUMP_DISPATCHER_APP_PRIVATE_KEY = contents of "
                f"./scripts/create_chart_bump_dispatcher/keys/{app_name}.pem"
            )
        print("\nStore these values as the sender workflow's Environment secrets.")
        print("Do not add this App to saas-deploy ruleset bypass actors.")
        if "slug" in credentials:
            print(f"\nApp page: https://github.com/apps/{credentials['slug']}")
        if installation_failed:
            sys.exit(1)
    finally:
        stop_callback_server(server_handle)
        if manifest_path:
            Path(manifest_path).unlink(missing_ok=True)


if __name__ == "__main__":
    args = parse_args()
    main(
        dry_run=args.dry_run,
        app_name=args.app_name,
        org=args.org,
        callback_port=args.callback_port,
    )
