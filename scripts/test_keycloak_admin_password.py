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


def reconcile(
    url,
    password="",
    success=True,
    client_id="",
    client_secret="",
    needs_scope=False,
    timeout=None,
):
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
        KEYCLOAK_NEEDS_ADMIN_API_SCOPE=str(needs_scope).lower(),
    )
    result = subprocess.run(
        ["sh"],
        input=script,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
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
    assert login(keycloak, BOOTSTRAP_PASSWORD)[1]["error"] == "invalid_grant"
    second = "second-admin-password"
    reconcile(keycloak, second)
    assert login(keycloak, second)[0] == 200
    assert login(keycloak, first)[1]["error"] == "invalid_grant"
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
    assert login(keycloak, BOOTSTRAP_PASSWORD)[1]["error"] == "invalid_grant"


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


def provisioner_admin_status(url):
    _, token = request(
        url + "/realms/master/protocol/openid-connect/token",
        {
            "client_id": "openhands-provisioner",
            "grant_type": "client_credentials",
            "client_secret": BOOTSTRAP_PASSWORD,
        },
    )
    return request(url + "/admin/realms/master", token=token["access_token"])[0]


CONSOLE_PASSWORD = 'console + & " password'


def provisioner_without_admin_api_scope(url, password=""):
    """Set up the provisioner without kc-admin-api, like a client created before 26.8.2."""
    reconcile(url, password, client_id="openhands-provisioner")
    _, auth = login(url, password or BOOTSTRAP_PASSWORD)
    master = url + "/admin/realms/master"
    _, scopes = request(master + "/client-scopes", token=auth["access_token"])
    scope = next((s for s in scopes if s["name"] == "kc-admin-api"), None)
    if scope is None:
        pytest.skip("This Keycloak has no kc-admin-api scope (added in 26.8.2)")
    _, clients = request(
        master + "/clients?clientId=openhands-provisioner", token=auth["access_token"]
    )
    assert (
        request(
            master
            + "/clients/"
            + clients[0]["id"]
            + "/default-client-scopes/"
            + scope["id"],
            token=auth["access_token"],
            method="DELETE",
        )[0]
        == 204
    )
    assert provisioner_admin_status(url) == 401


@pytest.mark.parametrize("password", ["", CONSOLE_PASSWORD])
def test_upgrade_adds_admin_api_scope_to_existing_provisioner(keycloak, password):
    provisioner_without_admin_api_scope(keycloak, password)
    reconcile(keycloak, password, client_id="openhands-provisioner", needs_scope=True)
    assert provisioner_admin_status(keycloak) == 200


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
        assert login(keycloak, "new-dashboard-password")[1]["error"] == "invalid_grant"


def test_console_password_is_only_passed_to_provisioning_container():
    import yaml

    rendered = subprocess.check_output(
        [
            "helm",
            "template",
            "test",
            str(ROOT / "charts/openhands"),
            "--set",
            "automation.enabled=false",
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


def has_admin_api_scope(url, password=""):
    _, auth = login(url, password or BOOTSTRAP_PASSWORD)
    _, scopes = request(
        url + "/admin/realms/master/client-scopes", token=auth["access_token"]
    )
    return any(s["name"] == "kc-admin-api" for s in scopes)


def run_scope_hook(
    url,
    target_version,
    password="",
    client_id="openhands-provisioner",
    mode="fix",
    timeout=60,
    success=True,
    client_secret="",
):
    template = (
        ROOT / "charts/openhands/templates/keycloak-admin-api-scope-job.yaml"
    ).read_text()
    script = template.split('define "openhands.keycloakAdminApiScopeScript" -}}\n', 1)[
        1
    ].split("\n{{- end }}", 1)[0]
    env = dict(
        os.environ,
        KEYCLOAK_SERVER_URL=url,
        KEYCLOAK_ADMIN_PASSWORD=BOOTSTRAP_PASSWORD,
        KEYCLOAK_ADMIN_UI_PASSWORD=password,
        KEYCLOAK_ADMIN_CLIENT_ID=client_id,
        KEYCLOAK_ADMIN_CLIENT_SECRET=client_secret,
        TARGET_VERSION=target_version,
        NEEDS_ADMIN_API_SCOPE=str(
            tuple(map(int, target_version.split("."))) >= (26, 8, 2)
        ).lower(),
        MODE=mode,
    )
    try:
        result = subprocess.run(
            ["sh"],
            input=script,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        out = error.stdout or ""
        output = "TIMEOUT\n" + (out if isinstance(out, str) else out.decode())
    else:
        output = result.stdout + result.stderr
        assert (result.returncode == 0) == success, output
    assert BOOTSTRAP_PASSWORD not in output
    if password:
        assert password not in output
    return output


@pytest.mark.parametrize(
    "password,client_id",
    [
        ("", "openhands-provisioner"),
        (CONSOLE_PASSWORD, "openhands-provisioner"),
        # A console password alone makes keycloak-config create openhands-provisioner.
        (CONSOLE_PASSWORD, ""),
    ],
)
def test_scope_hook_adds_admin_api_scope_without_app_restart(
    keycloak, password, client_id
):
    provisioner_without_admin_api_scope(keycloak, password)
    out = run_scope_hook(keycloak, "26.8.2", password, client_id)
    assert "has the kc-admin-api scope" in out
    assert provisioner_admin_status(keycloak) == 200
    assert "nothing to do" in run_scope_hook(keycloak, "26.8.2", password, client_id)


def test_scope_hook_needs_no_admin_login_when_the_provisioner_works(keycloak):
    reconcile(keycloak, CONSOLE_PASSWORD, client_id="openhands-provisioner")
    if not has_admin_api_scope(keycloak, CONSOLE_PASSWORD):
        pytest.skip("This Keycloak has no kc-admin-api scope (added in 26.8.2)")
    # The console password was cleared, so neither secret matches the admin user.
    out = run_scope_hook(keycloak, "26.8.2", password="")
    assert "has Admin REST API access; nothing to do" in out


def test_scope_hook_fails_clearly_when_no_admin_password_works(keycloak):
    provisioner_without_admin_api_scope(keycloak, CONSOLE_PASSWORD)
    out = run_scope_hook(keycloak, "26.8.2", password="", success=False)
    assert "Set the Keycloak Admin Password" in out
    assert provisioner_admin_status(keycloak) == 401


def test_scope_hook_is_a_no_op_before_26_8_2(keycloak):
    reconcile(keycloak, client_id="openhands-provisioner")
    if has_admin_api_scope(keycloak):
        pytest.skip("This Keycloak has the kc-admin-api scope")
    assert "nothing to do" in run_scope_hook(keycloak, "26.8.0")


def test_scope_hook_waits_while_keycloak_is_older_than_the_target(keycloak):
    # Without --wait, the hook can start before the new Keycloak replaces the old one.
    reconcile(keycloak, client_id="openhands-provisioner")
    if has_admin_api_scope(keycloak):
        pytest.skip("This Keycloak has the kc-admin-api scope")
    out = run_scope_hook(keycloak, "26.8.2", timeout=15)
    assert out.startswith("TIMEOUT") and "Waiting for Keycloak 26.8.2" in out


def test_scope_hook_keeps_waiting_while_keycloak_is_down():
    out = run_scope_hook("http://127.0.0.1:9", "26.8.2", timeout=10)
    assert out.startswith("TIMEOUT") and "Waiting for Keycloak 26.8.2" in out


def test_scope_hook_skips_an_install_without_a_provisioner(keycloak):
    reconcile(keycloak)
    out = run_scope_hook(keycloak, "26.8.2", client_id="")
    assert 'Client "openhands-provisioner" does not exist' in out


@pytest.mark.parametrize("password,passes", [(CONSOLE_PASSWORD, True), ("", False)])
def test_pre_upgrade_check_needs_a_working_admin_login(keycloak, password, passes):
    # Runs against the old Keycloak, before the 26.8.2 migration.
    reconcile(keycloak, CONSOLE_PASSWORD, client_id="openhands-provisioner")
    if has_admin_api_scope(keycloak, CONSOLE_PASSWORD):
        pytest.skip("Runs against a Keycloak before 26.8.2")
    out = run_scope_hook(keycloak, "26.8.2", password, mode="check", success=passes)
    if passes:
        assert "admin login works" in out
    else:
        assert "Set the Keycloak Admin Password" in out


def test_pre_upgrade_check_skips_upgrades_that_keep_keycloak_before_26_8_2(keycloak):
    reconcile(keycloak, CONSOLE_PASSWORD, client_id="openhands-provisioner")
    assert run_scope_hook(keycloak, "26.8.0", mode="check") == ""


def test_keycloak_config_waits_while_the_old_keycloak_still_serves(keycloak):
    reconcile(keycloak, client_id="openhands-provisioner")
    if has_admin_api_scope(keycloak):
        pytest.skip("Runs against a Keycloak before 26.8.2")
    with pytest.raises(subprocess.TimeoutExpired) as error:
        reconcile(
            keycloak, client_id="openhands-provisioner", needs_scope=True, timeout=15
        )
    out = error.value.stdout or ""
    out = out if isinstance(out, str) else out.decode()
    assert "Waiting for Keycloak 26.8.2 or later" in out


def test_pre_upgrade_check_applies_a_changed_console_password(keycloak):
    reconcile(keycloak, CONSOLE_PASSWORD, client_id="openhands-provisioner")
    if has_admin_api_scope(keycloak, CONSOLE_PASSWORD):
        pytest.skip("Runs against a Keycloak before 26.8.2")
    changed = "changed console password"
    out = run_scope_hook(keycloak, "26.8.2", changed, mode="check")
    assert "admin login works" in out
    assert login(keycloak, changed)[0] == 200


@pytest.mark.parametrize("password,passes", [(CONSOLE_PASSWORD, True), ("", False)])
def test_pre_upgrade_check_after_keycloak_rejects_the_provisioner(
    keycloak, password, passes
):
    provisioner_without_admin_api_scope(keycloak, CONSOLE_PASSWORD)
    out = run_scope_hook(keycloak, "26.8.2", password, mode="check", success=passes)
    assert ("admin login works" in out) == passes


def test_scope_hook_leaves_a_client_with_a_wrong_secret_alone(keycloak):
    reconcile(keycloak, client_id="openhands-provisioner")
    out = run_scope_hook(keycloak, "26.8.2", client_secret="not-the-client-secret")
    assert 'rejects the "openhands-provisioner" client secret' in out


def test_scope_hook_fails_clearly_when_the_provisioner_lacks_admin_rights(keycloak):
    reconcile(keycloak, client_id="openhands-provisioner")
    if not has_admin_api_scope(keycloak):
        pytest.skip("This Keycloak has no kc-admin-api scope (added in 26.8.2)")
    _, auth = login(keycloak, BOOTSTRAP_PASSWORD)
    master = keycloak + "/admin/realms/master"
    _, clients = request(
        master + "/clients?clientId=openhands-provisioner", token=auth["access_token"]
    )
    _, user = request(
        master + "/clients/" + clients[0]["id"] + "/service-account-user",
        token=auth["access_token"],
    )
    _, admin_role = request(master + "/roles/admin", token=auth["access_token"])
    # With the scope but without the admin role, the admin API answers 403.
    assert (
        request(
            master + "/users/" + user["id"] + "/role-mappings/realm",
            token=auth["access_token"],
            method="DELETE",
            json_body=[admin_role],
        )[0]
        == 204
    )
    out = run_scope_hook(keycloak, "26.8.2", timeout=30, success=False)
    assert "still has no Admin REST API access (provisioner: forbidden" in out
