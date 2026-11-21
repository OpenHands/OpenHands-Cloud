"""Tests for the KOTS sandbox Deletion Time default."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
REPLICATED_CONFIG = REPO_ROOT / "replicated" / "config.yaml"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"


@pytest.fixture(scope="module")
def config_items() -> dict:
    config = yaml.safe_load(REPLICATED_CONFIG.read_text(encoding="utf-8"))
    return {
        item["name"]: item
        for group in config["spec"]["groups"]
        for item in group["items"]
    }


def test_deletion_time_is_shorter_only_for_new_embedded_cluster_installs(
    config_items: dict,
) -> None:
    # Sequence is 0 only on a fresh install, and a written `value:` is never re-rendered.
    # Distribution is "embedded-cluster" when nodes carry kots.io/embedded-cluster-role.
    item = config_items["sandbox_dead_seconds"]

    assert "default" not in item
    assert item["value"] == (
        '{{repl if and (eq Sequence 0) (eq Distribution "embedded-cluster") }}'
        "259200{{repl else }}864000{{repl end }}"
    )


def test_deletion_time_values_pass_their_own_validation(config_items: dict) -> None:
    item = config_items["sandbox_dead_seconds"]
    pattern = re.compile(item["validation"]["regex"]["pattern"])

    for seconds in re.findall(r"\}\}(\d+)\{\{", item["value"]):
        assert pattern.fullmatch(seconds)


def test_deletion_time_reaches_runtime_api() -> None:
    assert (
        'dead_seconds: repl{{ ConfigOption "sandbox_dead_seconds" }}'
        in REPLICATED_OPENHANDS.read_text(encoding="utf-8")
    )
