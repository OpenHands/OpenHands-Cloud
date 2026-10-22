"""Tests for the Replicated sandbox template selector advanced option."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENHANDS_CHART = REPO_ROOT / "charts" / "openhands"
REPLICATED_CONFIG = REPO_ROOT / "replicated" / "config.yaml"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"

FLAG_WHEN = '{{repl ConfigOptionEquals "sandbox_selector_enabled" "1" }}'
ORG_CONFIG_EXPR = (
    'repl{{ ConfigOption "sandbox_template_org_config" | trim | mustToJson }}'
)
MEMORY_LIMIT_EXPR = 'repl{{ ConfigOption "sandbox_memory_limit" }}'

ORG_A = "11111111-1111-4111-8111-111111111111"
ORG_B = "22222222-2222-4222-8222-222222222222"


def advanced_item(name: str) -> dict:
    config = yaml.safe_load(REPLICATED_CONFIG.read_text(encoding="utf-8"))
    group = next(g for g in config["spec"]["groups"] if g["name"] == "advanced_options")
    for item in group["items"]:
        if item["name"] == name:
            return item
    raise AssertionError(f"{name!r} is not in the Advanced Options group")


def selector_block_text() -> str:
    """Raw optionalValues entry, before KOTS renders it."""
    text = REPLICATED_OPENHANDS.read_text(encoding="utf-8")
    start = text.index(f"    - when: '{FLAG_WHEN}'")
    end = text.index("\n    - when:", start + 1)
    return text[start:end]


def render_selector_values(org_config: str, memory_limit: str = "4096Mi") -> dict:
    """Emulate KOTS for the two expressions the block uses (Sprig mustToJson)."""
    block = selector_block_text()
    assert ORG_CONFIG_EXPR in block
    assert MEMORY_LIMIT_EXPR in block
    rendered = block.replace(ORG_CONFIG_EXPR, json.dumps(org_config.strip())).replace(
        MEMORY_LIMIT_EXPR, memory_limit
    )
    (entry,) = yaml.safe_load(rendered)
    assert entry["recursiveMerge"] is True
    return entry["values"]


def test_flag_is_an_advanced_option_that_defaults_off() -> None:
    item = advanced_item("sandbox_selector_enabled")

    assert item["type"] == "bool"
    assert item["default"] == "0"
    assert "v1_current" in item["help_text"]


def test_org_config_is_required_only_when_the_flag_is_on() -> None:
    item = advanced_item("sandbox_template_org_config")

    assert item["type"] == "textarea"
    assert item["required"] is True
    assert item["when"] == 'repl{{ ConfigOptionEquals "sandbox_selector_enabled" "1" }}'


@pytest.mark.parametrize(
    "value",
    [
        json.dumps({ORG_A: {"template_ids": ["v1_current"], "default_template_id": "v1_current"}}),
        json.dumps(
            {
                ORG_A: {"template_ids": ["v1_current", "large"], "default_template_id": "v1_current"},
                ORG_B: {"default_template_id": "small", "template_ids": ["small"]},
            },
            indent=2,
        ),
        f'  {{"{ORG_A.upper()}":{{"template_ids":["a"],"default_template_id":"a"}}}}\n',
    ],
)
def test_org_config_validation_accepts_policy_objects(value: str) -> None:
    pattern = advanced_item("sandbox_template_org_config")["validation"]["regex"]["pattern"]

    assert re.fullmatch(pattern, value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "{}",
        "[]",
        '{"not-a-uuid": {"template_ids": ["a"], "default_template_id": "a"}}',
        f'{{"{ORG_A}": ["a"]}}',
        f'{{"{ORG_A}": {{"template_ids": ["a"], "default_template_id": "a"}}',
        f'{{"{ORG_A}": {{"nested": {{"a": 1}}}}}}',
    ],
)
def test_org_config_validation_rejects_malformed_values(value: str) -> None:
    pattern = advanced_item("sandbox_template_org_config")["validation"]["regex"]["pattern"]

    assert not re.fullmatch(pattern, value)


def test_selector_env_is_absent_unless_the_flag_is_on() -> None:
    values = yaml.safe_load(REPLICATED_OPENHANDS.read_text(encoding="utf-8"))
    base_env = values["spec"]["values"]["env"]
    gated = [
        entry
        for entry in values["spec"]["optionalValues"]
        if "SANDBOX_TEMPLATE_ORG_CONFIG" in entry["values"].get("env", {})
    ]

    assert "ENABLE_SANDBOX_SELECTOR" not in base_env
    assert "SANDBOX_TEMPLATE_ORG_CONFIG" not in base_env
    assert [entry["when"] for entry in gated] == [FLAG_WHEN]


def test_org_config_survives_kots_rendering_as_an_exact_string() -> None:
    # Multiline input with quotes that would break a single-quoted YAML scalar.
    org_config = (
        "\n{\n"
        f'  "{ORG_A}": {{"template_ids": ["v1_current", "o\'brien"],\n'
        '    "default_template_id": "v1_current"}\n}\n'
    )

    values = render_selector_values(org_config)

    assert values["env"]["ENABLE_SANDBOX_SELECTOR"] == "true"
    assert values["env"]["SANDBOX_TEMPLATE_ORG_CONFIG"] == org_config.strip()
    assert json.loads(values["env"]["SANDBOX_TEMPLATE_ORG_CONFIG"])[ORG_A][
        "template_ids"
    ] == ["v1_current", "o'brien"]


def helm_template(values: dict, tmp_path: Path, show_only: str) -> str:
    values_file = tmp_path / "values.yaml"
    values_file.write_text(yaml.safe_dump(values), encoding="utf-8")
    result = subprocess.run(
        [
            "helm",
            "template",
            "openhands",
            str(OPENHANDS_CHART),
            "--set",
            "runtime-api.warmRuntimes.enabled=true",
            "-f",
            str(values_file),
            "--show-only",
            show_only,
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_chart_renders_selector_env_and_default_display_name(tmp_path: Path) -> None:
    org_config = json.dumps(
        {ORG_A: {"template_ids": ["v1_current", "large"], "default_template_id": "v1_current"}}
    )
    values = render_selector_values(org_config, memory_limit="4096Mi")

    deployment = yaml.safe_load(helm_template(values, tmp_path, "templates/deployment.yaml"))
    env = {
        e["name"]: e.get("value")
        for c in deployment["spec"]["template"]["spec"]["containers"]
        for e in c.get("env", [])
    }
    assert env["ENABLE_SANDBOX_SELECTOR"] == "true"
    assert env["SANDBOX_TEMPLATE_ORG_CONFIG"] == org_config

    configmap = yaml.safe_load(
        helm_template(
            values,
            tmp_path,
            "charts/runtime-api/templates/warm-runtimes-configmap.yaml",
        )
    )
    configs = json.loads(configmap["data"]["warm-runtimes.json"])["configs"]
    default = next(c for c in configs if c["name"] == "v1_current")
    assert default["display_name"] == "Standard (4096Mi)"
    assert "resources" not in default
