"""Tests for Replicated org condenser default configuration."""

from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
REPLICATED_CONFIG = REPO_ROOT / "replicated" / "config.yaml"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"


def replicated_config_item(name: str) -> dict[str, object]:
    config = yaml.safe_load(REPLICATED_CONFIG.read_text(encoding="utf-8"))
    for group in config["spec"]["groups"]:
        for item in group["items"]:
            if item["name"] == name:
                return item
    raise AssertionError(f"Replicated config item {name!r} was not found")


def test_replicated_exposes_org_condenser_options() -> None:
    max_tokens = replicated_config_item("org_condenser_max_tokens")
    apply_existing = replicated_config_item("org_condenser_apply_to_existing")
    overwrite_existing = replicated_config_item("org_condenser_overwrite_existing")

    assert max_tokens["type"] == "text"
    assert max_tokens["default"] == ""
    assert max_tokens["validation"] == {
        "regex": {
            "pattern": "^$|^[1-9][0-9]*$",
            "message": "Must be blank or a positive base-10 integer.",
        }
    }
    assert apply_existing["type"] == "bool"
    assert apply_existing["default"] == "0"
    assert overwrite_existing["type"] == "bool"
    assert overwrite_existing["default"] == "0"


def test_replicated_maps_blank_to_yaml_null_and_positive_value_to_integer() -> None:
    values = REPLICATED_OPENHANDS.read_text(encoding="utf-8")

    assert "orgDefaults:\n      condenser:\n" in values
    assert "maxTokens: repl{{ $orgCondenserMaxTokens := trim" in values
    assert "repl{{ $orgCondenserMaxTokens }}repl{{ else }}null" in values
    assert 'ConfigOption "org_condenser_max_tokens"' in values
    assert 'applyToExisting: repl{{ and (ne (ConfigOption "org_condenser_max_tokens") "") (ConfigOptionEquals "org_condenser_apply_to_existing" "1") }}' in values
    assert 'overwriteExisting: repl{{ and (ne (ConfigOption "org_condenser_max_tokens") "") (ConfigOptionEquals "org_condenser_apply_to_existing" "1") (ConfigOptionEquals "org_condenser_overwrite_existing" "1") }}' in values

    assert 'maxTokens: "repl{{' not in values
    assert "maxTokens: 'repl{{" not in values
