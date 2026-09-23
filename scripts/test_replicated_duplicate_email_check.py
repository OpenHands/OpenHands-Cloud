"""Tests for the Replicated duplicate-email configuration contract."""

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


def test_replicated_exposes_duplicate_email_check_with_secure_default() -> None:
    item = replicated_config_item("duplicate_email_check")

    assert item["type"] == "bool"
    assert item["default"] == "1"
    assert "plus" in str(item["help_text"]).lower()


def test_replicated_passes_duplicate_email_check_to_the_chart_value() -> None:
    values = REPLICATED_OPENHANDS.read_text(encoding="utf-8")

    assert (
        '    duplicateEmailCheck: repl{{ ConfigOptionEquals "duplicate_email_check" "1" }}'
        in values
    )
    assert "DUPLICATE_EMAIL_CHECK:" not in values
