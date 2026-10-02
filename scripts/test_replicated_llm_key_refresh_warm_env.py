"""Keep the Replicated warm pool claimable for the LLM key refresh-on-401 env.

The app sends OH_LLM_API_KEY_REFRESH_{URL,HEADERS,BASE_URLS} on every runtime
request whenever WEB_HOST is set, and runtime-api claims warm
pods only on an exact env match. These assertions rebuild the values the app
derives from the chart-rendered WEB_HOST and LITE_LLM_API_URL and require the
Replicated warm env to carry identical ones.
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


def test_replicated_warm_env_matches_app_llm_key_refresh_env() -> None:
    spec = yaml.safe_load(REPLICATED_OPENHANDS.read_text(encoding="utf-8"))["spec"]
    values = spec["values"]
    warm_env = values["runtime-api"]["warmRuntimes"]["configsByName"]["default"][
        "environment"
    ]

    manifest = subprocess.run(
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
    web_host = rendered_env_value(manifest, "WEB_HOST")
    lite_llm_api_url = rendered_env_value(manifest, "LITE_LLM_API_URL")

    assert web_host == APP_HOSTNAME
    assert {
        key: value.replace(APP_HOSTNAME_TEMPLATE, APP_HOSTNAME)
        for key, value in warm_env.items()
        if key.startswith("OH_LLM_API_KEY_REFRESH_")
    } == {
        "OH_LLM_API_KEY_REFRESH_URL": (
            f"https://{web_host}/api/keys/llm/managed/current"
        ),
        "OH_LLM_API_KEY_REFRESH_HEADERS": json.dumps(
            {"X-Session-API-Key": "${SESSION_API_KEY}"}
        ),
        "OH_LLM_API_KEY_REFRESH_BASE_URLS": lite_llm_api_url,
    }
