#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyYAML", "pytest"]
# ///
"""Behavior tests for the Compose / Helm defaults check."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from check_compose_helm_defaults import check, differences, recorded_names, resolve_compose_value

COMPOSE = {
    "services": {
        "db": {},
        "automation-server": {},
        "app": {
            "environment": {
                "ENABLE_BILLING": "false",
                "RATE_LIMITING_ENABLED": "'false'".strip("'"),
                "WEB_HOST": "${WEB_HOST:-localhost}",
                "OH_WEB_URL": "${WEB_URL:-http://localhost:${APP_PORT:-9043}}",
                "SUPER_ADMIN": "1",
            }
        },
    }
}


def manifests(env: list[dict], workloads: tuple[str, ...] = ("oh-postgresql",)) -> list[dict]:
    app = {
        "kind": "Deployment",
        "metadata": {"name": "openhands"},
        "spec": {"template": {"spec": {"containers": [{"env": env}]}}},
    }
    return [app] + [{"kind": "StatefulSet", "metadata": {"name": name}} for name in workloads]


def test_compose_defaults_expand_as_if_nothing_is_set():
    assert resolve_compose_value("${WEB_HOST:-localhost}") == "localhost"
    assert resolve_compose_value("${WEB_URL:-http://localhost:${APP_PORT:-9043}}") == "http://localhost:9043"
    assert resolve_compose_value("${POSTHOG_CLIENT_KEY-phc_x}") == "phc_x"
    assert resolve_compose_value("${OPENHANDS_LICENSE_KEY:-}") == ""


def test_reports_value_presence_and_bundled_service_differences():
    env = [
        {"name": "ENABLE_BILLING", "value": "false"},
        {"name": "WEB_HOST", "value": "localhost"},
        {"name": "OH_WEB_URL", "value": "http://localhost:9043"},
        {"name": "SUPER_ADMIN", "value": "true"},
        {"name": "DB_PASS", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}},
    ]
    found = differences(COMPOSE, manifests(env))

    assert found == {
        "DB_PASS": ("<unset>", "<from secret>"),
        "RATE_LIMITING_ENABLED": ("false", "<unset>"),
        "service:automation-server": ("bundled", "not bundled"),
    }


def test_fails_on_unrecorded_and_stale_rows_and_passes_when_exact():
    found = {"RATE_LIMITING_ENABLED": ("false", "<unset>")}
    table = "| Name | Reason |\n|---|---|\n| `RATE_LIMITING_ENABLED` | x |\n| `GONE` | y |\n"

    problems = check(found, recorded_names(table))
    assert len(problems) == 1 and "`GONE`" in problems[0]
    assert len(check(found, set())) == 1
    assert check(found, {"RATE_LIMITING_ENABLED"}) == []
