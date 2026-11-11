"""Tests for how Replicated wires the Automations page visibility."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"
SAVED_CHOICE = 'repl{{ ConfigOptionEquals "automations_enabled" "1" }}'


def replicated_values() -> dict:
    return yaml.safe_load(REPLICATED_OPENHANDS.read_text(encoding="utf-8"))["spec"]["values"]


def automations_optional_values() -> dict:
    for entry in yaml.safe_load(REPLICATED_OPENHANDS.read_text(encoding="utf-8"))["spec"]["optionalValues"]:
        if entry["when"] == '{{repl ConfigOptionEquals "automations_enabled" "1" }}':
            return entry["values"]
    raise AssertionError("no optionalValues entry for automations_enabled")


def test_both_ui_flags_follow_the_saved_automations_choice() -> None:
    env = replicated_values()["env"]

    assert env["ENABLE_AUTOMATIONS"] == SAVED_CHOICE
    assert env["OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_AUTOMATIONS"] == SAVED_CHOICE


def test_saved_on_choice_supplies_the_service_url_the_chart_default_needs() -> None:
    values = automations_optional_values()

    assert values["automationService"]["url"]
    assert values["automation"]["enabled"] is True


def test_saved_off_choice_leaves_automation_wiring_to_chart_defaults() -> None:
    values = replicated_values()

    for key in ("automation", "automationService", "automationServiceKey", "automationWebhookSecret"):
        assert key not in values
