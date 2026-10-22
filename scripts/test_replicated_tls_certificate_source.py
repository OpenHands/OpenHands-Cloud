"""Tests for the KOTS Certificate Source option.

Upload renders the certificate into openhands-tls and Keycloak's <host>-tls on
every deploy. Existing secret points every consumer at a secret the operator
manages, which the installer must never render.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
REPLICATED_CONFIG = REPO_ROOT / "replicated" / "config.yaml"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"
REPLICATED_SECRETS = REPO_ROOT / "replicated" / "secrets.yaml"

SECRET_NAME = '\'{{repl ConfigOption "computed_tls_certificate_name" }}\''
IF_UPLOAD = '{{repl if ConfigOptionEquals "tls_certificate_source" "upload" }}'
IF_SECRET = '{{repl if ConfigOptionEquals "tls_certificate_source" "secret" }}'


@pytest.fixture(scope="module")
def config_items() -> dict:
    config = yaml.safe_load(REPLICATED_CONFIG.read_text(encoding="utf-8"))
    return {
        item["name"]: item
        for group in config["spec"]["groups"]
        for item in group["items"]
    }


@pytest.fixture(scope="module")
def openhands_text() -> str:
    return REPLICATED_OPENHANDS.read_text(encoding="utf-8")


def test_existing_installs_keep_uploading(config_items: dict) -> None:
    source = config_items["tls_certificate_source"]

    assert source["default"] == "upload"
    assert "value" not in source
    assert [item["name"] for item in source["items"]] == ["upload", "secret"]


def test_each_source_shows_only_its_own_fields(config_items: dict) -> None:
    upload_only = "repl{{ ConfigOptionEquals \"tls_certificate_source\" \"upload\" }}"
    secret_only = "repl{{ ConfigOptionEquals \"tls_certificate_source\" \"secret\" }}"

    assert config_items["tls_certificate"]["when"] == upload_only
    assert config_items["tls_private_key"]["when"] == upload_only
    assert config_items["tls_existing_certificate_name"]["when"] == secret_only
    assert config_items["tls_existing_certificate_name"]["required"] is True
    assert "when" not in config_items["tls_ca_certificate"]


@pytest.mark.parametrize(
    "name, valid",
    [("customer-tls", True), ("tls.example.com", True), ("Bad_Name", False), ("-tls", False)],
)
def test_secret_name_validation(config_items: dict, name: str, valid: bool) -> None:
    pattern = config_items["tls_existing_certificate_name"]["validation"]["regex"]["pattern"]
    assert bool(re.fullmatch(pattern, name)) is valid


def test_computed_secret_name_falls_back_to_openhands_tls(config_items: dict) -> None:
    computed = config_items["computed_tls_certificate_name"]

    assert computed["hidden"] is True
    assert computed["default"] == (
        f'{IF_SECRET}{{{{repl ConfigOption "tls_existing_certificate_name" }}}}'
        "{{repl else }}openhands-tls{{repl end }}"
    )


def test_every_consumer_reads_the_computed_secret_name(openhands_text: str) -> None:
    code = "\n".join(
        line for line in openhands_text.splitlines() if not line.lstrip().startswith("#")
    )

    assert "openhands-tls" not in code
    # tlsStore, litellm, runtime-api ingress, gateway env + listener, laminar x2, agent-canvas
    assert code.count(SECRET_NAME) == 8


@pytest.fixture(scope="module")
def existing_secret_values() -> dict:
    chart = yaml.safe_load(REPLICATED_OPENHANDS.read_text(encoding="utf-8"))
    (block,) = [
        block
        for block in chart["spec"]["optionalValues"]
        if "tls_certificate_source" in block["when"]
    ]
    assert block["when"] == '{{repl ConfigOptionEquals "tls_certificate_source" "secret" }}'
    assert block["recursiveMerge"] is True
    return block["values"]


def test_existing_secret_reaches_the_chart(existing_secret_values: dict) -> None:
    assert existing_secret_values["tls"] == {
        "existingSecret": '{{repl ConfigOption "tls_existing_certificate_name" }}'
    }


def test_keycloak_references_the_existing_secret_without_rendering_one(
    existing_secret_values: dict,
) -> None:
    # recursiveMerge replaces arrays, so [] drops the uploaded-certificate secret.
    assert existing_secret_values["keycloak"]["ingress"] == {
        "tls": False,
        "secrets": [],
        "extraTls": [
            {
                "hosts": ['{{repl ConfigOption "computed_auth_hostname" }}'],
                "secretName": '{{repl ConfigOption "tls_existing_certificate_name" }}',
            }
        ],
    }


def test_secrets_chart_never_renders_an_existing_secret() -> None:
    text = REPLICATED_SECRETS.read_text(encoding="utf-8")

    for item in ("tls_certificate", "tls_private_key"):
        assert (
            f'{IF_UPLOAD}{{{{repl ConfigOptionData "{item}" | nindent 8}}}}{{{{repl end }}}}'
        ) in text
