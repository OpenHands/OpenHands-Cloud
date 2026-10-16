"""Opt-in real-Keycloak startup/restart checks using only disposable local data.

OHE_KEYCLOAK_TEST_URL=http://127.0.0.1:18087 enables this test. The listener
must be a disposable Keycloak26.3.0 dev container with the documented test-only
bootstrap user. Set OHE_KEYCLOAK_TEST_CONTAINER to an ohe-sso-policy-* container
to include a real restart. Never point this fixture at a deployed installation.
"""

import http.cookiejar
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = os.getenv("OHE_KEYCLOAK_TEST_URL", "")
pytestmark = pytest.mark.skipif(not BASE, reason="requires disposable local Keycloak")
PASSWORD = "local-disposable-test-only"


def admin(path, body=None, method=None):
    token_data = urllib.parse.urlencode({
        "client_id": "admin-cli", "username": "test-admin",
        "password": PASSWORD, "grant_type": "password",
    }).encode()
    token = json.load(urllib.request.urlopen(
        BASE + "/realms/master/protocol/openid-connect/token", data=token_data,
        timeout=10,
    ))["access_token"]
    req = urllib.request.Request(
        BASE + "/admin/realms" + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


@pytest.fixture
def realm(tmp_path):
    assert urllib.parse.urlsplit(BASE).hostname in {"127.0.0.1", "localhost"}
    assert urllib.parse.urlsplit(BASE).scheme == "http", "local dev fixture only"
    realm_name = "openhands-sso-policy-" + uuid.uuid4().hex[:10]
    idp = realm_name + "-idp"
    # The real chart bootstrap can use admin or tmpadmin. Make a disposable
    # tmpadmin only if neither exists in this dedicated test container.
    if not admin("/master/users?username=admin&exact=true"):
        users = admin("/master/users?username=tmpadmin&exact=true")
        if not users:
            admin("/master/users", {"username": "tmpadmin", "enabled": True,
                  "credentials": [{"type": "password", "value": PASSWORD, "temporary": False}]})
            users = admin("/master/users?username=tmpadmin&exact=true")
        admin("/master/users/" + users[0]["id"] + "/role-mappings/realm",
              [admin("/master/roles/admin")])
    admin("", {"realm": idp, "enabled": True, "sslRequired": "none"})
    yield realm_name, idp, tmp_path
    for name in (realm_name, idp):
        try:
            admin("/" + name, method="DELETE")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise


def startup(realm, enforce, expect_success=True):
    yaml = pytest.importorskip("yaml")
    name, idp, tmp = realm
    rendered = subprocess.check_output([
        "helm", "template", "sso-test", str(ROOT / "charts/openhands"),
        "--set", "keycloak.enabled=true", "--set", "enterpriseSSO.enabled=true",
        "--set", f"enterpriseSSO.enforceOnly={str(enforce).lower()}",
        "--set", "enterpriseSSO.idpMetadataUrl=https://idp.example.test/metadata",
    ], text=True)
    maps = {doc["metadata"]["name"]: doc["data"] for doc in yaml.safe_load_all(rendered)
            if doc and doc.get("kind") == "ConfigMap"}
    raw_template = maps["keycloak-realm-template"]["allhands-realm-github-provider.json.tmpl"]
    # Keycloak IDs are globally unique, including IDs referenced elsewhere in
    # an export. Isolate this realm without breaking those references.
    ids = set(re.findall(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", raw_template))
    for original in ids:
        raw_template = raw_template.replace(original, str(uuid.uuid5(uuid.NAMESPACE_URL, name + original)))
    template = json.loads(raw_template)
    # Only the synthetic fixture uses loopback HTTP. This does not validate or
    # modify production TLS policy; deployment metadata remains HTTPS-only.
    template["attributes"]["frontendUrl"] = BASE
    template["sslRequired"] = "none"
    realm_file = tmp / "realm.json"
    realm_file.write_text(json.dumps(template))
    script = maps["keycloak-config-script"]["keycloak-config.sh"]
    script = script.replace("/realm-template/allhands-realm-github-provider.json.tmpl", str(realm_file))
    script = script.replace("/tmp/", str(tmp) + "/")
    env = dict(os.environ,
        KEYCLOAK_SERVER_URL=BASE, KEYCLOAK_REALM_NAME=name, KEYCLOAK_CLIENT_ID="allhands",
        KEYCLOAK_CLIENT_SECRET="fixture-client-secret", KEYCLOAK_ADMIN_PASSWORD=PASSWORD,
        KEYCLOAK_ADMIN_CLIENT_ID="", KEYCLOAK_ADMIN_CLIENT_SECRET="", KEYCLOAK_ADMIN_UI_PASSWORD="",
        WEB_HOST="app.example.test", AUTH_WEB_HOST="auth.example.test",
        LAMINAR_WEB_HOST="analytics.example.test", GITHUB_BASE_URL="https://github.com",
        ENTERPRISE_SSO_IDP_METADATA_URL=f"http://localhost:8080/realms/{idp}/protocol/saml/descriptor",
        ENTERPRISE_SSO_DISPLAY_NAME="Corporate fixture",
    )
    run = subprocess.run(["sh"], input=script, env=env, text=True, capture_output=True, timeout=90)
    if expect_success:
        assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    else:
        assert run.returncode != 0, "unsafe policy reconciliation unexpectedly succeeded"


def client(realm):
    name = realm[0]
    summary = admin(f"/{name}/clients?clientId=allhands")[0]
    return admin(f"/{name}/clients/{summary['id']}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def route(realm, hint):
    # Keycloak marks loopback cookies Secure (as browsers permit on localhost).
    # This isolated HTTP fixture accepts them only on the asserted local host.
    jar = http.cookiejar.CookieJar(policy=http.cookiejar.DefaultCookiePolicy(
        secure_protocols=("http", "https")))
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)
    params = {"client_id": "allhands", "response_type": "code", "scope": "openid email profile",
              "redirect_uri": "https://app.example.test/oauth/keycloak/callback", "state": "fixture-state"}
    if hint is not None:
        params["kc_idp_hint"] = hint
    url = BASE + f"/realms/{realm[0]}/protocol/openid-connect/auth?" + urllib.parse.urlencode(params)
    for _ in range(5):
        assert url.startswith(BASE + "/"), "never follow an external fixture redirect"
        try:
            response = opener.open(url, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        location = response.headers.get("Location")
        if not location:
            return response.status, response.read().decode()
        url = urllib.parse.urljoin(url, location)
        if not url.startswith(BASE + "/"):
            return response.status, location
    raise AssertionError("too many fixture redirects")


def assert_enforced(realm):
    current = client(realm)
    assert current["enabled"] is True
    assert current["directAccessGrantsEnabled"] is False
    assert current["authenticationFlowBindingOverrides"]["browser"]
    providers = admin(f"/{realm[0]}/identity-provider/instances")
    assert all(p["linkOnly"] for p in providers if p["alias"] != "enterprise_sso")
    flows = admin(f"/{realm[0]}/authentication/flows")
    assert len([f for f in flows if f["alias"] == "openhands-enterprise-sso-only"]) == 1
    executions = admin(f"/{realm[0]}/authentication/flows/openhands-enterprise-sso-only/executions")
    assert len(executions) == 1
    assert executions[0]["providerId"] == "identity-provider-redirector"
    assert executions[0]["requirement"] == "REQUIRED"
    for hint in (None, "enterprise_sso"):
        status, html = route(realm, hint)
        assert status == 200 and "SAMLRequest" in html
        assert 'type="password"' not in html
    for hint in ("github", "azure_devops", ""):
        status, html = route(realm, hint)
        assert status >= 400 and 'type="password"' not in html
    data = urllib.parse.urlencode({"client_id": "allhands", "client_secret": "fixture-client-secret",
        "grant_type": "password", "username": "unused", "password": "unused"}).encode()
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(BASE + f"/realms/{realm[0]}/protocol/openid-connect/token", data=data)
    assert json.load(error.value)["error"] == "unauthorized_client"


def test_managed_policy_survives_reconciliation_restart_and_rollback(realm):
    startup(realm, True)
    assert_enforced(realm)
    flow = client(realm)["authenticationFlowBindingOverrides"]["browser"]
    startup(realm, True)
    assert client(realm)["authenticationFlowBindingOverrides"]["browser"] == flow
    assert_enforced(realm)
    container = os.getenv("OHE_KEYCLOAK_TEST_CONTAINER")
    if container:
        assert container.startswith("ohe-sso-policy-"), "disposable test container names only"
        subprocess.run(["docker", "restart", "--timeout", "10", container], check=True, capture_output=True)
        for _ in range(60):
            try:
                urllib.request.urlopen(BASE + "/realms/master", timeout=2)
                break
            except (OSError, urllib.error.URLError):
                time.sleep(1)
        else:
            pytest.fail("local Keycloak did not recover after restart")
        startup(realm, True)
        assert client(realm)["authenticationFlowBindingOverrides"]["browser"] == flow
        assert_enforced(realm)
    startup(realm, False)
    assert client(realm)["directAccessGrantsEnabled"] is True
    assert client(realm)["authenticationFlowBindingOverrides"] == {}
    assert all(not p["linkOnly"] for p in admin(f"/{realm[0]}/identity-provider/instances")
               if p["alias"] != "enterprise_sso")
    _, html = route(realm, None)
    assert 'type="password"' in html


@pytest.mark.parametrize("conflict", ["custom-broker", "flow-ownership"])
def test_policy_refuses_unsafe_custom_configuration(realm, conflict):
    startup(realm, True)
    name = realm[0]
    if conflict == "custom-broker":
        admin(f"/{name}/identity-provider/instances", {
            "alias": "customer-github", "providerId": "github", "enabled": True,
            "linkOnly": False, "config": {"clientId": "fixture", "clientSecret": "fixture"},
        })
    else:
        flows = admin(f"/{name}/authentication/flows")
        flow = next(f for f in flows if f["alias"] == "openhands-enterprise-sso-only")
        flow["description"] = "Customer managed"
        admin(f"/{name}/authentication/flows/{flow['id']}", flow, method="PUT")
    startup(realm, True, expect_success=False)
