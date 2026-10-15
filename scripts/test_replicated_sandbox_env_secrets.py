"""Keep installer secrets out of the agent-server (sandbox) env.

``global.agentServerEnv`` is serialised into ``OH_AGENT_SERVER_ENV`` on every
pod that uses the app env and mirrored into each warm-runtime config, so the
app hands it to every sandbox and runtime-api writes it into the sandbox pod
spec as literal values. Sandboxes reach the LLM through the bundled LiteLLM
proxy with the user's own key; the upstream provider credentials belong only
in ``litellm-env-secrets``.

For every provider configuration, set each password-type config item to a
sentinel, apply the matching optionalValues blocks the way KOTS does, and
require that no sentinel ends up in the effective ``agentServerEnv`` map.
"""

from __future__ import annotations

from functools import cache

import pytest
import yaml

from test_replicated_llm_provider_routes import (
    OPENHANDS,
    PROVIDERS,
    _config_items,
    _config_options,
    _helm_render,
)

SENTINEL = "SENTINEL-"
CA_BUNDLE_KEYS = {"SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"}


def _secret_overrides() -> dict[str, str]:
    secrets = {
        item["name"]: f"{SENTINEL}{item['name']}"
        for item in _config_items()
        if item.get("type") == "password"
    }
    secrets["google_vertex_credentials"] = (
        '{"private_key": "' + SENTINEL + 'google_vertex_credentials"}'
    )
    return secrets


def _agent_server_env_blocks() -> list[tuple[str, dict[str, str]]]:
    doc = next(
        d
        for d in yaml.safe_load_all(OPENHANDS.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("metadata", {}).get("name") == "openhands"
    )
    spec = doc["spec"]
    blocks = [("true", spec["values"]["global"]["agentServerEnv"])]
    for entry in spec.get("optionalValues", []):
        env = (entry.get("values", {}).get("global") or {}).get("agentServerEnv")
        if env:
            blocks.append((entry["when"], env))
    return blocks


@cache
def agent_server_env(provider: str) -> dict[str, str]:
    """Effective global.agentServerEnv for a provider with sentinel secrets."""
    cfg = _config_options({**PROVIDERS[provider][0], **_secret_overrides()})
    blocks = _agent_server_env_blocks()
    whens = _helm_render({f"w{i}": when for i, (when, _) in enumerate(blocks)}, cfg)
    active = [env for i, (_, env) in enumerate(blocks) if str(whens[f"w{i}"]) == "true"]
    fields = {
        f"b{i}k{j}": value
        for i, env in enumerate(active)
        for j, value in enumerate(env.values())
    }
    rendered = _helm_render(fields, cfg)
    result: dict[str, str] = {}
    for i, env in enumerate(active):
        for j, key in enumerate(env):
            result[key] = str(rendered[f"b{i}k{j}"])
    return result


def test_sentinels_reach_the_config() -> None:
    cfg = _config_options({**PROVIDERS["azure-api-key"][0], **_secret_overrides()})
    assert cfg["azure_api_key"] == f"{SENTINEL}azure_api_key"


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_sandbox_env_has_no_installer_secrets(provider: str) -> None:
    env = agent_server_env(provider)
    assert CA_BUNDLE_KEYS <= env.keys()
    leaked = sorted(key for key, value in env.items() if SENTINEL in value)
    assert not leaked, f"{provider}: installer secrets in agentServerEnv: {leaked}"
