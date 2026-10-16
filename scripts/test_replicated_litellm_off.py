"""Render the KOTS ``enable_litellm`` toggle and its direct-default model wiring.

With LiteLLM off, the LiteLLM subchart and gateway env must be disabled, BYOK
forced on, and each API-key provider must seed ``defaultLlm`` with its first
listed model and a key that ``litellm-env-secrets`` exports. Providers that
need gateway-only auth (Bedrock, Vertex, Azure) get no default and a notice.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import pytest
import yaml
from test_replicated_llm_provider_routes import (
    OPENHANDS,
    PROVIDERS,
    _config_options,
    _helm_render,
    exported_secret_env,
)

APPLICATION = Path(__file__).resolve().parents[1] / "replicated" / "application.yaml"

# provider -> (models textarea, first line to configure, expected SDK model)
DIRECT = {
    "anthropic": ("anthropic_models", "model-a", "anthropic/model-a"),
    "openai": ("openai_models", "model-a", "openai/model-a"),
    "gemini": ("gemini_models", "model-a", "gemini/model-a"),
    "deepseek": ("deepseek_models", "model-a", "deepseek/model-a"),
    "mistral": ("mistral_models", "model-a", "mistral/model-a"),
    "groq": ("groq_models", "model-a", "groq/model-a"),
    "openrouter": ("openrouter_models", "vendor/model-a", "openrouter/vendor/model-a"),
    "custom": ("custom_models", "my-model", "openai/my-model"),
}
GATEWAY_ONLY = [
    "vertex",
    "azure-api-key",
    "azure-service-principal",
    "bedrock-static-keys",
    "bedrock-instance-profile",
]
BYOK_ENV = (
    "OH_WEB_CLIENT_FEATURE_FLAGS_ALLOW_USER_LLM_CONFIGURATION",
    "OH_ALLOW_USER_LLM_CONFIGURATION",
)


@cache
def _openhands_spec() -> dict:
    doc = next(
        d
        for d in yaml.safe_load_all(OPENHANDS.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("metadata", {}).get("name") == "openhands"
    )
    return doc["spec"]


def _render(overrides: dict[str, str]) -> dict:
    """Effective litellm switches, BYOK env and defaultLlm for a config."""
    cfg = _config_options(overrides)
    spec = _openhands_spec()
    values = spec["values"]
    fields = {
        "litellm": values["litellm"]["enabled"],
        "litellm_helm": values["litellm-helm"]["enabled"],
        **{env: values["env"][env] for env in BYOK_ENV},
    }
    blocks = [
        (i, entry)
        for i, entry in enumerate(spec.get("optionalValues", []))
        if "defaultLlm" in entry.get("values", {})
    ]
    for i, entry in blocks:
        default_llm = entry["values"]["defaultLlm"]
        fields[f"w{i}"] = entry["when"]
        fields[f"m{i}"] = default_llm["model"]
        if "baseUrl" in default_llm:
            fields[f"b{i}"] = default_llm["baseUrl"]
    rendered = _helm_render(fields, cfg)
    default_llm = None
    for i, entry in blocks:
        if str(rendered[f"w{i}"]) != "true":
            continue
        assert default_llm is None, "more than one defaultLlm block matched"
        default_llm = {
            **entry["values"]["defaultLlm"],
            "model": rendered[f"m{i}"],
            "baseUrl": rendered.get(f"b{i}"),
        }
    return {
        "litellm": str(rendered["litellm"]),
        "litellm_helm": str(rendered["litellm_helm"]),
        "byok": {env: str(rendered[env]) for env in BYOK_ENV},
        "default_llm": default_llm,
        "cfg": cfg,
    }


def _off(provider: str, **extra: str) -> dict:
    return _render({**PROVIDERS[provider][0], "enable_litellm": "0", **extra})


@pytest.mark.parametrize("provider", sorted(DIRECT))
def test_off_seeds_the_first_model_as_the_direct_default(provider: str) -> None:
    field, first, expected = DIRECT[provider]
    result = _off(provider, **{field: f"  {first} , other-model\r\n"})
    default_llm = result["default_llm"]
    assert default_llm is not None, f"{provider}: no defaultLlm when LiteLLM is off"
    assert default_llm["enabled"] is True
    assert default_llm["model"] == expected
    assert default_llm["auth"]["existingSecret"] == "litellm-env-secrets"
    assert default_llm["auth"]["secretKey"] in exported_secret_env(provider)


def test_custom_default_keeps_an_explicit_provider_prefix_and_base_url() -> None:
    result = _off("custom", custom_models="anthropic/claude-x\nother")
    assert result["default_llm"]["model"] == "anthropic/claude-x"
    assert result["default_llm"]["baseUrl"] == "https://llm.example.com/v1"


@pytest.mark.parametrize("provider", GATEWAY_ONLY)
def test_gateway_only_providers_get_no_default_and_a_notice(provider: str) -> None:
    result = _off(provider)
    assert result["default_llm"] is None
    assert result["cfg"]["litellm_off_unsupported_provider_notice"] == ""
    notice_visible = _helm_render(
        {"n": _notice_when()}, result["cfg"]
    )["n"]
    assert str(notice_visible) == "true"


def _notice_when() -> str:
    doc = yaml.safe_load(
        (OPENHANDS.parent / "config.yaml").read_text(encoding="utf-8")
    )
    return next(
        item["when"]
        for group in doc["spec"]["groups"]
        for item in group.get("items", [])
        if item["name"] == "litellm_off_unsupported_provider_notice"
    )


@pytest.mark.parametrize("provider", sorted(DIRECT))
def test_notice_hidden_for_direct_providers(provider: str) -> None:
    result = _off(provider)
    assert str(_helm_render({"n": _notice_when()}, result["cfg"])["n"]) == "false"


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_off_disables_the_gateway_and_forces_byok(provider: str) -> None:
    result = _off(provider, allow_user_llm_configuration="0")
    assert result["litellm"] == "false"
    assert result["litellm_helm"] == "false"
    assert set(result["byok"].values()) == {"true"}


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_on_by_default_keeps_the_gateway_and_no_direct_default(provider: str) -> None:
    result = _render(PROVIDERS[provider][0])
    assert result["litellm"] == "true"
    assert result["litellm_helm"] == "true"
    assert result["default_llm"] is None
    assert set(result["byok"].values()) == {"false"}


@pytest.mark.parametrize("enabled, expected", [("1", True), ("0", False)])
def test_litellm_status_informers_follow_the_toggle(enabled: str, expected: bool) -> None:
    doc = yaml.safe_load(APPLICATION.read_text(encoding="utf-8"))
    informers = [i for i in doc["spec"]["statusInformers"] if "litellm" in i]
    assert len(informers) == 2
    cfg = _config_options({"enable_litellm": enabled})
    rendered = _helm_render({f"i{n}": v for n, v in enumerate(informers)}, cfg)
    assert all(bool(str(v or "").strip()) is expected for v in rendered.values())
