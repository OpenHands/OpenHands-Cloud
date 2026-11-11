#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyYAML"]
# ///
"""Fail when the Helm chart's defaults differ from Docker Compose's without a
recorded reason.

Docker Compose (``docker-compose.yml`` in OpenHands/enterprise) is the default
OpenHands Enterprise install. This check renders the openhands chart, reads the
Compose file at the chart's ``appVersion`` tag, and compares:

  * the app container's environment (names and values), and
  * which services are bundled.

Every difference must be listed in ``charts/openhands/compose-differences.md``,
and every listed name must still differ, so the table stays exact.

Run it from the chart repo root:

    uv run scripts/check_compose_helm_defaults.py

Exit status is 0 when every difference is recorded, and 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ENTERPRISE_REPO = "OpenHands/enterprise"
CHART = Path("charts/openhands")
TABLE = CHART / "compose-differences.md"
VALUES = Path("scripts/compose_parity_values.yaml")
SECRET = "<from secret>"

# Compose service -> name fragment of the chart workload that bundles it.
SERVICES = {
    "db": "postgresql",
    "redis": "redis",
    "automation-server": "automation",
    "canvas-server": "agent-canvas",
}

TRUTHY = {"1", "true", "yes"}
FALSY = {"0", "false", "no"}


def normalize(value: object) -> str:
    text ="" if value is None else str(value).strip()
    if text.lower() in TRUTHY:
        return "true"
    if text.lower() in FALSY:
        return "false"
    return text


def resolve_compose_value(value: str) -> str:
    """Expand ${VAR:-default}, ${VAR-default} and ${VAR} as if no VAR is set."""
    pattern = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?::?-([^${}]*))?\}")
    while True:
        expanded = pattern.sub(lambda m: m.group(1) or "", value)
        if expanded == value:
            return expanded.replace("$$", "$")
        value = expanded


def compose_env(compose: dict) -> dict[str, str]:
    env = compose["services"]["app"].get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    return {name: normalize(resolve_compose_value(str(value))) for name, value in env.items()}


def compose_services(compose: dict) -> set[str]:
    return {name for name in compose["services"] if name in SERVICES}


def render_chart(chart: Path, values: Path) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "oh", str(chart), "-f", str(values)],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise SystemExit(f"helm template failed:\n{result.stderr.strip()}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def helm_env(manifests: list[dict]) -> dict[str, str]:
    for doc in manifests:
        if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "openhands":
            container = doc["spec"]["template"]["spec"]["containers"][0]
            return {
                item["name"]: SECRET if "valueFrom" in item else normalize(item.get("value"))
                for item in container.get("env", [])
            }
    raise SystemExit("the rendered chart has no openhands Deployment")


def helm_services(manifests: list[dict]) -> set[str]:
    workloads = [
        doc["metadata"]["name"]
        for doc in manifests
        if doc.get("kind") in ("Deployment", "StatefulSet")
    ]
    return {
        service
        for service, fragment in SERVICES.items()
        if any(fragment in name for name in workloads)
    }


def differences(compose: dict, manifests: list[dict]) -> dict[str, tuple[str, str]]:
    """Map each differing name to its (Compose, Helm) values."""
    left ={k: v for k, v in compose_env(compose).items() if v != ""}
    right = {k: v for k, v in helm_env(manifests).items() if v != ""}
    found = {
        name: (left.get(name, "<unset>"), right.get(name, "<unset>"))
        for name in sorted(set(left) | set(right))
        if left.get(name) != right.get(name)
    }
    bundled_left, bundled_right = compose_services(compose), helm_services(manifests)
    for service in sorted(bundled_left ^ bundled_right):
        found[f"service:{service}"] = (
            "bundled" if service in bundled_left else "not bundled",
            "bundled" if service in bundled_right else "not bundled",
        )
    return found


def recorded_names(table: str) -> set[str]:
    """Names in the first cell of each table row, written in backticks."""
    return set(re.findall(r"^\|\s*`([^`]+)`", table, flags=re.MULTILINE))


def check(found: dict[str, tuple[str, str]], recorded: set[str]) -> list[str]:
    problems = [
        f"not in {TABLE}: `{name}` (Compose {left!r}, Helm {right!r})"
        for name, (left, right) in found.items()
        if name not in recorded
    ]
    problems += [
        f"in {TABLE} but no longer different: `{name}`"
        for name in sorted(recorded - set(found))
    ]
    return problems


def app_version(chart: Path) -> str:
    return str(yaml.safe_load((chart / "Chart.yaml").read_text())["appVersion"])


def fetch_compose(ref: str, token: str | None) -> str:
    url = f"https://raw.githubusercontent.com/{ENTERPRISE_REPO}/{ref}/docker-compose.yml"
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode()
    except urllib.error.URLError as error:
        raise SystemExit(f"cannot read {url}: {error}") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--compose", type=Path, help="local docker-compose.yml instead of the tag")
    args = parser.parse_args(argv)

    if args.compose:
        compose_text = args.compose.read_text()
    else:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        compose_text = fetch_compose(app_version(CHART), token)

    found = differences(yaml.safe_load(compose_text), render_chart(CHART, VALUES))
    recorded = recorded_names(TABLE.read_text()) if TABLE.exists() else set()
    problems = check(found, recorded)
    for line in problems:
        print(line)
    if problems:
        print(f"\n{len(problems)} problem(s). Fix the default, or record the reason in {TABLE}.")
        return 1
    print(f"Every difference between Compose and Helm is recorded in {TABLE}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
