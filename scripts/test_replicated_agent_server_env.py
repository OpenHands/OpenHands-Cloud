"""Keep the agent-server env limited to non-sensitive runtime settings.

``OH_AGENT_SERVER_ENV`` is folded from ``env.OH_AGENT_SERVER_ENV`` (JSON),
``global.agentServerEnv`` (also mirrored into each warm-runtime config) and the
legacy top-level ``agentServerEnv``. The agent-server reaches the LLM
through the bundled LiteLLM proxy, so provider settings belong only in
``litellm-env-secrets``.

For every provider configuration, set each password-type config item to a
sentinel, apply the matching optionalValues blocks the way KOTS does, and
require that no sentinel ends up in the effective ``agentServerEnv`` map.
Also require that the map holds only the allowlisted CA-bundle and proxy keys,
which catches values from non-password items and hardcoded literals.
"""

from __future__ import annotations

import json
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
# Every key the agent-server env may hold: the CA-bundle paths and the proxy
# block's vars. Adding one here should be a deliberate decision.
ALLOWED_KEYS = CA_BUNDLE_KEYS | {
    "GIT_SSL_CAINFO",
    "CURL_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "SSL_VERIFY",
    "GIT_SSL_NO_VERIFY",
}


def _password_overrides() -> dict[str, str]:
    values = {
        item["name"]: f"{SENTINEL}{item['name']}"
        for item in _config_items()
        if item.get("type") == "password"
    }
    values["google_vertex_credentials"] = (
        '{"private_key": "' + SENTINEL + 'google_vertex_credentials"}'
    )
    return values


def _agent_server_env_sources() -> list[tuple[str, str, object]]:
    """(when, kind, value) for every input the chart folds into the env."""
    doc = next(
        d
        for d in yaml.safe_load_all(OPENHANDS.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("metadata", {}).get("name") == "openhands"
    )
    spec = doc["spec"]
    entries = [("true", spec["values"])] + [
        (entry["when"], entry.get("values", {}))
        for entry in spec.get("optionalValues", [])
    ]
    sources: list[tuple[str, str, object]] = []
    for when, values in entries:
        raw = (values.get("env") or {}).get("OH_AGENT_SERVER_ENV")
        if raw:
            sources.append((when, "json", raw))
        for kind, env in (
            ("global", (values.get("global") or {}).get("agentServerEnv")),
            ("top", values.get("agentServerEnv")),
        ):
            if env:
                sources.append((when, kind, env))
    return sources


@cache
def agent_server_env(provider: str) -> dict[str, str]:
    """Effective OH_AGENT_SERVER_ENV map for a provider with sentinel passwords."""
    cfg = _config_options({**PROVIDERS[provider][0], **_password_overrides()})
    sources = _agent_server_env_sources()
    whens = _helm_render(
        {f"w{i}": when for i, (when, _, _) in enumerate(sources)}, cfg
    )
    active = [
        (kind, value)
        for i, (_, kind, value) in enumerate(sources)
        if str(whens[f"w{i}"]) == "true"
    ]
    fields: dict[str, str] = {}
    for i, (kind, value) in enumerate(active):
        if kind == "json":
            fields[f"b{i}"] = value
        else:
            fields.update({f"b{i}k{j}": v for j, v in enumerate(value.values())})
    rendered = _helm_render(fields, cfg)
    # Same precedence as openhands.env: JSON base, then the global map, then
    # the top-level map.
    base: dict[str, str] = {}
    maps: dict[str, dict[str, str]] = {"global": {}, "top": {}}
    for i, (kind, value) in enumerate(active):
        if kind == "json":
            base = json.loads(str(rendered[f"b{i}"]))
        else:
            for j, key in enumerate(value):
                maps[kind][key] = str(rendered[f"b{i}k{j}"])
    return {**base, **maps["global"], **maps["top"]}


def test_sentinels_reach_the_config() -> None:
    cfg = _config_options({**PROVIDERS["azure-api-key"][0], **_password_overrides()})
    assert cfg["azure_api_key"] == f"{SENTINEL}azure_api_key"


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_agent_server_env_has_no_password_values(provider: str) -> None:
    env = agent_server_env(provider)
    assert CA_BUNDLE_KEYS <= env.keys()
    leaked = sorted(key for key, value in env.items() if SENTINEL in value)
    assert not leaked, f"{provider}: password config values in agentServerEnv: {leaked}"


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_agent_server_env_has_only_allowlisted_keys(provider: str) -> None:
    unexpected = sorted(agent_server_env(provider).keys() - ALLOWED_KEYS)
    assert not unexpected, f"{provider}: unexpected agentServerEnv keys: {unexpected}"
