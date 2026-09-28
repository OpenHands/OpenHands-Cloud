# Keycloak administration

In the Replicated Admin Console, open **Config → Keycloak Administration**,
enter a **Keycloak Admin Password**, save, and deploy. Open the Keycloak URL
shown in the field's help text (`https://<authentication-hostname>/admin/`)
and sign in as **admin** with that password. Select the **allhands** realm to
manage OpenHands users and identity provider links.

The setting works on fresh and existing installations. Changing it and deploying
updates the existing admin account without recreating users. Leaving it blank
keeps the current password; it does not disable the account or reset the password.
Manage this password in Replicated: a later deployment reapplies the configured
value if someone changed it directly in Keycloak.

For direct Helm installations, set `config.keycloak_admin_ui_password` in the
`openhands-secrets` chart and restart the OpenHands deployment after updating the
Secret. Realm provisioning must be enabled. The Replicated deployment triggers
this restart automatically through its secrets checksum.

## Upgrade and recovery behavior

The hidden `keycloak_admin_password` setting remains the installation's original
bootstrap credential. Do not change it to reset the console password. Replicated
sets `KEYCLOAK_ADMIN_CLIENT_ID=openhands-provisioner` so provisioning creates the
service account even before a console password is configured. Direct Helm installs
opt in by setting `env.KEYCLOAK_ADMIN_CLIENT_ID` on the `openhands` chart, or by
configuring a console password. Without either, provisioning keeps password
authentication and does not create a privileged client.

Provisioning uses the configured client ID, falling back to `openhands-provisioner`
for console-password-only installations. The application and provisioning script
accept `KEYCLOAK_ADMIN_CLIENT_SECRET` through their environment; when omitted,
both use the stable bootstrap credential. Supply a separate secret consistently
to both containers when using an independently managed service credential.
Its service account receives the master `admin` role with an explicit role scope;
browser and password login flows are disabled for this client. The installer
verifies service-account administration access before changing the human admin
password, then uses client credentials for later provisioning and token refreshes.

Do not delete or disable this client: future deployments need it. An interrupted
client setup can be retried while the original admin password still works. A
conflicting client with different credentials causes provisioning to fail without
replacing that client's credentials or changing the admin password.

Before rolling back to a chart that predates this feature, restore the human
admin password to the original `keycloak-admin` Secret's `admin-password` value.
Old charts authenticate as the human admin and cannot use the provisioning client.
If the original admin password was already changed manually before enabling this
feature, restore working provisioning access first; this is not an unauthenticated
Keycloak password-recovery mechanism.

## Local validation

With Docker, Helm, curl, jq, and uv installed:

```sh
KEYCLOAK_TEST_IMAGE=quay.io/keycloak/keycloak:26.3.3 \
  uv run --with pytest --with pyyaml pytest scripts/test_keycloak_admin_password.py -q
```

The integration tests start disposable local containers and cover fresh installs,
existing admin identity preservation, password rotation, special characters,
password-history idempotency, blank settings, interrupted setup, and conflicting
clients. Without `KEYCLOAK_TEST_IMAGE`, Docker tests are skipped.
