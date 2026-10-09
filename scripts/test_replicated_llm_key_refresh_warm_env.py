"""Keep every Replicated warm-runtime config consistent with the env the app
injects on each runtime request.

runtime-api claims a warm pod when the request env equals the warm config env,
compared after it drops a set of deployment keys. A key the app emits that the
warm config lacks or gets wrong fails in one of two ways:

* compared keys (``OH_LLM_API_KEY_REFRESH_*``, ``LMNR_*``,
  ``global.agentServerEnv``): no warm pod matches, so every conversation
  cold-starts;
* dropped keys (``OH_WEBHOOKS_0_BASE_URL``, ``OH_ALLOW_CORS_ORIGINS_0``): the
  pod is still claimed, but claiming does not rewrite its env, so the sandbox
  runs with the warm value and sends webhooks / allows CORS for the wrong host.

Two classes of app-injected env exist:

* ``global.agentServerEnv`` -- the chart serialises this into
  ``OH_AGENT_SERVER_ENV`` on the app and the runtime-api subchart mirrors the
  same map into every warm config (see
  ``charts/openhands/charts/runtime-api/templates/warm-runtimes-configmap.yaml``),
  so that class is kept in sync from a single source; a sentinel test below pins
  that merge.
* Request-derived env the app computes per request from ``WEB_HOST`` and
  ``LITE_LLM_API_URL`` (the managed-LLM key refresh contract, webhooks, CORS).
  These are hand-mirrored into each warm config and are the class that silently
  drifts -- the missing ``OH_LLM_API_KEY_REFRESH_*`` keys were the original bug.
  This test rebuilds that contract from the rendered chart and requires every
  rendered warm config (Replicated and chart-default entries alike) to carry it.

When the app starts emitting a new always-on request env var, add it to
``injected_request_env`` below (and mirror it into the warm config). ``LMNR_*``
is also on every request: the chart always sets it on the app (``""`` when
Laminar is off) and the app auto-forwards the ``LMNR_`` prefix, so each warm
config must carry the same values Replicated sets on the app.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENHANDS_CHART = REPO_ROOT / "charts" / "openhands"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"
APP_HOSTNAME_TEMPLATE = '{{repl ConfigOption "computed_app_hostname" }}'
APP_HOSTNAME = "app.example.com"
WARM_CONFIGMAP = "charts/runtime-api/templates/warm-runtimes-configmap.yaml"


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
    LITE_LLM_API_URL. Every warm config must carry these verbatim: a drifted
    refresh key leaves the pool unclaimable, and a drifted webhook/CORS key
    leaves claimed pods running with the wrong value."""
    return {
        "OH_LLM_API_KEY_REFRESH_URL": (
            f"https://{web_host}/api/keys/llm/managed/current"
        ),
        "OH_LLM_API_KEY_REFRESH_HEADERS": json.dumps(
            {"X-Session-API-Key": "${SESSION_API_KEY}"}
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


def render_warm_configs(
    spec: dict, extra_agent_server_env: dict[str, str] | None = None
) -> list[dict]:
    """Render warm-runtimes.json the way a Replicated install gets it: Replicated
    values layered over the chart defaults, with global.agentServerEnv merged in."""
    values = spec["values"]
    # count is a KOTS template string; only the configs matter here.
    warm = {**values["runtime-api"]["warmRuntimes"], "count": 1}
    # Only agentServerEnv from global: the rest holds {{repl}} strings that
    # third-party subcharts tpl-render and helm cannot parse.
    agent_server_env = {
        **values["global"]["agentServerEnv"],
        **(extra_agent_server_env or {}),
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as overrides:
        yaml.safe_dump(
            {
                "global": {"agentServerEnv": agent_server_env},
                "runtime-api": {"warmRuntimes": warm},
            },
            overrides,
        )
        overrides.flush()
        manifest = subprocess.run(
            [
                "helm",
                "template",
                spec["releaseName"],
                str(OPENHANDS_CHART),
                "--namespace",
                spec["namespace"],
                "-f",
                overrides.name,
                "--show-only",
                WARM_CONFIGMAP,
            ],
            check=True,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        ).stdout
    return json.loads(yaml.safe_load(manifest)["data"]["warm-runtimes.json"])["configs"]


def test_replicated_warm_env_matches_app_injected_request_env() -> None:
    spec = yaml.safe_load(REPLICATED_OPENHANDS.read_text(encoding="utf-8"))["spec"]

    manifest = render_openhands_app(spec)
    web_host = rendered_env_value(manifest, "WEB_HOST")
    lite_llm_api_url = rendered_env_value(manifest, "LITE_LLM_API_URL")
    assert web_host == APP_HOSTNAME

    expected = injected_request_env(web_host, lite_llm_api_url)

    # Replicated sets the analytics-on LMNR_* values on the app from the same
    # anchors the warm config uses; the warm value must match those, not the
    # chart's "".
    app_lmnr_env = {
        key: value
        for block in spec["optionalValues"]
        for key, value in block["values"].get("env", {}).items()
        if key.startswith("LMNR_")
    }
    lmnr_keys = set(re.findall(r"(?m)^\s+- name: (LMNR_\w+)$", manifest))
    assert lmnr_keys and lmnr_keys <= app_lmnr_env.keys(), (lmnr_keys, app_lmnr_env)
    expected = {**expected, **app_lmnr_env}

    configs = render_warm_configs(spec)
    assert configs, "no warm-runtime configs rendered"
    for config in configs:
        warm_env = {
            key: value.replace(APP_HOSTNAME_TEMPLATE, APP_HOSTNAME)
            if isinstance(value, str)
            else value
            for key, value in config["environment"].items()
        }
        missing = {
            key: value
            for key, value in expected.items()
            if warm_env.get(key) != value
        }
        assert not missing, (
            f"warm config {config['name']!r} does not match app-injected request "
            f"env: {missing} (a compared key makes the pool unclaimable; a "
            "webhook/CORS key leaves claimed pods misrouted)"
        )


def test_every_warm_config_carries_global_agent_server_env() -> None:
    spec = yaml.safe_load(REPLICATED_OPENHANDS.read_text(encoding="utf-8"))["spec"]
    sentinel = {"OH_TEST_AGENT_SERVER_ENV_SENTINEL": "1"}
    expected = {**spec["values"]["global"]["agentServerEnv"], **sentinel}

    for config in render_warm_configs(spec, sentinel):
        assert expected.items() <= config["environment"].items(), config["name"]
