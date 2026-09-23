"""Guard the default sandbox-spec name across app and installer layers."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _yaml(path: str) -> dict:
    return yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))


def test_default_sandbox_spec_matches_every_warm_runtime_default() -> None:
    env_template = (REPO_ROOT / "charts/openhands/templates/_env.yaml").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r"- name: OH_SANDBOX_SPEC_DEFAULT_SPEC_NAME\s+value: ([^\s]+)",
        env_template,
    )
    assert match is not None

    umbrella = _yaml("charts/openhands/values.yaml")
    subchart = _yaml("charts/openhands/charts/runtime-api/values.yaml")
    replicated = _yaml("replicated/openhands.yaml")

    names = {
        "app env": match.group(1),
        "umbrella chart": umbrella["runtime-api"]["warmRuntimes"]["configsByName"][
            "default"
        ]["name"],
        "runtime-api subchart": subchart["warmRuntimes"]["configsByName"]["default"][
            "name"
        ],
        "Replicated values": replicated["spec"]["values"]["runtime-api"][
            "warmRuntimes"
        ]["configsByName"]["default"]["name"],
    }

    assert set(names.values()) == {"v1_current"}, names
