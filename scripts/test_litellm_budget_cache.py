#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyYAML", "pytest"]
# ///
"""Budget policy changes must take effect on the next authenticated request."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_helm_and_replicated_disable_stale_authorization_cache():
    values = yaml.safe_load((ROOT / "charts/openhands/values.yaml").read_text())
    documents = yaml.safe_load_all((ROOT / "replicated/openhands.yaml").read_text())
    chart = next(doc for doc in documents if doc and doc.get("kind") == "HelmChart")
    assert values["litellm-helm"]["image"]["tag"] == (
        "1.100.4@sha256:59833b59195879712355a3be4287cfcaa4e04db1356df9c8c446da7b977fb6d1"
    )
    assert "tag" not in chart["spec"]["values"]["litellm-helm"]["image"]
    for config in [values, chart["spec"]["values"]]:
        settings = config["litellm-helm"]["proxy_config"]["general_settings"]
        assert settings["user_api_key_cache_ttl"] == 0
    for entry in chart["spec"].get("optionalValues", []):
        proxy = entry.get("values", {}).get("litellm-helm")
        if proxy is not None:
            assert entry.get("recursiveMerge") is True
            settings = proxy.get("proxy_config", {}).get("general_settings", {})
            assert settings.get("user_api_key_cache_ttl", 0) == 0
