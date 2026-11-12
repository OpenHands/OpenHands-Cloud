"""The bundled LiteLLM proxy must not retry provider rate limits (429).

The agent SDK already retries 429s with backoff, so proxy retries on top of it
multiply requests to a throttled provider (4 per SDK attempt with
``num_retries: 3``). ``router_settings.retry_policy.RateLimitErrorRetries: 0``
turns off proxy retries for rate limits only; other errors keep
``litellm_settings.num_retries``.

These tests render the proxy ConfigMap from the real chart. For Replicated, the
KOTS ``litellm-helm.proxy_config`` values are rendered for each provider the
way KOTS applies them (spec values, then matching optionalValues merged
recursively) and passed to ``helm template`` as user values, so Helm merges
them with the chart defaults just as an install does.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from test_replicated_llm_provider_routes import (
    OPENHANDS,
    PROVIDERS,
    _config_options,
    _helm_render,
    render_routes,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENHANDS_CHART = REPO_ROOT / "charts" / "openhands"
CHART_VALUES = OPENHANDS_CHART / "values.yaml"
CONFIGMAP = "charts/litellm-helm/templates/configmap-litellm.yaml"

RETRY_POLICY = {"retry_policy": {"RateLimitErrorRetries": 0}}


def render_proxy_config(*set_args: str, values: dict | None = None) -> dict:
    command = [
        "helm",
        "template",
        "openhands",
        str(OPENHANDS_CHART),
        "--set",
        "automation.enabled=false",
        "--show-only",
        CONFIGMAP,
        "--set",
        "litellm-helm.enabled=true",
    ]
    for arg in set_args:
        command.extend(["--set", arg])
    if values is not None:
        command.extend(["--values", "-"])
    result = subprocess.run(
        command,
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        input=yaml.safe_dump(values) if values is not None else None,
    )
    return yaml.safe_load(yaml.safe_load(result.stdout)["data"]["config.yaml"])


def chart_litellm_settings() -> dict:
    values = yaml.safe_load(CHART_VALUES.read_text(encoding="utf-8"))
    return values["litellm-helm"]["proxy_config"]["litellm_settings"]


def _merge(base: dict, override: dict) -> dict:
    """KOTS recursiveMerge: maps merge, everything else (arrays too) replaces."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _kots_leaves(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _kots_leaves(value, (*path, key))
    elif isinstance(node, str) and "repl" in node:
        yield path, node


def replicated_proxy_values(overrides: dict[str, str]) -> dict:
    """Return the litellm-helm.proxy_config values KOTS passes to Helm."""
    doc = next(
        d
        for d in yaml.safe_load_all(OPENHANDS.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("metadata", {}).get("name") == "openhands"
    )
    spec = doc["spec"]
    blocks = [("true", spec["values"]["litellm-helm"]["proxy_config"])]
    for entry in spec.get("optionalValues", []):
        proxy_config = (
            entry.get("values", {}).get("litellm-helm", {}).get("proxy_config")
        )
        if proxy_config is not None:
            assert entry.get("recursiveMerge") is True, entry["when"]
            blocks.append((entry["when"], proxy_config))

    cfg = _config_options(overrides)
    whens = _helm_render({f"w{i}": when for i, (when, _) in enumerate(blocks)}, cfg)
    proxy: dict = {}
    for i, (_, proxy_config) in enumerate(blocks):
        if str(whens[f"w{i}"]) == "true":
            proxy = _merge(proxy, proxy_config)

    leaves = list(_kots_leaves(proxy))
    rendered = _helm_render({f"l{i}": t for i, (_, t) in enumerate(leaves)}, cfg)
    for i, (path, _) in enumerate(leaves):
        parent = proxy
        for key in path[:-1]:
            parent = parent[key]
        # KOTS renders the template text, then the manifest is parsed as YAML.
        parent[path[-1]] = yaml.safe_load(str(rendered[f"l{i}"]))
    return proxy


def test_helm_default_disables_proxy_rate_limit_retries() -> None:
    config = render_proxy_config()

    assert config["router_settings"] == RETRY_POLICY
    assert config["litellm_settings"] == chart_litellm_settings()
    assert config["litellm_settings"]["num_retries"] == 3


def test_helm_users_can_override_rate_limit_retries() -> None:
    config = render_proxy_config(
        "litellm-helm.proxy_config.router_settings.retry_policy.RateLimitErrorRetries=2"
    )

    assert config["router_settings"] == {"retry_policy": {"RateLimitErrorRetries": 2}}
    assert config["litellm_settings"] == chart_litellm_settings()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_replicated_providers_keep_rate_limit_retry_policy(provider: str) -> None:
    proxy = replicated_proxy_values(PROVIDERS[provider][0])
    assert "router_settings" not in proxy
    assert "litellm_settings" not in proxy

    config = render_proxy_config(values={"litellm-helm": {"proxy_config": proxy}})

    assert config["router_settings"] == RETRY_POLICY
    assert config["litellm_settings"] == chart_litellm_settings()
    assert config["model_list"] == render_routes(provider)[1]


def test_replicated_azure_providers_together_keep_retry_policy() -> None:
    overrides = {
        **PROVIDERS["azure-api-key"][0],
        **PROVIDERS["azure-ai"][0],
        "azure_provider_mode": "both",
    }
    proxy = replicated_proxy_values(overrides)

    config = render_proxy_config(values={"litellm-helm": {"proxy_config": proxy}})

    assert config["router_settings"] == RETRY_POLICY
    assert config["litellm_settings"] == chart_litellm_settings()
    assert [r["model_name"] for r in config["model_list"]] == [
        "azure-gpt-4o",
        "azure_ai-gpt-5.4",
    ]
    assert config["general_settings"]["store_model_in_db"] is True
