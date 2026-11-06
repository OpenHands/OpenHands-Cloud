#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyYAML", "pytest"]
# ///
"""The Admin Console config collector stays generated, complete and secret-free."""

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

import support_bundle_config as sbc

CONFIG = yaml.safe_load(sbc.CONFIG.read_text())
MANIFEST_TEXT = sbc.MANIFEST.read_text()


def all_items() -> list[dict]:
    return [item for group in CONFIG["spec"]["groups"] for item in group.get("items") or []]


def collected_names() -> set[str]:
    return set(re.findall(r'ConfigOption "([^"]+)"', MANIFEST_TEXT))


def test_manifest_matches_generator():
    assert MANIFEST_TEXT == sbc.render(CONFIG), "run scripts/support_bundle_config.py"


def test_check_mode_passes_on_committed_manifest(capsys):
    assert sbc.main(["--check"]) == 0


def test_manifest_is_a_kots_support_bundle_with_one_data_collector():
    manifest = yaml.safe_load(MANIFEST_TEXT)
    assert manifest["kind"] == "SupportBundle"
    assert manifest["apiVersion"] == "troubleshoot.sh/v1beta2"
    collectors = manifest["spec"]["collectors"]
    assert len(collectors) == 1
    assert collectors[0]["data"]["name"] == sbc.BUNDLE_PATH


def test_every_non_secret_item_is_collected():
    expected = {
        item["name"]
        for item in all_items()
        if item.get("type") not in sbc.SECRET_TYPES | sbc.VALUELESS_TYPES
        and item["name"] not in sbc.EXCLUDED
    }
    assert expected, "config.yaml has no collectable items?"
    assert collected_names() == expected


def test_no_password_or_file_item_is_collected():
    secret = {item["name"] for item in all_items() if item.get("type") in sbc.SECRET_TYPES}
    assert secret, "config.yaml has no password items?"
    assert not secret & collected_names()
    assert not sbc.EXCLUDED & collected_names()


def test_excluded_names_exist_in_config():
    # An exclusion for a renamed or removed item would silently stop guarding anything.
    names = {item["name"] for item in all_items()}
    assert sbc.EXCLUDED <= names


def test_values_are_json_quoted_on_one_line():
    body = MANIFEST_TEXT.split("data: |\n", 1)[1]
    value_lines = [line for line in body.splitlines() if "ConfigOption" in line]
    assert value_lines
    for line in value_lines:
        assert re.fullmatch(r' {12}[a-z0-9_]+: repl\{\{ ConfigOption "[a-z0-9_]+" \| mustToJson \}\}', line), line


def test_rendered_values_parse_as_yaml():
    # Stand in for KOTS: a multi-line textarea renders as one JSON string.
    rendered = re.sub(
        r'repl\{\{ ConfigOption "[^"]+" \| mustToJson \}\}',
        lambda _: '"line one\\nline two: with colon"',
        MANIFEST_TEXT,
    )
    data = yaml.safe_load(yaml.safe_load(rendered)["spec"]["collectors"][0]["data"]["data"])
    group_names = {group["name"] for group in CONFIG["spec"]["groups"]}
    assert set(data) <= group_names
    assert all(value == "line one\nline two: with colon" for group in data.values() for value in group.values())
