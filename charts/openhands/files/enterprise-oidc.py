"""Reconcile the chart-owned upstream OIDC broker. Never log response bodies."""

import json
import hashlib
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ConfigurationError(ValueError):
    """A safe operator-facing error containing no remote response data."""


class TemporaryFailure(Exception):
    """A network failure, without the URL or remote response body."""


class Conflict(Exception):
    """Another provisioner may have created the resource concurrently."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.query
        or any(c.isspace() for c in value)
    ):
        raise ConfigurationError(
            "OIDC URLs must be absolute HTTPS URLs without credentials, query or fragment"
        )
    return parsed


def provider(discovery, issuer, client_id, client_secret, display_name):
    origin = https_url(issuer).netloc
    if discovery.get("issuer") != issuer:
        raise ConfigurationError(
            "Discovery issuer does not exactly match the configured issuer"
        )
    for field in (
        "authorization_endpoint",
        "token_endpoint",
        "jwks_uri",
        "userinfo_endpoint",
    ):
        if https_url(discovery.get(field, "")).netloc != origin:
            raise ConfigurationError(
                "Discovery endpoints must share the configured issuer origin"
            )
    if "client_secret_basic" not in discovery.get(
        "token_endpoint_auth_methods_supported", []
    ):
        raise ConfigurationError("Issuer must support client_secret_basic")
    if "S256" not in discovery.get("code_challenge_methods_supported", []):
        raise ConfigurationError("Issuer must advertise S256 PKCE")
    if not client_id or not client_secret:
        raise ConfigurationError("OIDC client credentials are required")
    return {
        "alias": "enterprise_sso",
        "providerId": "oidc",
        "enabled": True,
        "displayName": display_name,
        "trustEmail": True,
        "storeToken": False,
        "addReadTokenRoleOnCreate": False,
        "linkOnly": False,
        "hideOnLogin": True,
        "firstBrokerLoginFlowAlias": FIRST_FLOW,
        "postBrokerLoginFlowAlias": POST_FLOW,
        "config": {
            "issuer": issuer,
            "authorizationUrl": discovery["authorization_endpoint"],
            "tokenUrl": discovery["token_endpoint"],
            "jwksUrl": discovery["jwks_uri"],
            "userInfoUrl": discovery["userinfo_endpoint"],
            "clientId": client_id,
            "clientSecret": client_secret,
            "clientAuthMethod": "client_secret_basic",
            "defaultScope": "openid profile email",
            "validateSignature": "true",
            "useJwksUrl": "true",
            "pkceEnabled": "true",
            "pkceMethod": "S256",
            "disableNonce": "false",
            "syncMode": "FORCE",
            "filteredByClaim": "false",
            "openhandsManaged": "true",
            "openhandsProtocol": "oidc",
        },
    }


def request(url, token=None, body=None, method=None, optional=False):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    try:
        with build_opener(NoRedirect()).open(
            Request(url, data, headers, method=method), timeout=30
        ) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ConfigurationError("OIDC response exceeds size limit")
            return json.loads(raw) if raw else None
    except HTTPError as exc:
        if optional and exc.code == 404:
            return None
        if exc.code == 409:
            raise Conflict from None
        if exc.code in (408, 429) or exc.code >= 500:
            raise TemporaryFailure from None
        raise ConfigurationError(
            "OIDC provisioning HTTP request failed (status %s)" % exc.code
        ) from None
    except (URLError, TimeoutError, ConnectionError):
        raise TemporaryFailure from None


def check_owner(current, env):
    if not current:
        return
    if (
        current.get("providerId") != "oidc"
        or current.get("config", {}).get("openhandsProtocol") != "oidc"
        or current.get("config", {}).get("openhandsManaged") != "true"
    ):
        raise ConfigurationError(
            "enterprise_sso ownership/protocol conflict; use a separate realm"
        )
    if (
        current["config"].get("issuer") != env["ENTERPRISE_SSO_OIDC_ISSUER"]
        or current["config"].get("clientId") != env["ENTERPRISE_SSO_OIDC_CLIENT_ID"]
    ):
        raise ConfigurationError(
            "Issuer/client identity is immutable; use a separate realm"
        )


FIRST_FLOW = "openhands-oidc-create-only-v1"
POST_FLOW = "openhands-oidc-verified-email-v1"
DENY_FLOW = "openhands-oidc-deny-unverified-v1"
VERIFIED_ATTRIBUTE = "openhands_oidc_email_verified"
FLOW_OWNER = "OpenHands managed OIDC verification v1; do not edit"
LOCK_CLIENT = "openhands-oidc-provisioning-lock-v1"
LOCK_OWNER = "OpenHands managed OIDC provisioning lock v1; do not edit"
LOCK_WAIT_SECONDS = 30
LOCK_POLL_SECONDS = 1


def _owned_lock(value, owner, client_id=LOCK_CLIENT):
    return (
        isinstance(owner, str)
        and bool(owner)
        and value.get("id") == owner
        and value.get("clientId") == client_id
        and value.get("description") == LOCK_OWNER
        and value.get("protocol") == "openid-connect"
        and value.get("attributes", {}).get("openhandsLockOwner") == owner
        and value.get("enabled") is False
        and value.get("publicClient") is True
        and all(
            value.get(field) is False
            for field in (
                "standardFlowEnabled",
                "directAccessGrantsEnabled",
                "implicitFlowEnabled",
                "serviceAccountsEnabled",
                "fullScopeAllowed",
            )
        )
    )


@contextmanager
def provisioning_lock(
    realm, token, api=request, client_id=LOCK_CLIENT, refresh_token=None
):
    """Serialize writers with Keycloak's unique per-realm client ID.

    The disabled client grants no login/service-account access. There is no
    time-based lock stealing: a paused or crashed owner must never overlap a
    second writer. An abandoned lock needs operator recovery after all prior
    provisioners have stopped. Waiting pods fail startup and can retry later.
    """
    owner = str(uuid.uuid4())
    url = realm + "/clients"
    own_url = url + "/" + owner
    desired = {
        "id": owner,
        "clientId": client_id,
        "description": LOCK_OWNER,
        "protocol": "openid-connect",
        "enabled": False,
        "publicClient": True,
        "standardFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "implicitFlowEnabled": False,
        "serviceAccountsEnabled": False,
        "fullScopeAllowed": False,
        "defaultClientScopes": [],
        "optionalClientScopes": [],
        "attributes": {"openhandsLockOwner": owner},
    }
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        if refresh_token:
            token = refresh_token()
        try:
            api(url, token=token, body=desired, method="POST")
            break
        except Conflict:
            existing = api(url + "?clientId=" + client_id, token=token)
            if any(
                not _owned_lock(item, item.get("id"), client_id) for item in existing
            ):
                raise ConfigurationError("OIDC provisioning lock ownership conflict")
            if time.monotonic() >= deadline:
                raise ConfigurationError(
                    "OIDC provisioning is locked (master client " + client_id + "); "
                    "stop prior provisioners before "
                    "recovering the managed lock client in Keycloak"
                )
            time.sleep(LOCK_POLL_SECONDS)
        except TemporaryFailure:
            # A lost POST response may still have committed. Read only this
            # attempt's UUID; never adopt another writer's lock.
            existing = api(own_url, token=token, optional=True)
            if existing and _owned_lock(existing, owner, client_id):
                break
            raise
    try:
        if refresh_token:
            token = refresh_token()
        existing = api(own_url, token=token)
        if not _owned_lock(existing, owner, client_id):
            raise ConfigurationError("OIDC provisioning lock ownership conflict")
        yield owner
    finally:
        if refresh_token:
            token = refresh_token()
        existing = api(own_url, token=token, optional=True)
        if not existing or not _owned_lock(existing, owner, client_id):
            raise ConfigurationError(
                "OIDC provisioning lock ownership changed; refusing cleanup"
            )
        api(own_url, token=token, method="DELETE")


STARTUP_LOCK_ENV = "OPENHANDS_OIDC_STARTUP_LOCK"


def refresh_admin_token(env):
    """Renew as the selected startup principal; never log token responses."""
    # ACCESS_TOKEN-only embedding remains supported. The chart always exports
    # USE_PROVISIONER so its long-running startup/cleanup can renew credentials.
    if "USE_PROVISIONER" not in env:
        return env["ACCESS_TOKEN"]
    if env["USE_PROVISIONER"] == "true":
        fields = {
            "client_id": env.get("KEYCLOAK_ADMIN_CLIENT_ID", "").strip()
            or "openhands-provisioner",
            "grant_type": "client_credentials",
            "client_secret": env.get("KEYCLOAK_ADMIN_CLIENT_SECRET")
            or env["KEYCLOAK_ADMIN_PASSWORD"],
        }
    else:
        fields = {
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": "admin",
            "password": env["KEYCLOAK_ADMIN_PASSWORD"],
        }
    url = (
        env["KEYCLOAK_SERVER_URL"].rstrip("/")
        + "/realms/master/protocol/openid-connect/token"
    )
    try:
        with build_opener(NoRedirect()).open(
            Request(
                url,
                urlencode(fields).encode(),
                {"Content-Type": "application/x-www-form-urlencoded"},
            ),
            timeout=30,
        ) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError()
        token = json.loads(raw).get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError()
    except (
        HTTPError,
        URLError,
        TimeoutError,
        ConnectionError,
        ValueError,
        AttributeError,
    ):
        raise ConfigurationError(
            "Could not renew Keycloak provisioning access token"
        ) from None
    env["ACCESS_TOKEN"] = token
    return token


def startup_lock_target(env):
    # The target realm may not exist yet. A disabled master-realm client with
    # a deterministic, target-specific name serializes its creation/update.
    master = env["KEYCLOAK_SERVER_URL"].rstrip("/") + "/admin/realms/master"
    client_id = (
        LOCK_CLIENT
        + "-"
        + hashlib.sha256(env["KEYCLOAK_REALM_NAME"].encode()).hexdigest()[:24]
    )
    return master, client_id


def verify_startup_lock(env, api=request):
    master, client_id = startup_lock_target(env)
    owner = env.get(STARTUP_LOCK_ENV, "")
    try:
        if str(uuid.UUID(owner)) != owner:
            raise ValueError()
    except ValueError:
        raise ConfigurationError("OIDC startup lock context is invalid") from None
    lock = api(master + "/clients/" + owner, token=env["ACCESS_TOKEN"], optional=True)
    if not lock or not _owned_lock(lock, owner, client_id):
        raise ConfigurationError("OIDC startup lock ownership conflict")


def startup_script(env, script, api=request, run=subprocess.run):
    env = dict(env)
    https_url(env["ENTERPRISE_SSO_OIDC_ISSUER"])
    master, client_id = startup_lock_target(env)
    with provisioning_lock(
        master, env["ACCESS_TOKEN"], api, client_id, lambda: refresh_admin_token(env)
    ) as owner:
        # Common realm updates can replace user-profile attributes. Keep the
        # broker closed throughout those updates, before rebuilding its policy.
        realm = (
            env["KEYCLOAK_SERVER_URL"].rstrip("/")
            + "/admin/realms/"
            + quote(env["KEYCLOAK_REALM_NAME"], safe="")
        )
        url = realm + "/identity-provider/instances/enterprise_sso"
        current = api(url, token=env["ACCESS_TOKEN"], optional=True)
        check_owner(current, env)
        if current:
            paused = dict(current, enabled=False)
            paused["config"] = dict(
                current["config"], clientSecret=env["ENTERPRISE_SSO_OIDC_CLIENT_SECRET"]
            )
            api(url, token=env["ACCESS_TOKEN"], body=paused, method="PUT")
        child_env = dict(env)
        child_env[STARTUP_LOCK_ENV] = owner
        result = run(["sh", script], env=child_env)
        if result.returncode:
            raise ConfigurationError("OIDC startup script failed; provisioning stopped")


def security_flows(realm, token, api=request):
    """Build fail-closed flows before removing the legacy ID-token-only filter.

    Existing-email collisions are rejected: no account is linked by email and
    no verification message is sent. A denied new user may leave a Keycloak
    record, but receives no application code/session. Only these owned flows
    are touched; SAML and the realm's browser flow remain unchanged.
    """

    def call(path, **kw):
        return api(realm + path, token=token, **kw)

    profile = call("/users/profile")
    attributes = profile.setdefault("attributes", [])
    found = [a for a in attributes if a.get("name") == VERIFIED_ATTRIBUTE]
    attribute = {
        "name": VERIFIED_ATTRIBUTE,
        "displayName": "OIDC verified email",
        "multivalued": False,
        "permissions": {"view": ["admin"], "edit": ["admin"]},
    }
    if found and (len(found) != 1 or found[0] != attribute):
        raise ConfigurationError("OIDC verification attribute ownership conflict")
    if not found:
        attributes.append(attribute)
        call("/users/profile", body=profile, method="PUT")

    def flow(alias, top_level, flow_id=None):
        flows = (
            [call("/authentication/flows/" + flow_id)]
            if flow_id
            else [f for f in call("/authentication/flows") if f.get("alias") == alias]
        )
        if not flows:
            try:
                call(
                    "/authentication/flows",
                    body={
                        "alias": alias,
                        "description": FLOW_OWNER,
                        "providerId": "basic-flow",
                        "topLevel": top_level,
                        "builtIn": False,
                    },
                    method="POST",
                )
            except Conflict:
                pass
            flows = [
                f for f in call("/authentication/flows") if f.get("alias") == alias
            ]
        if (
            len(flows) != 1
            or flows[0].get("description") != FLOW_OWNER
            or flows[0].get("providerId") != "basic-flow"
            or flows[0].get("builtIn")
            or flows[0].get("topLevel") != top_level
        ):
            raise ConfigurationError("OIDC authentication flow ownership conflict")
        return flows[0]

    def executions(alias):
        return call("/authentication/flows/" + quote(alias, safe="") + "/executions")

    def step(alias, provider_id, requirement, config=None):
        matches = [e for e in executions(alias) if e.get("providerId") == provider_id]
        if not matches:
            try:
                call(
                    "/authentication/flows/"
                    + quote(alias, safe="")
                    + "/executions/execution",
                    body={"provider": provider_id},
                    method="POST",
                )
            except Conflict:
                pass
            matches = [
                e for e in executions(alias) if e.get("providerId") == provider_id
            ]
        if len(matches) != 1:
            raise ConfigurationError("Ambiguous OIDC authentication execution")
        execution = matches[0]
        if config:
            desired = {"alias": alias + "-condition", "config": config}
            if execution.get("authenticationConfig"):
                old = call(
                    "/authentication/config/" + execution["authenticationConfig"]
                )
                if old.get("alias") != desired["alias"] or old.get("config") != config:
                    raise ConfigurationError(
                        "OIDC authenticator configuration conflict"
                    )
            else:
                call(
                    "/authentication/executions/" + execution["id"] + "/config",
                    body=desired,
                    method="POST",
                )
        if execution.get("requirement") != requirement:
            execution["requirement"] = requirement
            call(
                "/authentication/flows/" + quote(alias, safe="") + "/executions",
                body=execution,
                method="PUT",
            )

    flow(FIRST_FLOW, True)
    step(FIRST_FLOW, "idp-create-user-if-unique", "REQUIRED")
    if [e.get("providerId") for e in executions(FIRST_FLOW)] != [
        "idp-create-user-if-unique"
    ]:
        raise ConfigurationError(
            "Unexpected OIDC first-login execution; refusing automatic linking"
        )

    flow(POST_FLOW, True)
    nested = [e for e in executions(POST_FLOW) if e.get("displayName") == DENY_FLOW]
    if not nested:
        # Creating a nested flow also creates its execution under the parent.
        if any(f.get("alias") == DENY_FLOW for f in call("/authentication/flows")):
            raise ConfigurationError(
                "Orphaned OIDC deny flow; operator review required"
            )
        try:
            call(
                "/authentication/flows/" + POST_FLOW + "/executions/flow",
                body={
                    "alias": DENY_FLOW,
                    "description": FLOW_OWNER,
                    "provider": "basic-flow",
                    "type": "basic-flow",
                },
                method="POST",
            )
        except Conflict:
            pass
        nested = [e for e in executions(POST_FLOW) if e.get("displayName") == DENY_FLOW]
    if len(nested) != 1:
        raise ConfigurationError("Ambiguous OIDC deny subflow")
    flow(DENY_FLOW, False, nested[0]["flowId"])
    execution = nested[0]
    if execution.get("requirement") != "CONDITIONAL":
        execution["requirement"] = "CONDITIONAL"
        call(
            "/authentication/flows/" + POST_FLOW + "/executions",
            body=execution,
            method="PUT",
        )
    step(
        DENY_FLOW,
        "conditional-user-attribute",
        "REQUIRED",
        {
            "attribute_name": VERIFIED_ATTRIBUTE,
            "attribute_expected_value": "true",
            "include_group_attributes": "false",
            "not": "true",
            "regex": "false",
        },
    )
    step(DENY_FLOW, "deny-access-authenticator", "REQUIRED")
    if [e.get("providerId") for e in executions(DENY_FLOW)] != [
        "conditional-user-attribute",
        "deny-access-authenticator",
    ]:
        raise ConfigurationError("Unexpected OIDC verification execution")
    step(POST_FLOW, "allow-access-authenticator", "REQUIRED")
    if len(executions(POST_FLOW)) != 4:
        raise ConfigurationError("Unexpected OIDC post-login flow execution")


def reconcile_mappers(url, token, api=request):
    desired = [
        {
            "name": "identity-provider",
            "identityProviderAlias": "enterprise_sso",
            "identityProviderMapper": "hardcoded-attribute-idp-mapper",
            "config": {
                "attribute": "identity_provider",
                "attribute.value": "enterprise_sso:oidc",
                "syncMode": "FORCE",
            },
        },
        {
            "name": "openhands-oidc-verified-email-v1",
            "identityProviderAlias": "enterprise_sso",
            "identityProviderMapper": "oidc-user-attribute-idp-mapper",
            "config": {
                "claim": "email_verified",
                "user.attribute": VERIFIED_ATTRIBUTE,
                "syncMode": "FORCE",
            },
        },
    ]
    mappers_url = url + "/enterprise_sso/mappers"
    for mapper in desired:

        def find():
            return [
                m
                for m in api(mappers_url, token=token)
                if m.get("name") == mapper["name"]
            ]

        existing = find()
        if not existing:
            try:
                api(mappers_url, token=token, body=mapper, method="POST")
                continue
            except Conflict:
                existing = find()
        if len(existing) != 1:
            raise ConfigurationError("Ambiguous identity provider mapper")
        old = existing[0]
        expected = {k: v for k, v in mapper["config"].items() if k != "syncMode"}
        if old.get("identityProviderMapper") != mapper["identityProviderMapper"] or any(
            old.get("config", {}).get(k) != v for k, v in expected.items()
        ):
            raise ConfigurationError("Identity mapper ownership conflict")
        mapper["id"] = old["id"]
        api(
            mappers_url + "/" + quote(old["id"], safe=""),
            token=token,
            body=mapper,
            method="PUT",
        )


def reconcile(env, api=request):
    env = dict(env)
    https_url(env["ENTERPRISE_SSO_OIDC_ISSUER"])
    if env.get(STARTUP_LOCK_ENV):
        verify_startup_lock(env, api)
        _reconcile_locked(env, api)
    else:
        master, client_id = startup_lock_target(env)
        with provisioning_lock(
            master,
            env["ACCESS_TOKEN"],
            api,
            client_id,
            lambda: refresh_admin_token(env),
        ):
            _reconcile_locked(env, api)


def _reconcile_locked(env, api=request):
    issuer = env["ENTERPRISE_SSO_OIDC_ISSUER"]
    https_url(issuer)
    realm = (
        env["KEYCLOAK_SERVER_URL"].rstrip("/")
        + "/admin/realms/"
        + quote(env["KEYCLOAK_REALM_NAME"], safe="")
    )
    token = env["ACCESS_TOKEN"]
    url = realm + "/identity-provider/instances"
    current = api(url + "/enterprise_sso", token=token, optional=True)
    check_owner(current, env)
    try:
        discovery = api(issuer.rstrip("/") + "/.well-known/openid-configuration")
    except TemporaryFailure:
        # Preserve availability only if the already provisioned configuration
        # still matches every configured/security-sensitive setting. Continue
        # mapper reconciliation below, including on a previously partial run.
        # The outer startup lock may have deliberately disabled this otherwise
        # matching broker; only enable it after strict flow/mapper validation.
        config = (current or {}).get("config", {})
        discovery = {
            "issuer": issuer,
            "authorization_endpoint": config.get("authorizationUrl", ""),
            "token_endpoint": config.get("tokenUrl", ""),
            "jwks_uri": config.get("jwksUrl", ""),
            "userinfo_endpoint": config.get("userInfoUrl", ""),
            "token_endpoint_auth_methods_supported": ["client_secret_basic"],
            "code_challenge_methods_supported": ["S256"],
        }
        # Keycloak masks the stored client secret on admin GET. The PUT below
        # reapplies the operator-provided secret; do not compare it to the mask.
        expected = provider(
            discovery,
            issuer,
            env["ENTERPRISE_SSO_OIDC_CLIENT_ID"],
            env["ENTERPRISE_SSO_OIDC_CLIENT_SECRET"],
            env["ENTERPRISE_SSO_DISPLAY_NAME"],
        )
        if (
            not current
            or not isinstance(current.get("enabled"), bool)
            or any(
                current.get(k) != expected[k]
                for k in (
                    "trustEmail",
                    "storeToken",
                    "linkOnly",
                    "firstBrokerLoginFlowAlias",
                    "postBrokerLoginFlowAlias",
                )
            )
            or any(
                config.get(k) != v
                for k, v in expected["config"].items()
                if k != "clientSecret"
            )
        ):
            raise ConfigurationError(
                "Discovery unavailable and existing broker does not match requested secure configuration"
            ) from None
        print(
            "WARNING: OIDC discovery temporarily unavailable; retaining matching managed configuration.",
            file=sys.stderr,
        )
    desired = provider(
        discovery,
        issuer,
        env["ENTERPRISE_SSO_OIDC_CLIENT_ID"],
        env["ENTERPRISE_SSO_OIDC_CLIENT_SECRET"],
        env["ENTERPRISE_SSO_DISPLAY_NAME"],
    )
    if current:
        paused = dict(current, enabled=False)
        paused["config"] = dict(
            current["config"], clientSecret=env["ENTERPRISE_SSO_OIDC_CLIENT_SECRET"]
        )
        api(url + "/enterprise_sso", token=token, body=paused, method="PUT")
    security_flows(realm, token, api)
    # Never expose the new filter policy before both mappers are ready.
    # Failed provisioning leaves the broker disabled, rather than permitting
    # an unchecked login between provider and mapper updates.
    disabled = dict(desired, enabled=False)
    if not current:
        try:
            api(url, token=token, body=disabled, method="POST")
        except Conflict:
            check_owner(api(url + "/enterprise_sso", token=token), env)
            api(url + "/enterprise_sso", token=token, body=disabled, method="PUT")
    reconcile_mappers(url, token, api)
    api(url + "/enterprise_sso", token=token, body=desired, method="PUT")


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--startup-script":
            startup_script(os.environ, sys.argv[2])
        elif len(sys.argv) == 2 and sys.argv[1] == "--verify-startup-lock":
            verify_startup_lock(os.environ)
        elif len(sys.argv) == 1:
            reconcile(os.environ)
            print("Managed enterprise OIDC broker reconciled.")
        else:
            raise ConfigurationError("Invalid OIDC provisioning command")
    except Exception as exc:
        # Responses and URLs can carry credentials; expose only known safe errors.
        print(
            str(exc)
            if isinstance(exc, ConfigurationError)
            else "OIDC provisioning failed (%s)" % type(exc).__name__,
            file=sys.stderr,
        )
        sys.exit(1)
