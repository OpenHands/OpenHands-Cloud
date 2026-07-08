"""Tests for the Replicated OpenHands resolver label configuration."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
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


def extract_optional_values_block(text: str, when: str) -> str:
    match = re.search(
        rf"(?ms)^    - when: {re.escape(when)}\n.*?(?=^    - when: |\Z)",
        text,
    )
    assert match, f"optionalValues block not found for {when}"
    return match.group(0)


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


def test_openhands_resolver_label_is_rendered_globally_for_all_integrations() -> None:
    openhands_values = REPLICATED_OPENHANDS.read_text(encoding="utf-8")
    base_env = extract_base_env_block(openhands_values)

    assert (
        "OH_RESOLVER_LABEL: 'repl{{ ConfigOption \"openhands_resolver_label\" }}'"
        in base_env
    )

    github_auth = extract_optional_values_block(
        openhands_values,
        "'{{repl ConfigOptionEquals \"github_auth_enabled\" \"1\" }}'",
    )

    assert "OH_RESOLVER_LABEL" not in github_auth
    assert "github_resolver_label" not in openhands_values
