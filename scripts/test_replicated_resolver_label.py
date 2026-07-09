"""Tests for the Replicated OpenHands resolver label configuration."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OPENHANDS_CHART = REPO_ROOT / "charts" / "openhands"
OPENHANDS_VALUES = OPENHANDS_CHART / "values.yaml"
REPLICATED_CONFIG = REPO_ROOT / "replicated" / "config.yaml"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"


def extract_config_group(text: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^    - name: {re.escape(name)}\n.*?(?=^    - name: |\Z)",
        text,
    )
    assert match, f"{name} config group not found"
    return match.group(0)


def extract_named_config_item(text: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^        - name: {re.escape(name)}\n.*?(?=^        - name: |\n\n    - name: |\Z)",
        text,
    )
    assert match, f"{name} config item not found"
    return match.group(0)


def extract_base_env_block(text: str) -> str:
    match = re.search(
        r"(?ms)^    env:\n.*?(?=^    agentServerEnv:)",
        text,
    )
    assert match, "base env block not found"
    return match.group(0)


def extract_chart_values_block(text: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(name)}:\n.*?(?=^[A-Za-z0-9_-]+:|\Z)",
        text,
    )
    assert match, f"{name} chart values block not found"
    return match.group(0)


def extract_replicated_values_block(text: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^    {re.escape(name)}:\n.*?(?=^    [A-Za-z0-9_-]+:|\Z)",
        text,
    )
    assert match, f"{name} replicated values block not found"
    return match.group(0)


def extract_optional_values_block(text: str, when: str) -> str:
    match = re.search(
        rf"(?ms)^    - when: {re.escape(when)}\n.*?(?=^    - when: |\Z)",
        text,
    )
    assert match, f"optionalValues block not found for {when}"
    return match.group(0)


def render_openhands_deployment(resolver_label: str) -> str:
    result = subprocess.run(
        [
            "helm",
            "template",
            "openhands",
            str(OPENHANDS_CHART),
            "--show-only",
            "templates/deployment.yaml",
            "--set-string",
            f"integrations.resolverLabel={resolver_label}",
        ],
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


def test_installer_exposes_global_openhands_resolver_label() -> None:
    config = REPLICATED_CONFIG.read_text(encoding="utf-8")
    advanced_options = extract_config_group(config, "advanced_options")
    resolver_label = extract_named_config_item(advanced_options, "openhands_resolver_label")

    assert "title: OpenHands Resolver Label" in resolver_label
    assert "type: text" in resolver_label
    assert 'default: "openhands"' in resolver_label
    assert "when:" not in resolver_label
    assert "required:" not in resolver_label
    assert 'Defaults to "openhands"' in resolver_label
    assert "supported issue and pull" in resolver_label
    assert "request integrations" in resolver_label
    assert "custom value" in resolver_label


def test_chart_renders_resolver_label_env_from_integrations_value() -> None:
    chart_values = OPENHANDS_VALUES.read_text(encoding="utf-8")
    integrations = extract_chart_values_block(chart_values, "integrations")
    manifest = render_openhands_deployment("custom-openhands")

    assert 'resolverLabel: "openhands"' in integrations
    assert set(rendered_env_values(manifest, "OH_RESOLVER_LABEL")) == {
        "custom-openhands"
    }


def test_replicated_wires_resolver_label_through_chart_integrations_value() -> None:
    openhands_values = REPLICATED_OPENHANDS.read_text(encoding="utf-8")
    integrations = extract_replicated_values_block(openhands_values, "integrations")
    base_env = extract_base_env_block(openhands_values)

    assert (
        "resolverLabel: 'repl{{ ConfigOption \"openhands_resolver_label\" }}'"
        in integrations
    )
    assert "OH_RESOLVER_LABEL" not in base_env

    github_auth = extract_optional_values_block(
        openhands_values,
        "'{{repl ConfigOptionEquals \"github_auth_enabled\" \"1\" }}'",
    )

    assert "OH_RESOLVER_LABEL" not in github_auth
    assert "github_resolver_label" not in openhands_values
