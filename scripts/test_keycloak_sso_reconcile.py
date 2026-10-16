"""Execute the chart's SSO shell against a local, stateful Keycloak API double.

Unlike filter-only tests, these exercise the shell's transport of JSON containing
escaped certificate newlines. No real credentials or external services are used.
"""

import json
import os
import re
import shutil
import subprocess
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / (
    "charts/openhands/templates/keycloak-config-script.yaml"
)
MARKER = "{{- if and .Values.enterpriseSSO.enabled .Values.enterpriseSSO.idpMetadataUrl }}"
DISABLE_MARKER = "{{- else if not .Values.enterpriseSSO.enabled }}"


def reconciliation_script(tmp_path, enabled=True):
    template = TEMPLATE.read_text()
    helpers = []
    for name in ("keycloak_api_call", "keycloak_get_optional", "keycloak_write"):
        match = re.search(
            rf"(?ms)^(?P<indent> +){name}\(\) \{{\n.*?^(?P=indent)\}}",
            template,
        )
        assert match, name
        helpers.append(textwrap.dedent(match.group()))
    sso = template.split(MARKER, 1)[1]
    if enabled:
        sso = sso.split(DISABLE_MARKER, 1)[0]
    else:
        sso = sso.split(DISABLE_MARKER, 1)[1].split("{{- end }}", 1)[0]
    script = (
        "set -eu\n"
        "refresh_access_token() { ACCESS_TOKEN=synthetic-test-token; }\n"
        + "\n".join(helpers)
        + "\n"
        + textwrap.dedent(sso)
    )
    # The production script uses fixed /tmp files. Keep parallel tests isolated.
    return script.replace("/tmp/", str(tmp_path) + "/")


@pytest.fixture
def api():
    state = {
        "import": {
            "idpEntityId": "https://idp.example.test/entity",
            "singleSignOnServiceUrl": "https://idp.example.test/sso",
            "signingCertificate": "MIIBsynthetic\ncertificate\nlines",
        },
        "provider": None,
        "mappers": [],
        "writes": [],
        "import_status": 200,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, status, value):
            body = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.endswith("/mappers"):
                self.respond(200, state["mappers"])
            else:
                self.respond(200 if state["provider"] else 404, state["provider"])

        def write(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/import-config"):
                assert body == {
                    "providerId": "saml",
                    "fromUrl": "https://idp.example.test/metadata",
                }
                self.respond(state["import_status"], state["import"])
                return
            state["writes"].append((self.command, self.path, body))
            if "/mappers" in self.path:
                if self.command == "POST":
                    state["mappers"].append(dict(body, id=f"mapper-{len(state['mappers'])}"))
                else:
                    mapper_id = self.path.rsplit("/", 1)[-1]
                    existing = next(m for m in state["mappers"] if m["id"] == mapper_id)
                    assert body["id"] == mapper_id
                    existing.clear()
                    existing.update(body)
            else:
                state["provider"] = dict(body, internalId="provider-id")
            self.respond(201 if self.command == "POST" else 204, b"")

        do_POST = write
        do_PUT = write

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def reconcile(tmp_path, api, enabled=True):
    for executable in ("jq", "curl", "sh"):
        if not shutil.which(executable):
            pytest.skip(f"{executable} is required for the shell regression")
    url, _state = api
    env = dict(
        os.environ,
        KEYCLOAK_SERVER_URL=url,
        KEYCLOAK_REALM_NAME="allhands",
        ENTERPRISE_SSO_IDP_METADATA_URL="https://idp.example.test/metadata",
        ENTERPRISE_SSO_DISPLAY_NAME='Company "SSO" \\ test',
    )
    # dash is used on the affected deployment. POSIX/XPG echo on macOS also
    # interprets escapes, unlike bash's default echo behavior.
    shell = shutil.which("dash") or "sh"
    result = subprocess.run(
        [shell], input=reconciliation_script(tmp_path, enabled), env=env,
        text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_create_preserves_multiline_certificate(tmp_path, api, newline):
    _url, state = api
    certificate = newline.join(["MIIBsynthetic", "certificate", "lines"])
    state["import"]["signingCertificate"] = certificate
    result = reconcile(tmp_path, api)
    assert "auto-configuration failed" not in result.stderr
    provider = state["provider"]
    assert provider is not None, result.stdout + result.stderr
    assert provider["config"]["signingCertificate"] == certificate
    assert provider["config"]["validateSignature"] == "true"
    assert provider["displayName"] == 'Company "SSO" \\ test'
    mappers = {m["name"]: m for m in state["mappers"]}
    assert mappers["identity-provider"]["config"]["attribute.value"] == "enterprise_sso:saml"
    for attribute in ("email", "firstName", "lastName"):
        mapper = mappers[f"openhands-profile-{attribute}"]
        assert mapper["identityProviderMapper"] == "saml-user-attribute-idp-mapper"
        assert mapper["identityProviderAlias"] == "enterprise_sso"
        assert mapper["config"] == {
            "attribute.name": attribute,
            "attribute.name.format": "ATTRIBUTE_FORMAT_UNSPECIFIED",
            "user.attribute": attribute,
            "syncMode": "IMPORT",
        }
    assert [write[0] for write in state["writes"]] == ["POST"] * 5


def test_repeat_updates_provider_and_mapper_without_duplicates(tmp_path, api):
    _url, state = api
    reconcile(tmp_path, api)
    assert state["provider"] is not None
    # Existing API responses can also contain escaped data; do not limit the
    # fix to the import response and leave subsequent reconciliations broken.
    state["mappers"][0]["config"]["other"] = "line\nwith\\backslash"
    state["import"]["signingCertificate"] = "changed\ncertificate"
    state["writes"].clear()
    result = reconcile(tmp_path, api)
    assert "auto-configuration failed" not in result.stderr
    assert [write[0] for write in state["writes"]] == ["PUT"] * 5
    assert state["provider"]["config"]["signingCertificate"] == "changed\ncertificate"
    assert len(state["mappers"]) == 4


def test_profile_reconciliation_preserves_unrelated_mapper(tmp_path, api):
    _url, state = api
    custom = {"id": "custom-id", "name": "customer-managed",
              "identityProviderMapper": "hardcoded-attribute-idp-mapper",
              "config": {"attribute": "custom", "attribute.value": "keep"}}
    state["mappers"] = [custom.copy()]
    reconcile(tmp_path, api)
    reconcile(tmp_path, api)
    assert state["mappers"][0] == custom
    assert len(state["mappers"]) == 5


@pytest.mark.parametrize("managed", [True, False])
def test_disable_only_managed_provider_preserving_certificate(tmp_path, api, managed):
    _url, state = api
    certificate = state["import"]["signingCertificate"]
    state["provider"] = {
        "internalId": "provider-id", "enabled": True,
        "config": {"signingCertificate": certificate,
                   "openhandsManaged": str(managed).lower()},
    }
    result = reconcile(tmp_path, api, enabled=False)
    assert "could not disable" not in result.stderr
    assert state["provider"]["enabled"] is (not managed)
    assert state["provider"]["config"]["signingCertificate"] == certificate
    assert len(state["writes"]) == int(managed)


@pytest.mark.parametrize("bad_import", [
    {"idpEntityId": "https://idp.example.test"},
    b'{"signingCertificate":',
    {"error": "metadata unavailable"},
])
def test_bad_import_warns_without_creating_provider(tmp_path, api, bad_import):
    _url, state = api
    state["import"] = bad_import
    result = reconcile(tmp_path, api)
    assert "auto-configuration failed" in result.stderr
    assert state["provider"] is None
    assert state["writes"] == []
