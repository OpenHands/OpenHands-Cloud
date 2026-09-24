"""Render the bundled LiteLLM routes for every KOTS ``llm_provider`` option.

The KOTS ``repl{{ }}`` expressions in replicated/openhands.yaml and
replicated/secrets.yaml are Go text/template + sprig, so they are rendered
here with ``helm template`` after replacing ``ConfigOption`` calls with lookups
into a values map. Config items resolve like KOTS (user value > ``value`` >
``default``), items hidden by their ``when`` resolve empty, and matching
optionalValues blocks are applied in order (maps merge, arrays replace).

For each provider the effective ``model_list`` must route only through that
provider, reference only API-key env vars that the rendered
``litellm-env-secrets`` Secret exports, and ``LITELLM_DEFAULT_MODEL`` must
name one of its routes. Before the six key-only providers were wired, they
fell through to the Anthropic base routes, whose ANTHROPIC_API_KEY is not set.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from functools import cache
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / "replicated" / "config.yaml"
OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"
SECRETS = REPO_ROOT / "replicated" / "secrets.yaml"
SECRETS_CHART = REPO_ROOT / "charts" / "openhands-secrets"

# Every selectable provider configuration, with the LiteLLM prefix its routes
# must use. Each llm_provider dropdown item needs at least one entry here.
PROVIDERS = {
    "anthropic": (
        {"llm_provider": "anthropic", "anthropic_api_key": "k"},
        "anthropic/",
    ),
    "openai": ({"llm_provider": "openai", "openai_api_key": "k"}, "openai/"),
    "gemini": (
        {
            "llm_provider": "google",
            "google_api_type": "gemini",
            "google_gemini_api_key": "k",
        },
        "gemini/",
    ),
    "vertex": (
        {
            "llm_provider": "google",
            "google_api_type": "vertex",
            "google_vertex_credentials": "{}",
            "google_vertex_project_id": "p",
            "google_vertex_location": "us-central1",
        },
        "vertex_ai/",
    ),
    "deepseek": ({"llm_provider": "deepseek", "deepseek_api_key": "k"}, "deepseek/"),
    "mistral": ({"llm_provider": "mistral", "mistral_api_key": "k"}, "mistral/"),
    "azure-api-key": (
        {
            "llm_provider": "azure",
            "azure_auth_method": "api_key",
            "azure_api_key": "k",
            "azure_endpoint": "https://example.openai.azure.com",
            "azure_deployments": "gpt-4o",
        },
        "azure/",
    ),
    "azure-service-principal": (
        {
            "llm_provider": "azure",
            "azure_auth_method": "service_principal",
            "azure_tenant_id": "t",
            "azure_client_id": "c",
            "azure_client_secret": "s",
            "azure_endpoint": "https://example.openai.azure.com",
            "azure_deployments": "gpt-4o",
        },
        "azure/",
    ),
    "groq": ({"llm_provider": "groq", "groq_api_key": "k"}, "groq/"),
    "openrouter": (
        {"llm_provider": "openrouter", "openrouter_api_key": "k"},
        "openrouter/",
    ),
    "bedrock-static-keys": (
        {
            "llm_provider": "bedrock",
            "aws_auth_method": "static_keys",
            "aws_access_key_id": "a",
            "aws_secret_access_key": "s",
            "aws_region_name": "us-east-1",
            "bedrock_model_ids": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        },
        "bedrock/",
    ),
    "bedrock-instance-profile": (
        {
            "llm_provider": "bedrock",
            "aws_auth_method": "instance_profile",
            "aws_region_name": "us-east-1",
            "bedrock_model_ids": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        },
        "bedrock/",
    ),
    "custom": (
        {
            "llm_provider": "custom",
            "custom_api_key": "k",
            "custom_base_url": "https://llm.example.com/v1",
            "custom_models": "my-model",
        },
        "openai/",
    ),
}


def _to_helm(template: str) -> str:
    """Rewrite KOTS template syntax into a plain Helm template."""
    template = template.replace("{{repl ", "{{ ").replace("repl{{", "{{")
    template = re.sub(
        r'ConfigOptionEquals "([^"]+)" "([^"]*)"',
        r'(eq (index $.Values.cfg "\1") "\2")',
        template,
    )
    return re.sub(
        r'ConfigOption(?:Data)? "([^"]+)"', r'(index $.Values.cfg "\1")', template
    )


def _helm_render(fields: dict[str, str], cfg: dict[str, str]) -> dict[str, str]:
    """Render each KOTS template in ``fields`` with ConfigOption values ``cfg``."""
    body = "".join(f"{key}: |-\n  {_to_helm(value)}\n" for key, value in fields.items())
    with tempfile.TemporaryDirectory() as tmp:
        chart = Path(tmp)
        (chart / "templates").mkdir()
        (chart / "Chart.yaml").write_text(
            "apiVersion: v2\nname: kots-render\nversion: 0.0.0\n"
        )
        (chart / "templates" / "out.yaml").write_text(body)
        (chart / "values.yaml").write_text(json.dumps({"cfg": cfg}))
        result = subprocess.run(
            ["helm", "template", "r", str(chart), "--show-only", "templates/out.yaml"],
            capture_output=True,
            text=True,
        )
    assert result.returncode == 0, result.stderr
    return yaml.safe_load(result.stdout)


def _config_items() -> list[dict]:
    doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return [item for group in doc["spec"]["groups"] for item in group.get("items", [])]


def _config_options(overrides: dict[str, str]) -> dict[str, str]:
    """Resolve every config item the way KOTS ConfigOption does."""
    items = _config_items()
    cfg = {}
    for item in items:
        value = overrides.get(item["name"])
        for candidate in (value, item.get("value"), item.get("default")):
            if candidate not in (None, "") and "repl" not in str(candidate):
                value = str(candidate)
                break
        cfg[item["name"]] = value or ""
    whens = {i["name"]: i["when"] for i in items if isinstance(i.get("when"), str)}
    visible = _helm_render(whens, cfg)
    return {
        name: ("" if str(visible.get(name)) == "false" else v)
        for name, v in cfg.items()
    }


@cache
def _optional_litellm_blocks() -> tuple[tuple[str, str | None, str | None], ...]:
    doc = next(
        d
        for d in yaml.safe_load_all(OPENHANDS.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("metadata", {}).get("name") == "openhands"
    )
    spec = doc["spec"]
    blocks = [
        (
            "true",
            spec["values"]["env"]["LITELLM_DEFAULT_MODEL"],
            spec["values"]["litellm-helm"]["proxy_config"]["model_list"],
        )
    ]
    for entry in spec.get("optionalValues", []):
        values = entry.get("values", {})
        default = values.get("env", {}).get("LITELLM_DEFAULT_MODEL")
        model_list = (
            values.get("litellm-helm", {}).get("proxy_config", {}).get("model_list")
        )
        if default or model_list:
            blocks.append((entry["when"], default, model_list))
    return tuple(blocks)


@cache
def render_routes(provider: str) -> tuple[str, list[dict]]:
    """Return the effective (LITELLM_DEFAULT_MODEL, model_list) for a provider."""
    return _render_routes(PROVIDERS[provider][0])


def _render_routes(overrides: dict[str, str]) -> tuple[str, list[dict]]:
    cfg = _config_options(overrides)
    fields = {}
    for i, (when, default, model_list) in enumerate(_optional_litellm_blocks()):
        fields[f"w{i}"] = when
        if default:
            fields[f"d{i}"] = default
        if model_list:
            fields[f"m{i}"] = model_list
    rendered = _helm_render(fields, cfg)
    default_model, routes = None, None
    for i in range(len(_optional_litellm_blocks())):
        if str(rendered[f"w{i}"]) != "true":
            continue
        default_model = rendered.get(f"d{i}", default_model)
        if f"m{i}" in rendered:
            routes = json.loads(rendered[f"m{i}"])
    return default_model, routes


MODEL_FIELDS = {
    "openai": "openai_models",
    "deepseek": "deepseek_models",
    "mistral": "mistral_models",
    "groq": "groq_models",
    "openrouter": "openrouter_models",
    "gemini": "gemini_models",
}
LEGACY_MODEL = "claude-sonnet-4-5-20250929"


@pytest.mark.parametrize("provider", MODEL_FIELDS)
def test_legacy_model_route_remains_available(provider: str) -> None:
    default, routes = render_routes(provider)
    visible = [r for r in routes if not r.get("model_info", {}).get("openhands_hidden")]
    alias = next(r for r in routes if r["model_name"] == LEGACY_MODEL)
    assert alias["litellm_params"] == visible[0]["litellm_params"]
    assert alias["model_info"] == {
        "openhands_hidden": True,
        "openhands_canonical": visible[0]["model_name"],
    }
    assert default == "litellm_proxy/" + visible[0]["model_name"]
    assert len({r["model_name"] for r in routes}) == len(routes)


@pytest.mark.parametrize("provider", MODEL_FIELDS)
def test_model_list_trims_splits_and_deduplicates(provider: str) -> None:
    names = (
        ["vendor/model-a", "vendor/model-b"]
        if provider == "openrouter"
        else ["model-a", "model-b"]
    )
    default, routes = _render_routes(
        {
            **PROVIDERS[provider][0],
            MODEL_FIELDS[provider]: f"  {names[0]} , {names[1]}\r\n\n {names[0]}  \n",
        }
    )
    visible = [r for r in routes if not r.get("model_info", {}).get("openhands_hidden")]
    assert [r["model_name"] for r in visible] == names
    assert [r["litellm_params"]["model"] for r in visible] == [
        PROVIDERS[provider][1] + n for n in names
    ]
    assert default == "litellm_proxy/" + names[0]
    assert (
        next(r for r in routes if r["model_name"] == LEGACY_MODEL)["model_info"][
            "openhands_canonical"
        ]
        == names[0]
    )


@pytest.mark.parametrize("provider", MODEL_FIELDS)
def test_configured_legacy_name_remains_visible_without_duplicate_alias(
    provider: str,
) -> None:
    _, routes = _render_routes(
        {
            **PROVIDERS[provider][0],
            MODEL_FIELDS[provider]: f"another-model\n{LEGACY_MODEL}",
        }
    )
    matches = [r for r in routes if r["model_name"] == LEGACY_MODEL]
    assert len(matches) == 1
    assert not matches[0].get("model_info", {}).get("openhands_hidden")
    assert (
        matches[0]["litellm_params"]["model"] == PROVIDERS[provider][1] + LEGACY_MODEL
    )


@pytest.mark.parametrize("provider", MODEL_FIELDS)
def test_empty_model_list_does_not_create_an_alias(provider: str) -> None:
    _, routes = _render_routes(
        {**PROVIDERS[provider][0], MODEL_FIELDS[provider]: " , \r\n "}
    )
    assert routes == []


@cache
def exported_secret_env(provider: str) -> frozenset[str]:
    """Env names the rendered litellm-env-secrets Secret exports."""
    cfg = _config_options(PROVIDERS[provider][0])
    doc = next(
        d
        for d in yaml.safe_load_all(SECRETS.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("kind") == "HelmChart"
    )
    template = SECRETS_CHART / "templates" / "litellm-env-secrets.yaml"
    used = set(
        re.findall(r"\.Values\.config\.(\w+)", template.read_text(encoding="utf-8"))
    )
    config = _helm_render(
        {k: v for k, v in doc["spec"]["values"]["config"].items() if k in used}, cfg
    )
    result = subprocess.run(
        [
            "helm",
            "template",
            "openhands-secrets",
            str(SECRETS_CHART),
            "--show-only",
            "templates/litellm-env-secrets.yaml",
            "--values",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        input=json.dumps(
            {"config": {k: "" if v is None else str(v) for k, v in config.items()}}
        ),
    )
    return frozenset((yaml.safe_load(result.stdout).get("data") or {}).keys())


def test_every_llm_provider_option_is_covered() -> None:
    dropdown = next(i for i in _config_items() if i["name"] == "llm_provider")
    covered = {overrides["llm_provider"] for overrides, _ in PROVIDERS.values()}
    assert {item["name"] for item in dropdown["items"]} == covered


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_routes_use_only_the_selected_provider(provider: str) -> None:
    _, routes = render_routes(provider)
    prefix = PROVIDERS[provider][1]
    assert routes, f"{provider}: no LiteLLM routes"
    wrong = [
        r["litellm_params"]["model"]
        for r in routes
        if not r["litellm_params"]["model"].startswith(prefix)
    ]
    assert not wrong, f"{provider}: routes outside {prefix}: {wrong}"


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_route_credentials_are_exported_by_the_secret(provider: str) -> None:
    _, routes = render_routes(provider)
    referenced = {
        value.removeprefix("os.environ/")
        for route in routes or []
        for value in route["litellm_params"].values()
        if isinstance(value, str) and value.startswith("os.environ/")
    }
    missing = referenced - exported_secret_env(provider)
    assert not missing, (
        f"{provider}: routes read env vars the secret does not set: {missing}"
    )


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_default_model_is_a_visible_route(provider: str) -> None:
    default_model, routes = render_routes(provider)
    visible = {
        r["model_name"]
        for r in routes or []
        if not (r.get("model_info") or {}).get("openhands_hidden")
    }
    assert default_model and default_model.startswith("litellm_proxy/")
    assert default_model.removeprefix("litellm_proxy/") in visible, (
        f"{provider}: LITELLM_DEFAULT_MODEL {default_model} is not one of {sorted(visible)}"
    )
