"""Lock ownership/failure checks; actual contention is also tested on Keycloak."""

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "oidc_lock",
    Path(__file__).resolve().parents[1] / "charts/openhands/files/enterprise-oidc.py",
)
oidc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oidc)
REALM = "http://localhost/admin/realms/disposable"


class Clients:
    def __init__(self):
        self.items = {}
        self.deleted = []
        self.lose_post_response = False

    def __call__(self, url, token=None, body=None, method=None, optional=False):
        assert token == "synthetic-admin"
        if method == "POST":
            if self.items:
                raise oidc.Conflict()
            self.items[body["id"]] = copy.deepcopy(body)
            if self.lose_post_response:
                raise oidc.TemporaryFailure()
        elif "?clientId=" in url:
            return list(copy.deepcopy(self.items).values())
        else:
            owner = url.rsplit("/", 1)[-1]
            if method == "DELETE":
                self.deleted.append(owner)
                del self.items[owner]
            else:
                if owner not in self.items and not optional:
                    raise oidc.ConfigurationError("Missing lock")
                return copy.deepcopy(self.items.get(owner))


def test_lock_grants_no_access_and_is_removed():
    clients = Clients()
    with oidc.provisioning_lock(REALM, "synthetic-admin", clients):
        lock = next(iter(clients.items.values()))
        assert oidc._owned_lock(lock, lock["id"])
        assert lock["defaultClientScopes"] == lock["optionalClientScopes"] == []
    assert not clients.items and len(clients.deleted) == 1


def test_provisioning_exception_releases_own_lock():
    clients = Clients()
    with pytest.raises(ValueError, match="configuration failed"):
        with oidc.provisioning_lock(REALM, "synthetic-admin", clients):
            raise ValueError("configuration failed")
    assert not clients.items and len(clients.deleted) == 1


@pytest.mark.parametrize("child_fails", [False, True])
def test_cleanup_renews_token_after_child_outlives_original(child_fails):
    clients = Clients()
    current = [0]
    seen = []

    def renew():
        current[0] += 1
        return str(current[0])

    def api(url, token=None, **kwargs):
        assert token == str(current[0]), "stale token used"
        seen.append((kwargs.get("method"), token))
        return clients(url, token="synthetic-admin", **kwargs)

    try:
        with oidc.provisioning_lock(REALM, "expired", api, refresh_token=renew):
            current[0] += 1  # Child runs beyond the parent's token lifetime.
            if child_fails:
                raise ValueError("child failed")
    except ValueError:
        assert child_fails
    assert not clients.items
    assert seen[-1] == ("DELETE", "4")


@pytest.mark.parametrize("provisioner", [True, False])
def test_token_renewal_uses_selected_principal_without_logging(
    monkeypatch, provisioner
):
    import io
    import json
    from urllib.parse import parse_qs

    env = {
        **ENV,
        "USE_PROVISIONER": str(provisioner).lower(),
        "KEYCLOAK_ADMIN_CLIENT_ID": " fixture-admin ",
        "KEYCLOAK_ADMIN_CLIENT_SECRET": "synthetic-client-secret",
        "KEYCLOAK_ADMIN_PASSWORD": "synthetic-password",
    }

    class Opener:
        def open(self, req, timeout):
            fields = parse_qs(req.data.decode())
            assert (
                req.full_url
                == "http://localhost/realms/master/protocol/openid-connect/token"
            )
            if provisioner:
                assert fields == {
                    "client_id": ["fixture-admin"],
                    "grant_type": ["client_credentials"],
                    "client_secret": ["synthetic-client-secret"],
                }
            else:
                assert fields == {
                    "client_id": ["admin-cli"],
                    "grant_type": ["password"],
                    "username": ["admin"],
                    "password": ["synthetic-password"],
                }
            return io.BytesIO(json.dumps({"access_token": "renewed"}).encode())

    monkeypatch.setattr(oidc, "build_opener", lambda *args: Opener())
    assert oidc.refresh_admin_token(env) == env["ACCESS_TOKEN"] == "renewed"


def test_lost_post_response_adopts_only_own_uuid():
    clients = Clients()
    clients.lose_post_response = True
    with oidc.provisioning_lock(REALM, "synthetic-admin", clients):
        assert len(clients.items) == 1
    assert not clients.items


def test_waits_for_owner_without_deleting_or_stealing(monkeypatch):
    clients = Clients()
    with oidc.provisioning_lock(REALM, "synthetic-admin", clients):
        held = copy.deepcopy(clients.items)
        monkeypatch.setattr(oidc, "LOCK_WAIT_SECONDS", 0)
        with pytest.raises(oidc.ConfigurationError, match="is locked"):
            with oidc.provisioning_lock(REALM, "synthetic-admin", clients):
                pytest.fail("Second writer entered")
        assert clients.items == held and not clients.deleted
    assert not clients.items


def test_contender_retries_after_normal_release(monkeypatch):
    clients = Clients()
    # Simulate another process finishing while the contender is waiting.
    with oidc.provisioning_lock(REALM, "synthetic-admin", clients):
        held = copy.deepcopy(clients.items)
    clients.items = copy.deepcopy(held)
    clients.deleted.clear()
    monkeypatch.setattr(oidc.time, "sleep", lambda _: clients.items.clear())
    with oidc.provisioning_lock(REALM, "synthetic-admin", clients):
        assert set(clients.items).isdisjoint(held)
    assert len(clients.deleted) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"description": "foreign owner"},
        {"enabled": True},
        {"standardFlowEnabled": True},
        {"serviceAccountsEnabled": True},
        {"attributes": {}},
    ],
)
def test_foreign_or_modified_lock_is_never_removed(change):
    clients = Clients()
    with pytest.raises(oidc.ConfigurationError, match="ownership changed"):
        with oidc.provisioning_lock(REALM, "synthetic-admin", clients):
            next(iter(clients.items.values())).update(change)
    assert len(clients.items) == 1 and not clients.deleted
    with pytest.raises(oidc.ConfigurationError, match="ownership conflict"):
        with oidc.provisioning_lock(REALM, "synthetic-admin", clients):
            pytest.fail("Foreign lock was accepted")
    assert len(clients.items) == 1 and not clients.deleted


def test_failed_post_without_owned_lock_never_enters():
    def api(url, **kwargs):
        if kwargs.get("method") == "POST":
            raise oidc.TemporaryFailure()
        assert kwargs.get("optional") is True
        return None

    with pytest.raises(oidc.TemporaryFailure):
        with oidc.provisioning_lock(REALM, "synthetic-admin", api):
            pytest.fail("Unconfirmed acquisition entered")


ENV = {
    "KEYCLOAK_SERVER_URL": "http://localhost",
    "KEYCLOAK_REALM_NAME": "fixture",
    "ACCESS_TOKEN": "synthetic-admin",
    "ENTERPRISE_SSO_OIDC_ISSUER": "https://oidc.example.test",
    "ENTERPRISE_SSO_OIDC_CLIENT_ID": "fixture",
    "ENTERPRISE_SSO_OIDC_CLIENT_SECRET": "fixture-secret",
}


def test_startup_lock_is_target_specific_and_validated_in_child():
    clients = Clients()
    master, client_id = oidc.startup_lock_target(ENV)
    assert master.endswith("/realms/master")
    assert (
        oidc.startup_lock_target({**ENV, "KEYCLOAK_REALM_NAME": "other"})[1]
        != client_id
    )

    def run(args, env):
        assert args == ["sh", "/fixture/startup.sh"]
        assert oidc.STARTUP_LOCK_ENV in env and oidc.STARTUP_LOCK_ENV not in ENV
        oidc.verify_startup_lock(env, clients)
        lock = next(iter(clients.items.values()))
        assert lock["clientId"] == client_id
        return SimpleNamespace(returncode=0)

    oidc.startup_script(ENV, "/fixture/startup.sh", clients, run)
    assert not clients.items


def test_startup_failure_releases_its_lock():
    clients = Clients()
    with pytest.raises(oidc.ConfigurationError, match="startup script failed"):
        oidc.startup_script(
            ENV,
            "/fixture/startup.sh",
            clients,
            lambda *args, **kwargs: SimpleNamespace(returncode=1),
        )
    assert not clients.items


@pytest.mark.parametrize("owner", ["", "1", "../foreign-resource", "0" * 36])
def test_startup_marker_cannot_skip_lock_validation(owner):
    clients = Clients()
    with pytest.raises(oidc.ConfigurationError, match="lock context is invalid"):
        oidc.verify_startup_lock({**ENV, oidc.STARTUP_LOCK_ENV: owner}, clients)
    assert not clients.deleted


def test_startup_marker_requires_existing_owned_resource():
    import uuid

    clients = Clients()
    with pytest.raises(oidc.ConfigurationError, match="ownership conflict"):
        oidc.verify_startup_lock(
            {**ENV, oidc.STARTUP_LOCK_ENV: str(uuid.uuid4())}, clients
        )


def test_failed_startup_keeps_existing_broker_closed():
    clients = Clients()
    current = {
        "providerId": "oidc",
        "enabled": True,
        "config": {
            "openhandsManaged": "true",
            "openhandsProtocol": "oidc",
            "issuer": ENV["ENTERPRISE_SSO_OIDC_ISSUER"],
            "clientId": "fixture",
        },
    }
    writes = []

    def api(url, **kwargs):
        if url.endswith("/identity-provider/instances/enterprise_sso"):
            if kwargs.get("method") == "PUT":
                writes.append(copy.deepcopy(kwargs["body"]))
                return None
            return copy.deepcopy(current)
        return clients(url, **kwargs)

    def failed_child(*args, **kwargs):
        assert len(writes) == 1 and writes[0]["enabled"] is False
        assert writes[0]["config"]["clientSecret"] == "fixture-secret"
        return SimpleNamespace(returncode=1)

    with pytest.raises(oidc.ConfigurationError, match="startup script failed"):
        oidc.startup_script(ENV, "/fixture/startup.sh", api, failed_child)
    assert not clients.items and len(writes) == 1


def test_startup_never_changes_a_foreign_broker():
    clients = Clients()

    def api(url, **kwargs):
        if url.endswith("/identity-provider/instances/enterprise_sso"):
            assert "body" not in kwargs
            return {"providerId": "saml", "config": {}}
        return clients(url, **kwargs)

    def unexpected_child(*args, **kwargs):
        pytest.fail("Common realm writes started for a foreign broker")

    with pytest.raises(oidc.ConfigurationError, match="ownership/protocol conflict"):
        oidc.startup_script(ENV, "/fixture/startup.sh", api, unexpected_child)
    assert not clients.items
