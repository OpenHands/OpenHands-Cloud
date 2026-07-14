"""Tests for the replicated/troubleshoot rendering contract (PLTF-3198)."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENHANDS_CHART = REPO_ROOT / "charts" / "openhands"


def render(*set_args: str, show_only: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "openhands", str(OPENHANDS_CHART)]
    if show_only is not None:
        command.extend(["--show-only", show_only])
    for arg in set_args:
        command.extend(["--set", arg])
    return subprocess.run(
        command,
        check=check,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def render_troubleshoot_secrets(*set_args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return render(*set_args, show_only="templates/troubleshoot/secrets.yaml", check=check)


def test_troubleshoot_secrets_render_by_default() -> None:
    manifest = render_troubleshoot_secrets().stdout

    assert "troubleshoot.sh/kind: preflight" in manifest
    assert "troubleshoot.sh/kind: support-bundle" in manifest
    assert "name: openhands-preflight" in manifest
    assert "name: openhands-support-bundle" in manifest


def test_troubleshoot_secrets_absent_when_replicated_disabled() -> None:
    result = render_troubleshoot_secrets("replicated.enabled=false", check=False)

    # helm errors when --show-only matches no rendered manifests
    assert result.returncode != 0
    assert "could not find template" in result.stderr


def test_db_collectors_absent_without_credentials() -> None:
    manifest = render_troubleshoot_secrets().stdout

    assert "- postgresql:" not in manifest
    assert "- redis:" not in manifest


def test_db_collectors_render_with_credentials() -> None:
    manifest = render_troubleshoot_secrets(
        "replicated.postgresUsername=oh",
        "replicated.postgresPassword=pg-secret",
        "replicated.postgresDatabase=openhands",
        "replicated.redisPassword=redis-secret",
    ).stdout

    assert "- postgresql:" in manifest
    assert "uri: postgresql://oh:pg-secret@openhands-postgresql" in manifest
    assert "- redis:" in manifest
    assert "uri: redis://default:redis-secret@openhands-redis-master" in manifest


def test_replicated_sdk_renders_by_default() -> None:
    manifest = render().stdout

    assert "# Source: openhands/charts/replicated/" in manifest


def test_replicated_sdk_absent_when_disabled() -> None:
    manifest = render("replicated.enabled=false").stdout

    assert "# Source: openhands/charts/replicated/" not in manifest
