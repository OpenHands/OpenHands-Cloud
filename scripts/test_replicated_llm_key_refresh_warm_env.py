"""Keep every Replicated warm-runtime config claimable for the env the app
injects on each runtime request.

runtime-api claims a warm pod only on an exact environment match, so every env
var the app sends on a runtime start request must also appear, with the same
value, in each warm-runtime config. A key the app emits that is absent from the
warm env makes the pool unclaimable for those requests and the conversation
cold-starts instead.

Two classes of app-injected env exist:

* ``global.agentServerEnv`` -- the chart serialises this into
  ``OH_AGENT_SERVER_ENV`` on the app and the runtime-api subchart mirrors the
  same map into every warm config (see
  ``charts/openhands/charts/runtime-api/templates/warm-runtimes-configmap.yaml``),
  so that class is kept in sync structurally from a single source.
* Request-derived env the app computes per request from ``WEB_HOST`` and
  ``LITE_LLM_API_URL`` (the managed-LLM key refresh contract, webhooks, CORS).
  These are hand-mirrored into each warm config and are the class that silently
  drifts -- the missing ``OH_LLM_API_KEY_REFRESH_*`` keys were exactly this bug.
  This test rebuilds that contract from the rendered chart and requires every
  ``configsByName`` entry to carry it.

When the app starts emitting a new always-on request env var, add it to
``injected_request_env`` below (and mirror it into the warm config). ``LMNR_*``
is intentionally excluded: the app only emits it when analytics is enabled, so
it is not an unconditional match key.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENHANDS_CHART = REPO_ROOT / "charts" / "openhands"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"
APP_HOSTNAME_TEMPLATE = '{{repl ConfigOption "computed_app_hostname" }}'
APP_HOSTNAME = "app.example.com"


def rendered_env_value(manifest: str, env_name: str) -> str:
    values = set(
        re.findall(
            rf"(?m)^\s+- name: {re.escape(env_name)}\n\s+value: \"?([^\"\n]+)\"?$",
            manifest,
        )
    )
    assert len(values) == 1, (env_name, values)
    return values.pop()


def injected_request_env(web_host: str, lite_llm_api_url: str) -> dict[str, str]:
    """The always-on env the app derives per runtime request from WEB_HOST and
    LITE_LLM_API_URL. Every warm config must carry these verbatim or the pool is
    unclaimable for the requests that carry them."""
    return {
        "OH_LLM_API_KEY_REFRESH_URL": (
            f"https://{web_host}/api/keys/llm/managed/current"
        ),
        "OH_LLM_API_KEY_REFRESH_HEADERS": json.dumps(
            {"X-Session-API-Key": "${OH_SESSION_API_KEYS_0}"}
        ),
        "OH_LLM_API_KEY_REFRESH_BASE_URLS": lite_llm_api_url,
        "OH_WEBHOOKS_0_BASE_URL": f"https://{web_host}/api/v1/webhooks",
        "OH_ALLOW_CORS_ORIGINS_0": f"https://{web_host}",
    }


def render_openhands_app(spec: dict) -> str:
    values = spec["values"]
    return subprocess.run(
        [
            "helm",
            "template",
            spec["releaseName"],
            str(OPENHANDS_CHART),
            "--namespace",
            spec["namespace"],
            "--set-string",
            f"ingress.host={APP_HOSTNAME}",
            "--set",
            f"litellm-helm.enabled={str(values['litellm-helm']['enabled']).lower()}",
            "--show-only",
            "templates/deployment.yaml",
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout


def test_replicated_warm_env_matches_app_injected_request_env() -> None:
    spec = yaml.safe_load(REPLICATED_OPENHANDS.read_text(encoding="utf-8"))["spec"]
    configs = spec["values"]["runtime-api"]["warmRuntimes"]["configsByName"]

    manifest = render_openhands_app(spec)
    web_host = rendered_env_value(manifest, "WEB_HOST")
    lite_llm_api_url = rendered_env_value(manifest, "LITE_LLM_API_URL")
    assert web_host == APP_HOSTNAME

    expected = injected_request_env(web_host, lite_llm_api_url)

    checked = 0
    for name, config in configs.items():
        environment = config.get("environment")
        if environment is None:
            continue
        warm_env = {
            key: value.replace(APP_HOSTNAME_TEMPLATE, APP_HOSTNAME)
            if isinstance(value, str)
            else value
            for key, value in environment.items()
        }
        missing = {
            key: value
            for key, value in expected.items()
            if warm_env.get(key) != value
        }
        assert not missing, (
            f"warm config {name!r} is missing or mismatches app-injected request "
            f"env (managed-proxy requests would cold-start): {missing}"
        )
        checked += 1

    assert checked, "no warm configsByName entries with an environment were checked"
