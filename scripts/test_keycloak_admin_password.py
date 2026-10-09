"""Opt-in real Keycloak tests: KEYCLOAK_TEST_IMAGE=quay.io/keycloak/keycloak:26.3.3."""

import json
import os
import subprocess
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_PASSWORD = "local-bootstrap-test-password"


def request(url, data=None, token=None, method=None, json_body=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def login(url, password):
    return request(
        url + "/realms/master/protocol/openid-connect/token",
        {
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": "admin",
            "password": password,
        },
    )


@pytest.fixture
def keycloak():
    image = os.environ.get("KEYCLOAK_TEST_IMAGE")
    if not image:
        pytest.skip("Set KEYCLOAK_TEST_IMAGE to run Docker integration tests")
    container = subprocess.check_output(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "-p",
            "127.0.0.1::8080",
            "-e",
            "KC_BOOTSTRAP_ADMIN_USERNAME=tmpadmin",
            "-e",
            f"KC_BOOTSTRAP_ADMIN_PASSWORD={BOOTSTRAP_PASSWORD}",
            image,
            "start-dev",
        ],
        text=True,
    ).strip()
    try:
        port = subprocess.check_output(
            ["docker", "port", container, "8080"], text=True
        ).strip()
        url = "http://" + port
        for _ in range(120):
            try:
                if request(url + "/realms/master")[0] == 200:
                    break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass
            time.sleep(1)
        else:
            pytest.fail("Local Keycloak did not become ready")
        yield url
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container], check=True, capture_output=True
        )


def reconcile(url, password="", success=True, client_id="", client_secret=""):
    template = (
        ROOT / "charts/openhands/templates/keycloak-config-script.yaml"
    ).read_text()
    script = textwrap.dedent(template.split("keycloak-config.sh: |\n", 1)[1])
    script = script.split("ERROR_MESSAGE=$(curl", 1)[0]
    script += "\nrefresh_access_token\n"
    env = dict(
        os.environ,
        KEYCLOAK_SERVER_URL=url,
        KEYCLOAK_ADMIN_PASSWORD=BOOTSTRAP_PASSWORD,
        KEYCLOAK_ADMIN_UI_PASSWORD=password,
        KEYCLOAK_ADMIN_CLIENT_ID=client_id,
        KEYCLOAK_ADMIN_CLIENT_SECRET=client_secret,
    )
    result = subprocess.run(
        ["sh"], input=script, env=env, text=True, capture_output=True, check=False
    )
    if not success:
        assert result.returncode != 0
        return result
    assert result.returncode == 0, result.stdout + result.stderr
    if password:
        assert password not in result.stdout + result.stderr
    assert BOOTSTRAP_PASSWORD not in result.stdout + result.stderr


def test_existing_install_password_changes_and_restart(keycloak):
    reconcile(keycloak)
    status, auth = login(keycloak, BOOTSTRAP_PASSWORD)
    assert status == 200
    _, users_before = request(
        keycloak + "/admin/realms/master/users?username=admin&exact=true",
        token=auth["access_token"],
    )
    first = 'first + & = " quote \\ $ ` password'
    reconcile(keycloak, first)
    assert login(keycloak, first)[0] == 200
    assert login(keycloak, BOOTSTRAP_PASSWORD)[0] == 401
    second = "second-admin-password"
    reconcile(keycloak, second)
    assert login(keycloak, second)[0] == 200
    assert login(keycloak, first)[0] == 401
    _, auth = login(keycloak, second)
    assert (
        request(
            keycloak + "/admin/realms/master",
            token=auth["access_token"],
            method="PUT",
            json_body={"passwordPolicy": "passwordHistory(2)"},
        )[0]
        == 204
    )
    reconcile(keycloak, second)
    reconcile(keycloak)
    _, users_after = request(
        keycloak + "/admin/realms/master/users?username=admin&exact=true",
        token=auth["access_token"],
    )
    assert users_after[0]["id"] == users_before[0]["id"]
    assert login(keycloak, second)[0] == 200
    assert login(keycloak, BOOTSTRAP_PASSWORD)[0] == 401


def test_fresh_install_with_custom_password(keycloak):
    reconcile(keycloak, "fresh-admin-password")
    assert login(keycloak, "fresh-admin-password")[0] == 200
    reconcile(keycloak, "replacement-admin-password")
    assert login(keycloak, "replacement-admin-password")[0] == 200


@pytest.mark.parametrize(
    "client_id,client_secret",
    [
        ("openhands-provisioner", ""),
        ("custom provisioner & review", "separate + & secret"),
    ],
)
def test_application_service_account_exists_without_console_password(
    keycloak, client_id, client_secret
):
    reconcile(keycloak, client_id=client_id, client_secret=client_secret)
    status, auth = request(
        keycloak + "/realms/master/protocol/openid-connect/token",
        {
            "client_id": client_id,
            "grant_type": "client_credentials",
            "client_secret": client_secret or BOOTSTRAP_PASSWORD,
        },
    )
    assert status == 200
    assert (
        request(keycloak + "/admin/realms/master", token=auth["access_token"])[0] == 200
    )
    assert login(keycloak, BOOTSTRAP_PASSWORD)[0] == 200
    reconcile(keycloak, client_id=client_id, client_secret=client_secret)
    assert (
        request(keycloak + "/admin/realms/master", token=auth["access_token"])[0] == 200
    )

    if client_secret:
        reconcile(
            keycloak,
            "changed-console-password",
            client_id=client_id,
            client_secret=client_secret,
        )
        assert login(keycloak, "changed-console-password")[0] == 200
        reconcile(
            keycloak, "changed-again", client_id=client_id, client_secret=client_secret
        )
        assert login(keycloak, "changed-again")[0] == 200


@pytest.mark.parametrize("matching_secret", [True, False])
def test_partial_provisioner_setup_and_client_collision(keycloak, matching_secret):
    reconcile(keycloak)
    _, auth = login(keycloak, BOOTSTRAP_PASSWORD)
    # Replace only the fixture's client to simulate an incomplete/colliding setup.
    _, clients = request(
        keycloak + "/admin/realms/master/clients?clientId=openhands-provisioner",
        token=auth["access_token"],
    )
    for client in clients:
        assert (
            request(
                keycloak + "/admin/realms/master/clients/" + client["id"],
                token=auth["access_token"],
                method="DELETE",
            )[0]
            == 204
        )
    assert (
        request(
            keycloak + "/admin/realms/master/clients",
            token=auth["access_token"],
            method="POST",
            json_body={
                "clientId": "openhands-provisioner",
                "enabled": True,
                "secret": BOOTSTRAP_PASSWORD
                if matching_secret
                else "unrelated-client-secret",
                "serviceAccountsEnabled": True,
                "publicClient": False,
                "fullScopeAllowed": False,
            },
        )[0]
        == 201
    )
    reconcile(keycloak, "new-dashboard-password", success=matching_secret)
    if matching_secret:
        assert login(keycloak, "new-dashboard-password")[0] == 200
        reconcile(keycloak, "next-dashboard-password")
        assert login(keycloak, "next-dashboard-password")[0] == 200
    else:
        assert login(keycloak, BOOTSTRAP_PASSWORD)[0] == 200
        assert login(keycloak, "new-dashboard-password")[0] == 401


def test_console_password_is_only_passed_to_provisioning_container():
    import yaml

    rendered = subprocess.check_output(
        [
            "helm",
            "template",
            "test",
            str(ROOT / "charts/openhands"),
            "--set",
            "keycloak.enabled=true",
        ],
        text=True,
    )
    deployment = next(
        doc
        for doc in yaml.safe_load_all(rendered)
        if doc
        and doc.get("kind") == "Deployment"
        and doc["metadata"]["name"] == "openhands"
    )
    spec = deployment["spec"]["template"]["spec"]
    init = next(
        container
        for container in spec["initContainers"]
        if container["name"] == "keycloak-config"
    )
    env = next(
        item for item in init["env"] if item["name"] == "KEYCLOAK_ADMIN_UI_PASSWORD"
    )
    assert env["valueFrom"]["secretKeyRef"] == {
        "name": "keycloak-admin",
        "key": "admin-ui-password",
        "optional": True,
    }
    for container in spec["containers"]:
        assert not any(
            item["name"] == "KEYCLOAK_ADMIN_UI_PASSWORD" for item in container["env"]
        )


def test_unconfigured_install_does_not_create_or_reconcile_service_client(keycloak):
    reconcile(keycloak)
    _, auth = login(keycloak, BOOTSTRAP_PASSWORD)
    url = keycloak + "/admin/realms/master/clients"
    _, clients = request(
        url + "?clientId=openhands-provisioner", token=auth["access_token"]
    )
    assert clients == []
    assert (
        request(
            url,
            token=auth["access_token"],
            method="POST",
            json_body={
                "clientId": "openhands-provisioner",
                "enabled": True,
                "secret": "unrelated-service-secret",
                "serviceAccountsEnabled": True,
                "publicClient": False,
                "fullScopeAllowed": False,
            },
        )[0]
        == 201
    )
    reconcile(keycloak)
    assert login(keycloak, BOOTSTRAP_PASSWORD)[0] == 200
    _, clients = request(
        url + "?clientId=openhands-provisioner", token=auth["access_token"]
    )
    client_url = url + "/" + clients[0]["id"]
    _, secret = request(client_url + "/client-secret", token=auth["access_token"])
    assert secret["value"] == "unrelated-service-secret"
    _, roles = request(client_url + "/scope-mappings/realm", token=auth["access_token"])
    assert not any(role["name"] == "admin" for role in roles)
