"""Tests for Replicated GitHub resolver label configuration."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPLICATED_CONFIG = REPO_ROOT / "replicated" / "config.yaml"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"


def extract_named_config_item(text: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^        - name: {re.escape(name)}\n.*?(?=^        - name: |\n\n    - name: |\Z)",
        text,
    )
    assert match, f"{name} config item not found"
    return match.group(0)


def extract_optional_values_block(text: str, when: str) -> str:
    match = re.search(
        rf"(?ms)^    - when: {re.escape(when)}\n.*?(?=^    - when: |\Z)",
        text,
    )
    assert match, f"optionalValues block not found for {when}"
    return match.group(0)


def test_installer_exposes_optional_github_resolver_label_with_github_auth() -> None:
    config = REPLICATED_CONFIG.read_text(encoding="utf-8")
    resolver_label = extract_named_config_item(config, "github_resolver_label")

    assert "title: OpenHands Resolver Label" in resolver_label
    assert "type: text" in resolver_label
    assert 'default: "openhands"' in resolver_label
    assert 'when: \'repl{{ ConfigOptionEquals "github_auth_enabled" "1" }}\'' in resolver_label
    assert "required:" not in resolver_label
    assert 'Defaults to "openhands"' in resolver_label
    assert "custom value" in resolver_label

    private_key_position = config.index("        - name: github_app_private_key")
    resolver_label_position = config.index("        - name: github_resolver_label")
    assert private_key_position < resolver_label_position


def test_github_auth_optional_values_map_resolver_label_to_openhands_env() -> None:
    openhands_values = REPLICATED_OPENHANDS.read_text(encoding="utf-8")
    github_auth = extract_optional_values_block(
        openhands_values,
        "'{{repl ConfigOptionEquals \"github_auth_enabled\" \"1\" }}'",
    )

    assert "recursiveMerge: true" in github_auth
    assert "env:" in github_auth
    assert 'ENABLE_V1_GITHUB_RESOLVER: "true"' in github_auth
    assert (
        "OH_RESOLVER_LABEL: '{{repl ConfigOption \"github_resolver_label\"}}'"
        in github_auth
    )
