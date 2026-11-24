"""Tests for the Replicated OAuth V2 login default."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / "replicated" / "config.yaml"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"
SAVED_CHOICE = 'repl{{ ConfigOptionEquals "auth_v2_enabled" "1" }}'


def config_item(name: str) -> dict:
    for group in yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["spec"]["groups"]:
        for item in group.get("items", []):
            if item["name"] == name:
                return item
    raise AssertionError(f"no config item named {name}")


def test_oauth_v2_login_is_on_by_default() -> None:
    assert config_item("auth_v2_enabled")["default"] == "1"


def test_server_and_web_client_flags_follow_the_saved_choice() -> None:
    env = yaml.safe_load(REPLICATED_OPENHANDS.read_text(encoding="utf-8"))["spec"]["values"]["env"]

    assert env["ENABLE_OAUTH_V2_LOGIN"] == SAVED_CHOICE
    assert env["OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_OAUTH_V2_LOGIN"] == SAVED_CHOICE
