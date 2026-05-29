"""Tests for Keycloak realm chart invariants."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
REALM_TEMPLATE = (
    REPO_ROOT
    / "charts"
    / "openhands"
    / "files"
    / "allhands-realm-github-provider.json.tmpl"
)
KEYCLOAK_CONFIG_SCRIPT = (
    REPO_ROOT / "charts" / "openhands" / "templates" / "keycloak-config-script.yaml"
)


def pkce_enabled_providers_missing_method(realm: dict) -> list[str]:
    missing_pkce_method = []
    for provider in realm.get("identityProviders", []):
        config = provider.get("config") or {}
        if config.get("pkceEnabled") == "true" and not config.get("pkceMethod"):
            missing_pkce_method.append(provider.get("alias", "<unknown>"))
    return missing_pkce_method


def keycloak_api_call_body(script_template: str) -> str:
    match = re.search(
        r"(?ms)^(?P<indent>[ \t]*)keycloak_api_call\(\) \{\n"
        r"(?P<body>.*?)^(?P=indent)\}",
        script_template,
    )
    assert match, "Could not find keycloak_api_call() in keycloak-config-script.yaml"
    return match.group("body")


def assert_pkce_enabled_providers_set_method(realm: dict) -> None:
    missing_pkce_method = pkce_enabled_providers_missing_method(realm)
    assert not missing_pkce_method, (
        "Identity providers with pkceEnabled=true must set pkceMethod: "
        + ", ".join(missing_pkce_method)
    )


def assert_keycloak_api_call_detects_error_message(script_template: str) -> None:
    body = keycloak_api_call_body(script_template)
    assert "errorMessage" in body, (
        "keycloak_api_call() must treat Keycloak errorMessage responses as errors"
    )


def test_realm_template_is_valid_json() -> None:
    json.loads(REALM_TEMPLATE.read_text(encoding="utf-8"))


def test_pkce_enabled_identity_providers_set_pkce_method() -> None:
    realm = json.loads(REALM_TEMPLATE.read_text(encoding="utf-8"))
    assert_pkce_enabled_providers_set_method(realm)


def test_pkce_guard_catches_missing_method() -> None:
    realm = {
        "identityProviders": [
            {
                "alias": "azure_devops",
                "config": {"pkceEnabled": "true"},
            },
            {
                "alias": "github",
                "config": {"pkceEnabled": "false"},
            },
        ],
    }

    with pytest.raises(AssertionError, match="azure_devops"):
        assert_pkce_enabled_providers_set_method(realm)


def test_keycloak_api_call_checks_error_message_responses() -> None:
    script_template = KEYCLOAK_CONFIG_SCRIPT.read_text(encoding="utf-8")
    assert_keycloak_api_call_detects_error_message(script_template)


def test_keycloak_api_call_extraction_is_not_tied_to_yaml_indent() -> None:
    script_template = """\
  keycloak_api_call() {
    ERROR=$(echo "$RESPONSE" | jq -r '.errorMessage')
  }
"""

    assert "errorMessage" in keycloak_api_call_body(script_template)


def test_keycloak_error_guard_catches_missing_error_message() -> None:
    script_template = """\
    keycloak_api_call() {
      COMMAND=$1
      export RESPONSE=$(eval $COMMAND)
      ERROR=$(echo "$RESPONSE" | jq -r '.error')
      if [ -n "$ERROR" ] && [ "null" != "$ERROR" ]; then
        exit 1
      fi
    }
"""

    with pytest.raises(AssertionError, match="errorMessage"):
        assert_keycloak_api_call_detects_error_message(script_template)
