"""Tests for Keycloak realm chart invariants."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REALM_TEMPLATE = (
    REPO_ROOT
    / "charts"
    / "openhands"
    / "files"
    / "allhands-realm-github-provider.json.tmpl"
)
KEYCLOAK_CONFIG_SCRIPT = (
    REPO_ROOT / "charts" / "openhands" / "templates" / "keycloak-config-script.yaml"
)
OPENHANDS_CHART = REPO_ROOT / "charts" / "openhands"
OPENHANDS_VALUES = OPENHANDS_CHART / "values.yaml"
OPENHANDS_VALUES_SCHEMA = OPENHANDS_CHART / "values.schema.json"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"
REPLICATED_CONFIG = REPO_ROOT / "replicated" / "config.yaml"
OPENHANDS_README = OPENHANDS_CHART / "README.md"


def pkce_enabled_providers_missing_method(realm: dict) -> list[str]:
    missing_pkce_method = []
    for provider in realm.get("identityProviders", []):
        config = provider.get("config") or {}
        if config.get("pkceEnabled") == "true" and not config.get("pkceMethod"):
            missing_pkce_method.append(provider.get("alias", "<unknown>"))
    return missing_pkce_method


def keycloak_api_call_body(script_template: str) -> str:
    match = re.search(
        r"(?ms)^(?P<indent>[ \t]*)keycloak_api_call\(\) \{\n"
        r"(?P<body>.*?)^(?P=indent)\}",
        script_template,
    )
    assert match, "Could not find keycloak_api_call() in keycloak-config-script.yaml"
    return match.group("body")


def assert_pkce_enabled_providers_set_method(realm: dict) -> None:
    missing_pkce_method = pkce_enabled_providers_missing_method(realm)
    assert not missing_pkce_method, (
        "Identity providers with pkceEnabled=true must set pkceMethod: "
        + ", ".join(missing_pkce_method)
    )


def assert_keycloak_api_call_detects_error_message(script_template: str) -> None:
    body = keycloak_api_call_body(script_template)
    assert "errorMessage" in body, (
        "keycloak_api_call() must treat Keycloak errorMessage responses as errors"
    )


def test_realm_template_is_valid_json() -> None:
    json.loads(REALM_TEMPLATE.read_text(encoding="utf-8"))


def test_pkce_enabled_identity_providers_set_pkce_method() -> None:
    realm = json.loads(REALM_TEMPLATE.read_text(encoding="utf-8"))
    assert_pkce_enabled_providers_set_method(realm)


def test_pkce_guard_catches_missing_method() -> None:
    realm = {
        "identityProviders": [
            {
                "alias": "azure_devops",
                "config": {"pkceEnabled": "true"},
            },
            {
                "alias": "github",
                "config": {"pkceEnabled": "false"},
            },
        ],
    }

    with pytest.raises(AssertionError, match="azure_devops"):
        assert_pkce_enabled_providers_set_method(realm)


def test_keycloak_api_call_checks_error_message_responses() -> None:
    script_template = KEYCLOAK_CONFIG_SCRIPT.read_text(encoding="utf-8")
    assert_keycloak_api_call_detects_error_message(script_template)


def test_keycloak_api_call_extraction_is_not_tied_to_yaml_indent() -> None:
    script_template = """\
  keycloak_api_call() {
    ERROR=$(echo "$RESPONSE" | jq -r '.errorMessage')
  }
"""

    assert "errorMessage" in keycloak_api_call_body(script_template)


def test_keycloak_api_call_propagates_transport_failure() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not available")

    script_template = KEYCLOAK_CONFIG_SCRIPT.read_text(encoding="utf-8")
    helper = keycloak_api_call_body(script_template)
    result = subprocess.run(
        [
            "sh",
            "-c",
            f"keycloak_api_call() {{\n{helper}}}\nkeycloak_api_call false",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0


def sso_session_jq_filter(script_template: str) -> str:
    match = re.search(
        r"jq --argjson idle \"\$SSO_SESSION_IDLE_TIMEOUT\" "
        r"--argjson max \"\$SSO_SESSION_MAX_LIFESPAN\" \\\n"
        r"\s*'([^']+)'",
        script_template,
    )
    assert match, (
        "Could not find the SSO session lifetime jq override in "
        "keycloak-config-script.yaml"
    )
    return match.group(1)


def test_keycloak_config_script_applies_sso_session_lifetimes() -> None:
    """The config script must apply keycloak.ssoSession* values to the realm JSON.

    The realm template itself must stay valid JSON, so the numeric session
    lifetimes are applied with jq after envsubst rather than templated in.
    """
    script_template = KEYCLOAK_CONFIG_SCRIPT.read_text(encoding="utf-8")

    assert ".Values.keycloak.ssoSessionIdleTimeout" in script_template
    assert ".Values.keycloak.ssoSessionMaxLifespan" in script_template

    jq_filter = sso_session_jq_filter(script_template)
    assert ".ssoSessionIdleTimeout = $idle" in jq_filter
    assert ".ssoSessionMaxLifespan = $max" in jq_filter


def test_sso_session_jq_filter_rewrites_realm_lifetimes() -> None:
    """Run the script's actual jq filter against the realm template."""
    if shutil.which("jq") is None:
        pytest.skip("jq not available")

    script_template = KEYCLOAK_CONFIG_SCRIPT.read_text(encoding="utf-8")
    jq_filter = sso_session_jq_filter(script_template)

    result = subprocess.run(
        [
            "jq",
            "--argjson",
            "idle",
            "28800",
            "--argjson",
            "max",
            "2592000",
            jq_filter,
            str(REALM_TEMPLATE),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    realm = json.loads(result.stdout)
    assert realm["ssoSessionIdleTimeout"] == 28800
    assert realm["ssoSessionMaxLifespan"] == 2592000


def test_keycloak_error_guard_catches_missing_error_message() -> None:
    script_template = """\
    keycloak_api_call() {
      COMMAND=$1
      export RESPONSE=$(eval $COMMAND)
      ERROR=$(echo "$RESPONSE" | jq -r '.error')
      if [ -n "$ERROR" ] && [ "null" != "$ERROR" ]; then
        exit 1
      fi
    }
"""

    with pytest.raises(AssertionError, match="errorMessage"):
        assert_keycloak_api_call_detects_error_message(script_template)


def test_realm_template_uses_laminar_web_host_variable() -> None:
    """The realm template must use $LAMINAR_WEB_HOST instead of hardcoded laminar URLs.

    This allows customers with custom Laminar domains to configure the redirect
    URLs correctly without being reset on pod restarts.
    """
    realm_template = REALM_TEMPLATE.read_text(encoding="utf-8")
    assert "$LAMINAR_WEB_HOST" in realm_template, (
        "Realm template must use $LAMINAR_WEB_HOST variable for laminar redirect URLs"
    )
    # Ensure the hardcoded laminar.$WEB_HOST pattern is not present
    assert "laminar.$WEB_HOST" not in realm_template, (
        "Realm template must not use hardcoded 'laminar.$WEB_HOST' pattern"
    )


def test_keycloak_config_script_includes_laminar_web_host_in_envsubst() -> None:
    """The keycloak config script must include LAMINAR_WEB_HOST in envsubst."""
    script_template = KEYCLOAK_CONFIG_SCRIPT.read_text(encoding="utf-8")
    assert "$LAMINAR_WEB_HOST" in script_template, (
        "keycloak-config-script.yaml must include $LAMINAR_WEB_HOST in envsubst"
    )


def test_keycloak_identity_provider_socket_timeout() -> None:
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(OPENHANDS_CHART),
            "--set",
            "enabled=true",
            "--set",
            "keycloak.enabled=true",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert re.search(
        r"name: KC_SPI_CONNECTIONS_HTTP_CLIENT__DEFAULT__SOCKET_TIMEOUT_MILLIS"
        r"\s+value: [\"']15000[\"']",
        result.stdout,
    )


def test_replicated_keycloak_identity_provider_socket_timeout() -> None:
    replicated_values = REPLICATED_OPENHANDS.read_text(encoding="utf-8")

    assert re.search(
        r"name: KC_SPI_CONNECTIONS_HTTP_CLIENT__DEFAULT__SOCKET_TIMEOUT_MILLIS"
        r"\s+value: [\"']15000[\"']",
        replicated_values,
    )


def enterprise_sso_idp_jq_filter(script_template: str) -> str:
    match = re.search(
        r'echo "\$IMPORT_RESPONSE" \| jq \\\n'
        r'\s*--arg display "\$ENTERPRISE_SSO_DISPLAY_NAME" \\\n'
        r"\s*'(?P<filter>\{.*?\})' > /tmp/idp-enterprise-sso\.json",
        script_template,
        re.DOTALL,
    )
    assert match, "Could not find the enterprise SSO identity provider jq filter"
    return match.group("filter")


def enterprise_sso_import_guard(script_template: str) -> str:
    match = re.search(
        r'if ! echo "\$IMPORT_RESPONSE" \| jq -e \\\n'
        r"\s*'(?P<filter>[^']+)' \\\n",
        script_template,
    )
    assert match, "Could not find the enterprise SSO import response guard"
    return match.group("filter")


def test_enterprise_sso_uses_one_canonical_values_key() -> None:
    values = OPENHANDS_VALUES.read_text(encoding="utf-8")
    schema = json.loads(OPENHANDS_VALUES_SCHEMA.read_text(encoding="utf-8"))
    replicated = REPLICATED_OPENHANDS.read_text(encoding="utf-8")

    assert "enterpriseSso:" not in values
    assert re.search(r"^enterpriseSSO:$", values, re.MULTILINE)
    assert "enterpriseSso" not in schema["properties"]
    assert set(schema["properties"]["enterpriseSSO"]["properties"]) == {
        "enabled",
        "displayName",
        "idpMetadataUrl",
    }
    assert "enterpriseSso:" not in replicated
    assert "    enterpriseSSO:" in replicated


def test_enterprise_sso_metadata_url_requires_https() -> None:
    schema = json.loads(OPENHANDS_VALUES_SCHEMA.read_text(encoding="utf-8"))
    metadata_schema = schema["properties"]["enterpriseSSO"]["properties"][
        "idpMetadataUrl"
    ]
    assert metadata_schema["pattern"] == r"^$|^https://[^\s]+$"


def test_enterprise_sso_uses_keycloak_import_contract() -> None:
    script = KEYCLOAK_CONFIG_SCRIPT.read_text(encoding="utf-8")

    assert re.search(
        r'identity-provider/import-config".*?Content-Type: application/json',
        script,
        re.DOTALL,
    )
    assert "--data-urlencode" not in script
    assert 'providerId: "saml"' in script
    assert "fromUrl: $from_url" in script
    assert 'has("idpEntityId")' in script
    assert 'has("singleSignOnServiceUrl")' in script
    assert 'has("signingCertificate")' in script
    assert '"validateSignature": "true"' in script
    assert 'syncMode: "FORCE"' in script


def test_enterprise_sso_import_guard_requires_signing_certificate() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not available")

    script = KEYCLOAK_CONFIG_SCRIPT.read_text(encoding="utf-8")
    jq_filter = enterprise_sso_import_guard(script)
    imported_config = {
        "idpEntityId": "https://idp.example.com/entity",
        "singleSignOnServiceUrl": "https://idp.example.com/sso",
    }

    missing_certificate = subprocess.run(
        ["jq", "-e", jq_filter],
        input=json.dumps(imported_config),
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing_certificate.returncode == 1

    imported_config["signingCertificate"] = "certificate-data"
    with_certificate = subprocess.run(
        ["jq", "-e", jq_filter],
        input=json.dumps(imported_config),
        capture_output=True,
        text=True,
        check=False,
    )
    assert with_certificate.returncode == 0


def test_enterprise_sso_jq_filter_builds_keycloak_idp() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not available")

    script = KEYCLOAK_CONFIG_SCRIPT.read_text(encoding="utf-8")
    jq_filter = enterprise_sso_idp_jq_filter(script)
    imported_config = {
        "idpEntityId": "https://idp.example.com/entity",
        "singleSignOnServiceUrl": "https://idp.example.com/sso",
        "signingCertificate": "certificate-data",
    }
    display_name = 'Company "Platform" $(touch /tmp/should-not-run)'

    result = subprocess.run(
        ["jq", "--arg", "display", display_name, jq_filter],
        input=json.dumps(imported_config),
        capture_output=True,
        text=True,
        check=True,
    )
    identity_provider = json.loads(result.stdout)

    assert identity_provider["alias"] == "enterprise_sso"
    assert identity_provider["displayName"] == display_name
    assert identity_provider["config"]["idpEntityId"] == imported_config["idpEntityId"]
    assert identity_provider["config"]["validateSignature"] == "true"
    assert identity_provider["config"]["syncMode"] == "IMPORT"


def test_enterprise_sso_inputs_are_environment_data() -> None:
    deployment = (OPENHANDS_CHART / "templates" / "deployment.yaml").read_text(
        encoding="utf-8"
    )
    script = KEYCLOAK_CONFIG_SCRIPT.read_text(encoding="utf-8")
    replicated = REPLICATED_OPENHANDS.read_text(encoding="utf-8")

    assert "- name: ENTERPRISE_SSO_DISPLAY_NAME" in deployment
    assert (
        'value: {{ .Values.enterpriseSSO.displayName | default "Company SSO" | quote }}'
        in deployment
    )
    assert "- name: ENTERPRISE_SSO_IDP_METADATA_URL" in deployment
    assert "value: {{ .Values.enterpriseSSO.idpMetadataUrl | quote }}" in deployment
    assert "{{ .Values.enterpriseSSO.displayName" not in script
    assert "{{ .Values.enterpriseSSO.idpMetadataUrl" not in script
    assert '--arg display "$ENTERPRISE_SSO_DISPLAY_NAME"' in script
    assert '--arg from_url "$ENTERPRISE_SSO_IDP_METADATA_URL"' in script
    assert (
        'displayName: repl{{ ConfigOption "enterprise_sso_display_name" | mustToJson }}'
        in replicated
    )
    assert (
        'idpMetadataUrl: repl{{ ConfigOption "enterprise_sso_idp_metadata_url" | mustToJson }}'
        in replicated
    )


def test_enterprise_sso_env_overrides_render_once_for_keycloak_init() -> None:
    display_name = "Operator SSO"
    metadata_url = "https://operator.example.com/saml/metadata"
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(OPENHANDS_CHART),
            "--show-only",
            "templates/deployment.yaml",
            "--set",
            "enabled=true",
            "--set",
            "keycloak.enabled=true",
            "--set",
            "databaseMigrations.waitForDatabase=false",
            "--set",
            "databaseMigrations.createDatabases=false",
            "--set",
            "databaseMigrations.migrate=false",
            "--set",
            "enterpriseSSO.enabled=true",
            "--set-string",
            "enterpriseSSO.idpMetadataUrl=https://idp.example.com/saml/metadata",
            "--set-string",
            f"env.ENTERPRISE_SSO_DISPLAY_NAME={display_name}",
            "--set-string",
            f"env.ENTERPRISE_SSO_IDP_METADATA_URL={metadata_url}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    keycloak_init = re.search(
        r"(?ms)^      - name: keycloak-config\n(?P<body>.*?)(?=^      - name: litellm-config\n)",
        result.stdout,
    )
    assert keycloak_init, "Could not find the rendered keycloak-config init container"
    rendered = keycloak_init.group("body")

    assert rendered.count("name: ENTERPRISE_SSO_DISPLAY_NAME") == 1
    assert re.search(
        rf"name: ENTERPRISE_SSO_DISPLAY_NAME\s+value: {re.escape(display_name)}",
        rendered,
    )
    assert rendered.count("name: ENTERPRISE_SSO_IDP_METADATA_URL") == 1
    assert re.search(
        rf"name: ENTERPRISE_SSO_IDP_METADATA_URL\s+value: {re.escape(metadata_url)}",
        rendered,
    )


def test_enterprise_sso_disable_path_reconciles_managed_provider() -> None:
    script = KEYCLOAK_CONFIG_SCRIPT.read_text(encoding="utf-8")

    assert "else if .Values.enterpriseSSO.idpMetadataUrl" in script
    assert "jq '.enabled = false'" in script
    assert "Disabled managed identity provider: enterprise_sso" in script


def test_manual_enterprise_sso_guidance_documents_required_mapper() -> None:
    required_mapper_settings = (
        "identity-provider",
        "hardcoded-attribute-idp-mapper",
        "identity_provider",
        "enterprise_sso:saml",
        "FORCE",
    )

    for guidance_path in (OPENHANDS_README, REPLICATED_CONFIG):
        guidance = guidance_path.read_text(encoding="utf-8")
        for setting in required_mapper_settings:
            assert setting in guidance, f"{guidance_path} must document {setting}"
