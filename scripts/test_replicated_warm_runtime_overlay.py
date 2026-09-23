"""Keep warm-runtime overlay activation scoped to Replicated installs."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _yaml(path: str) -> dict:
    return yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))


def test_replicated_enables_overlay_without_changing_shared_chart_defaults() -> None:
    replicated = _yaml("replicated/openhands.yaml")
    subchart = _yaml("charts/openhands/charts/runtime-api/values.yaml")
    umbrella = _yaml("charts/openhands/values.yaml")

    assert (
        replicated["spec"]["values"]["runtime-api"]["env"][
            "WARM_RUNTIME_CONFIG_OVERLAY"
        ]
        == "1"
    )
    assert "WARM_RUNTIME_CONFIG_OVERLAY" not in subchart["env"]
    assert "WARM_RUNTIME_CONFIG_OVERLAY" not in umbrella["runtime-api"]["env"]
