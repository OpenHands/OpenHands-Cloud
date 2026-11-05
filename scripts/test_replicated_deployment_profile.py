"""Contract tests for the EXPERIMENTAL deployment-profile switch.

The Deployment Profile option (added as the first config group) lets an operator
install either the full OpenHands Enterprise platform or a runtime-api-only
"Sandbox Service". These tests pin the wiring that makes the sandbox_service
profile deploy nothing but runtime-api and the infrastructure it needs:

* the profile option is first, defaults to enterprise, and offers exactly the
  two documented choices;
* the components that are not part of a runtime-api-only install are gated on the
  enterprise profile, while runtime-api itself stays unconditionally enabled;
* the config groups that only feed those enterprise components are hidden when
  sandbox_service is selected;
* the trust-manager CA Bundle that runtime-api mounts still renders when the app
  is disabled.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"
REPLICATED_CONFIG = REPO_ROOT / "replicated" / "config.yaml"
CA_BUNDLE_TEMPLATE = REPO_ROOT / "charts" / "openhands" / "templates" / "ca-bundle.yaml"

ENTERPRISE_GATE = 'repl{{ ConfigOptionEquals "deployment_profile" "enterprise" }}'
SANDBOX_HIDE = 'repl{{ ne (ConfigOption "deployment_profile") "sandbox_service" }}'

# Config groups that only configure components absent from a runtime-api-only
# install; each must be hidden while sandbox_service is selected.
ENTERPRISE_ONLY_GROUPS = {
    "llm_configuration",
    "litellm_admin_console",
    "default_openhands_organization",
    "keycloak_administration",
    "authentication_security",
    "bitbucket_data_center_authentication",
    "azure_devops_authentication",
    "jira_data_center_integration",
    "jira_cloud_integration",
    "github_authentication",
    "gitlab_authentication",
    "enterprise_sso_authentication",
    "slack_configuration",
    "smtp_configuration",
    "automations_configuration",
    "agent_canvas_configuration",
    "analytics_configuration",
    "experimental",
}

# Components hard-enabled in the installer that the sandbox_service profile drops.
ENTERPRISE_ONLY_COMPONENTS = ["keycloak", "litellm-helm", "redis", "minio"]


@pytest.fixture(scope="module")
def helm_chart_cr() -> dict:
    return yaml.safe_load(REPLICATED_OPENHANDS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config() -> dict:
    return yaml.safe_load(REPLICATED_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def groups_by_name(config: dict) -> dict:
    return {group["name"]: group for group in config["spec"]["groups"]}


def test_deployment_profile_is_the_first_config_group(config: dict) -> None:
    first_group = config["spec"]["groups"][0]
    assert first_group["name"] == "deployment_profile"

    item = first_group["items"][0]
    assert item["name"] == "deployment_profile"
    assert item["type"] == "select_one"
    # Existing installs have no stored value, so the default keeps them on the
    # full platform. A `value:` would instead pin every install on first render.
    assert item["default"] == "enterprise"

    option_names = [option["name"] for option in item["items"]]
    assert option_names == ["enterprise", "sandbox_service"]


def test_app_and_enterprise_components_are_gated_on_the_profile(
    helm_chart_cr: dict,
) -> None:
    values = helm_chart_cr["spec"]["values"]

    # The umbrella chart's own templates (the app) are skipped for sandbox_service.
    assert values["enabled"] == ENTERPRISE_GATE

    for component in ENTERPRISE_ONLY_COMPONENTS:
        assert values[component]["enabled"] == ENTERPRISE_GATE, component


def test_runtime_api_is_always_enabled(helm_chart_cr: dict) -> None:
    # runtime-api is the whole point of the sandbox_service profile, so it is
    # never gated on the profile.
    assert helm_chart_cr["spec"]["values"]["runtime-api"]["enabled"] is True


def test_enterprise_only_groups_are_hidden_for_sandbox_service(
    groups_by_name: dict,
) -> None:
    for name in ENTERPRISE_ONLY_GROUPS:
        assert name in groups_by_name, name
        assert groups_by_name[name].get("when") == SANDBOX_HIDE, name


def test_runtime_api_groups_stay_visible_for_sandbox_service(
    groups_by_name: dict,
) -> None:
    # These configure runtime-api or shared infra, so they must not carry the
    # sandbox-hiding `when:`.
    for name in ("domain_configuration", "sandbox_configuration", "database_configuration"):
        assert groups_by_name[name].get("when") != SANDBOX_HIDE, name


def test_ca_bundle_renders_without_the_app(helm_chart_cr: dict) -> None:
    # runtime-api mounts the trust-manager Bundle's ConfigMap even when the app
    # is off, so the Bundle template must not be gated on .Values.enabled.
    guard = CA_BUNDLE_TEMPLATE.read_text(encoding="utf-8").splitlines()
    condition = next(line for line in guard if line.strip().startswith("{{- if"))
    assert ".Values.caBundle.enabled" in condition
    assert ".Values.enabled" not in condition

    # And the installer keeps the Bundle enabled regardless of profile.
    assert helm_chart_cr["spec"]["values"]["caBundle"]["enabled"] is True
