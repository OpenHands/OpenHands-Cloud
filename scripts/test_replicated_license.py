"""Tests for the KOTS License options.

The operator picks a license key or an offline license file. Neither is
required: an upcoming release will enforce the license, and until then the
chart's preflight only warns when neither is set.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
REPLICATED_CONFIG = REPO_ROOT / "replicated" / "config.yaml"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"

WHEN_KEY = '{{repl ConfigOptionEquals "license_source" "key" }}'
WHEN_OFFLINE = '{{repl ConfigOptionEquals "license_source" "offline" }}'
OFFLINE_DATA = '{{repl ConfigOptionData "offline_license" | nindent 12 }}'


@pytest.fixture(scope="module")
def license_group() -> dict:
    config = yaml.safe_load(REPLICATED_CONFIG.read_text(encoding="utf-8"))
    groups = {group["name"]: group for group in config["spec"]["groups"]}
    return {item["name"]: item for item in groups["license"]["items"]}


@pytest.fixture(scope="module")
def optional_values() -> list[dict]:
    chart = yaml.safe_load(REPLICATED_OPENHANDS.read_text(encoding="utf-8"))
    return chart["spec"]["optionalValues"]


def test_no_license_field_blocks_an_install(license_group: dict) -> None:
    for item in license_group.values():
        assert not item.get("required"), item["name"]
    assert license_group["license_key"]["recommended"] is True
    assert license_group["offline_license"]["recommended"] is True


def test_each_license_type_shows_only_its_own_field(license_group: dict) -> None:
    source = license_group["license_source"]

    assert [item["name"] for item in source["items"]] == ["key", "offline"]
    assert license_group["license_key"]["type"] == "password"
    assert license_group["license_key"]["when"] == (
        'repl{{ ConfigOptionEquals "license_source" "key" }}'
    )
    assert license_group["offline_license"]["type"] == "file"
    assert license_group["offline_license"]["when"] == (
        'repl{{ ConfigOptionEquals "license_source" "offline" }}'
    )


@pytest.mark.parametrize(
    "contents, valid",
    [
        (json.dumps({"key_id": "k1", "payload": "{}", "signature": "c2ln"}), True),
        (json.dumps({"key_id": "k1", "payload": "{}", "signature": "c2ln"}, indent=2), True),
        ("apiVersion: kots.io/v1beta1\nkind: License\nspec:\n  signature: abc\n", False),
        ("-----BEGIN CERTIFICATE-----\n", False),
    ],
)
def test_offline_license_validation(license_group: dict, contents: str, valid: bool) -> None:
    # KOTS matches the decoded file contents, unanchored.
    pattern = license_group["offline_license"]["validation"]["regex"]["pattern"]
    assert bool(re.search(pattern, contents)) is valid


def test_only_the_chosen_license_reaches_the_chart(optional_values: list[dict]) -> None:
    by_when = {entry["when"]: entry for entry in optional_values}

    key_values = by_when[WHEN_KEY]["values"]
    offline_values = by_when[WHEN_OFFLINE]["values"]
    assert by_when[WHEN_KEY]["recursiveMerge"] is True
    assert by_when[WHEN_OFFLINE]["recursiveMerge"] is True
    assert key_values == {"license": {"key": '{{repl ConfigOption "license_key" | trim }}'}}
    assert offline_values == {"license": {"offlineLicense": OFFLINE_DATA + "\n"}}


def _nindent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n" + "\n".join(pad + line for line in text.split("\n"))


@pytest.mark.parametrize(
    "contents",
    [
        json.dumps({"key_id": "k1", "payload": "{}", "signature": "c2ln"}),
        json.dumps({"key_id": "k1", "payload": "{}", "signature": "c2ln"}, indent=2) + "\n",
        "",
    ],
)
def test_offline_license_block_round_trips(contents: str) -> None:
    """The nindent width must match the block scalar's indentation in the file."""
    text = REPLICATED_OPENHANDS.read_text(encoding="utf-8")
    rendered = text.replace(OFFLINE_DATA, _nindent(contents, 12), 1)
    chart = yaml.safe_load(rendered)
    entry = next(e for e in chart["spec"]["optionalValues"] if e["when"] == WHEN_OFFLINE)
    value = entry["values"]["license"]["offlineLicense"]

    if contents:
        assert json.loads(value) == json.loads(contents)
    else:
        assert value.strip() == ""
