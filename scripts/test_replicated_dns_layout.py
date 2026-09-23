"""Tests for the sandbox hostname layout contract between chart and installer.

A sandbox is served at {id}{RUNTIME_URL_SEPARATOR}{RUNTIME_BASE_URL}, and the
app reaches it through RUNTIME_URL_PATTERN. The two are set in different places
and have to agree, so the chart ships no separator at all (see
charts/openhands/tests/sandbox_hostname_layout_test.yaml) and every installer
that wants the flat layout sets one explicitly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"
REPLICATED_CONFIG = REPO_ROOT / "replicated" / "config.yaml"

SEPARATOR_OPTION = '{{repl ConfigOption "computed_runtime_hostname_separator" }}'
BASE_OPTION = '{{repl ConfigOption "computed_runtime_base_hostname" }}'
API_HOST_OPTION = '{{repl ConfigOption "computed_runtime_api_hostname" }}'


@pytest.fixture(scope="module")
def helm_chart_cr() -> dict:
    return yaml.safe_load(REPLICATED_OPENHANDS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config_items() -> dict:
    config = yaml.safe_load(REPLICATED_CONFIG.read_text(encoding="utf-8"))
    return {
        item["name"]: item
        for group in config["spec"]["groups"]
        for item in group["items"]
    }


def optional_values(chart_cr: dict, marker: str) -> dict:
    """The optionalValues block whose `when` mentions `marker`."""
    matches = [
        block
        for block in chart_cr["spec"]["optionalValues"]
        if marker in block.get("when", "")
    ]
    assert len(matches) == 1, f"expected one optionalValues block for {marker}"
    return matches[0]["values"]


def test_installer_sets_the_separator_rather_than_inheriting_a_chart_default(
    helm_chart_cr: dict,
) -> None:
    runtime_api_env = helm_chart_cr["spec"]["values"]["runtime-api"]["env"]

    assert runtime_api_env["RUNTIME_URL_SEPARATOR"] == SEPARATOR_OPTION
    assert runtime_api_env["RUNTIME_BASE_URL"] == BASE_OPTION


def test_runtime_api_advertises_its_own_ingress_for_storage_callbacks(
    helm_chart_cr: dict,
) -> None:
    runtime_api_env = helm_chart_cr["spec"]["values"]["runtime-api"]["env"]

    assert runtime_api_env["RUNTIME_API_BASE_URL"] == f"https://{API_HOST_OPTION}"


def test_app_sandbox_url_is_built_from_the_same_separator_and_base(
    helm_chart_cr: dict,
) -> None:
    pattern = helm_chart_cr["spec"]["values"]["env"]["RUNTIME_URL_PATTERN"]

    assert pattern == f"https://{{runtime_id}}{SEPARATOR_OPTION}{BASE_OPTION}"


def test_path_routing_overrides_both_sides_to_the_shared_host_form(
    helm_chart_cr: dict,
) -> None:
    values = optional_values(helm_chart_cr, '"runtime_routing_mode" "path"')

    assert values["runtime-api"]["env"]["RUNTIME_URL_SEPARATOR"] == "/"
    assert values["runtime-api"]["env"]["RUNTIME_ROUTING_MODE"] == "path"
    assert values["env"]["RUNTIME_URL_PATTERN"] == f"https://{BASE_OPTION}/{{runtime_id}}"


def test_only_the_simple_layout_flattens_sandboxes_onto_the_base_domain(
    config_items: dict,
) -> None:
    separator = config_items["computed_runtime_hostname_separator"]

    assert separator["hidden"] is True
    # A `default:` re-renders on every config change, so an install that never
    # touched the field still tracks its hostname_mode. A `value:` would pin the
    # separator on first render and strand it there.
    assert "value" not in separator
    assert separator["default"] == (
        '{{repl if ConfigOptionEquals "hostname_mode" "wildcard" }}-'
        '{{repl else }}.{{repl end }}'
    )


def test_advertised_certificate_wildcard_tracks_the_separator(
    config_items: dict,
) -> None:
    # A dash makes each sandbox a sibling of the runtime base rather than a
    # child, so the covering wildcard sits one label above it: the SAN the
    # operator is told to buy has to move with the separator.
    wildcard = config_items["computed_sandbox_wildcard"]["default"]

    assert wildcard == (
        '{{repl if eq (ConfigOption "computed_runtime_hostname_separator") "-" }}'
        '*.{{repl splitList "." (ConfigOption "computed_runtime_base_hostname")'
        ' | rest | join "." }}'
        f'{{{{repl else }}}}*.{BASE_OPTION}{{{{repl end }}}}'
    )


def test_hostname_mode_pins_existing_installs_to_the_legacy_layout(
    config_items: dict,
) -> None:
    # Sequence is 0 only on a fresh install. Using `value:` (not `default:`)
    # means the choice is written once and never re-rendered, so an upgrade
    # cannot move an existing install's keycloak and sandbox hostnames off the
    # names its certificate was issued for.
    mode = config_items["hostname_mode"]

    assert "default" not in mode
    assert mode["value"] == (
        '{{repl if eq Sequence 0 }}wildcard{{repl else }}derive{{repl end }}'
    )
    assert {item["name"] for item in mode["items"]} == {"wildcard", "derive", "custom"}
