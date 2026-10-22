# TLS certificate source

Replicated/KOTS installs choose where the serving certificate comes from under **Certificate Configuration → Certificate Source**.

| Source | Who writes the secret | Renewal |
| -- | -- | -- |
| **Upload** (default) | OpenHands renders `openhands-tls` (and Keycloak's `<auth-host>-tls`) from the uploaded files on every deploy | Upload the new files and deploy. A secret edited by hand is overwritten on the next deploy. |
| **Existing secret** | You. OpenHands only reads the secret you name. | Replace the secret's contents. Traefik picks up the change without a deploy. |

## Using an existing secret

1. Create a `kubernetes.io/tls` secret in the `openhands` namespace. `tls.crt` holds the full chain and `tls.key` holds the private key. The certificate needs the same SANs listed in the Admin Console under the certificate fields.

   ```bash
   kubectl -n openhands create secret tls openhands-customer-tls --cert=fullchain.pem --key=privkey.pem
   ```

2. In the Admin Console, set **Certificate Source** to **Existing secret** and **TLS Secret Name** to the secret's name, then deploy. Preflights fail if the secret is missing, and warn if its SANs miss a hostname. During that one deploy, hostnames can briefly serve the ingress controller's default certificate: the installer-rendered secrets are removed before the app release starts using yours.

Do not name `openhands-tls`, `app-all-hands-<Admin Console namespace>-tls` or `<auth-host>-tls`. OpenHands creates those in Upload mode and deletes them when you switch, and the chart refuses to render with any of them.

To switch back, select **Upload** and deploy. The fields still hold the files you last uploaded, so replace them first if that certificate has expired. Your secret is left in place.

## Managing the secret with cert-manager

cert-manager runs on Embedded Cluster installs, so it can issue and renew the secret. Create your own `Issuer` or `ClusterIssuer` (ACME, Vault, Venafi or a private CA) and a `Certificate` whose `secretName` is the secret you select above. OpenHands never writes that secret, so cert-manager keeps ownership of it across deploys.

For ACME, the sandbox hostnames need a wildcard SAN on subdomain routing. Let's Encrypt only issues wildcards through a DNS-01 solver, which needs credentials for your DNS provider. Path-based sandbox routing has no wildcard SAN, so HTTP-01 is enough there.
