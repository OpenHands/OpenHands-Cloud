"""Tests for the resolver-label configuration contract."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENHANDS_CHART = REPO_ROOT / "charts" / "openhands"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"


def render_openhands_deployment(resolver_label: str | None) -> str:
    command = [
        "helm",
        "template",
        "openhands",
        str(OPENHANDS_CHART),
        "--show-only",
        "templates/deployment.yaml",
    ]
    if resolver_label is not None:
        command.extend(
            [
                "--set-string",
                f"integrations.resolverLabel={resolver_label}",
            ]
        )

    result = subprocess.run(
        command,
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.stdout


def rendered_env_values(manifest: str, env_name: str) -> list[str]:
    return re.findall(
        rf"(?m)^\s+- name: {re.escape(env_name)}\n\s+value: \"?([^\"\n]+)\"?$",
        manifest,
    )


@pytest.mark.parametrize(
    ("resolver_label", "expected"),
    [
        (None, "openhands"),
        ("custom-openhands", "custom-openhands"),
    ],
)
def test_chart_renders_resolver_label_for_default_and_override(
    resolver_label: str | None, expected: str
) -> None:
    manifest = render_openhands_deployment(resolver_label)

    assert set(rendered_env_values(manifest, "OH_RESOLVER_LABEL")) == {expected}


def test_replicated_passes_resolver_label_to_chart_integrations() -> None:
    values = REPLICATED_OPENHANDS.read_text(encoding="utf-8")

    assert (
        "    integrations:\n"
        "      resolverLabel: 'repl{{ ConfigOption \"openhands_resolver_label\" }}'"
    ) in values
    assert "OH_RESOLVER_LABEL:" not in values
