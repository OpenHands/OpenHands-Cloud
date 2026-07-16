---
name: local-kind-install
description: Stand up a full OpenHands Enterprise Helm install (Replicated registry, GitHub login, working conversations) on a local KinD cluster using the scripts in local-kind/. Use when asked to run, test, or debug a helm install of OpenHands locally, validate chart changes against a live install, or reproduce a customer helm-install issue on a laptop.
---

# Local KinD Helm install

Everything lives in `local-kind/` — read `local-kind/README.md` first. Run the
scripts rather than reimplementing their steps; they encode non-obvious fixes.

## Ask the user before starting

Never invent these inputs — ask the user for them (they are personal to their
domain, accounts, and machine):

- **Base domain** (e.g. `oh.example.com`) — a domain they control, with the
  wildcard DNS records and certificate from the README's one-time setup. If
  they haven't done that setup yet, walk them through it first.
- **TLS certificate + key paths** covering that domain's hostnames.
- **Replicated license** email and license ID.
- **GitHub App credentials** (from `scripts/create_github_app` run with their
  base domain) and the path to its private key.
- **Anthropic API key** — have them place secrets in files or export env vars
  themselves rather than pasting them into chat.

## Sequence

1. `create-cluster.sh` — needs `BASE_DOMAIN`, `TLS_CERT_FILE`, `TLS_KEY_FILE`.
   Creates the KinD cluster (ports 80/443 mapped), installs Traefik with the
   certificate as default, and adds a CoreDNS override so pods resolving
   `*.$BASE_DOMAIN` reach Traefik instead of their own loopback.
2. `create-secrets.sh` — needs the GitHub App env vars and `ANTHROPIC_API_KEY`.
3. `install.sh` — needs `LICENSE_EMAIL`, `LICENSE_ID`; renders
   `values.yaml.tmpl` and installs from `oci://registry.replicated.com`.

The kubeconfig is the default context after `kind create cluster`.

## Constraints that bite (do not "fix" these away)

- **TLS is mandatory and must be publicly trusted.** The frontend hardcodes
  `https://` for non-localhost hosts and sandboxes verify the app cert when
  posting event webhooks. nip.io/self-signed setups fail in misleading ways
  (login `Incorrect redirect_uri` loops, "swallowed" messages, permanent
  `Disconnected`).
- **Keycloak realm and OAuth client are named `allhands`** — the app requests
  `client_id=allhands` regardless of env, and GitHub Apps from
  `scripts/create_github_app` register the `/realms/allhands/...` callback.
  Realm provisioning re-runs on every app-pod restart and re-templates
  redirect URIs from `WEB_HOST`; manual `kcadm` edits do not survive restarts.
- **Hostname layout is `app.<base>` / `auth.app.<base>`** — assumed by the
  GitHub App script and derived by the chart (`auth.<ingress.host>`).
- **`RUNTIME_DISABLE_SSL` defaults to `"true"` in the chart**; the template
  sets it `"false"` so sandbox URLs are https. `RUNTIME_BASE_URL` must match
  `RUNTIME_URL_PATTERN`.
- **Memory**: ~16 GB Docker VM; each conversation spawns a 3 Gi sandbox that
  is never reaped. `Insufficient memory` → delete stale
  `runtime-<id>` deployments in the `openhands` namespace.
- The helm release manages the app: config changes go through
  `helm upgrade` with the same values files, never `kubectl edit`.

## Verifying an install

- `https://app.$BASE_DOMAIN` serves the login page (200, trusted cert).
- Login with the GitHub App completes.
- A conversation gets a `runtime-*` pod, its ingress under
  `*.runtime.$BASE_DOMAIN`, and an agent reply.
- Troubleshoot: `support-bundle --load-cluster-specs -n openhands` collects,
  `support-bundle upload <archive>` sends it to the Vendor Portal.
