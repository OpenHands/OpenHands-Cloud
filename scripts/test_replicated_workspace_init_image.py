"""The Replicated install lets an admin set the workspace init image.

A blank option must leave the runtime-api on its chart default: it rejects an
empty RUNTIME_INIT_IMAGE, so the value is only handed to the chart when set.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
OPTION = "sandbox_init_image"
IS_SET = f'ne (ConfigOption "{OPTION}") ""'


@pytest.fixture(scope="module")
def item() -> dict:
    groups = yaml.safe_load((ROOT / "replicated/config.yaml").read_text())["spec"]["groups"]
    group, found = {i["name"]: (g["name"], i) for g in groups for i in g["items"]}[OPTION]
    assert group == "sandbox_configuration"
    return found


@pytest.fixture(scope="module")
def spec() -> dict:
    return yaml.safe_load((ROOT / "replicated/openhands.yaml").read_text())["spec"]


def test_option_is_optional_and_blank_by_default(item: dict) -> None:
    assert item["type"] == "text"
    assert item["required"] is False
    assert "default" not in item
    assert "value" not in item
    assert "when" not in item


@pytest.mark.parametrize(
    "image",
    [
        "",
        "registry.example.com/mirror/busybox:1.36",
        "registry.example.com:5000/busybox@sha256:" + "a" * 64,
    ],
)
def test_option_accepts_blank_tag_and_digest_references(item: dict, image: str) -> None:
    assert re.search(item["validation"]["regex"]["pattern"], image)


@pytest.mark.parametrize("image", ["busybox:1.36 ", " busybox:1.36", "my mirror/busybox:1.36"])
def test_option_rejects_whitespace(item: dict, image: str) -> None:
    assert not re.search(item["validation"]["regex"]["pattern"], image)


def test_value_reaches_runtime_api_only_when_set(spec: dict) -> None:
    gated = [
        entry
        for entry in spec["optionalValues"]
        if "RUNTIME_INIT_IMAGE" in entry["values"].get("runtime-api", {}).get("env", {})
    ]

    assert len(gated) == 1
    assert gated[0]["when"] == "{{repl " + IS_SET + " }}"
    assert gated[0]["recursiveMerge"] is True
    assert gated[0]["values"]["runtime-api"]["env"]["RUNTIME_INIT_IMAGE"] == f'repl{{{{ ConfigOption "{OPTION}" }}}}'


def test_blank_option_leaves_the_chart_default(spec: dict) -> None:
    assert "RUNTIME_INIT_IMAGE" not in spec["values"]["runtime-api"]["env"]


def test_init_image_reuses_the_install_pull_secret(spec: dict) -> None:
    pull_secrets = spec["values"]["runtime-api"]["env"]["RUNTIME_IMAGE_PULL_SECRETS"]

    assert pull_secrets.endswith("{{repl else}}{{repl ImagePullSecretName }}{{repl end}}")
