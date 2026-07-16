# Local KinD Helm install

Scripts to stand up a full OpenHands Enterprise Helm install — Replicated
registry pull, GitHub login, working agent conversations — on a local
[KinD](https://kind.sigs.k8s.io/) cluster. Formalized from the KinD harness in
[#874](https://github.com/OpenHands/OpenHands-Cloud/pull/874) plus the fixes
found while validating the install docs end to end.

## You need your own domain

**Bring a real domain — the product assumes TLS end to end.** The frontend
hardcodes `https://` for every non-localhost host, and in-cluster components
(sandbox webhooks) *verify* the app's certificate. Self-signed certificates
and wildcard-DNS services like nip.io produce installs where login loops,
messages disappear, or sandboxes never connect — all silently. A domain you
control costs a few DNS records and gives you real Let's Encrypt certificates
via DNS-01 (no public reachability required; the records point at 127.0.0.1).

### One-time setup (per domain)

Pick a base, e.g. `oh.example.com`, and create these DNS records:

| Record (A) | Value |
|---|---|
| `*.oh.example.com` | `127.0.0.1` |
| `*.app.oh.example.com` | `127.0.0.1` |
| `*.runtime.oh.example.com` | `127.0.0.1` |

Issue one certificate covering all three wildcards via DNS-01, e.g. with
[acme.sh](https://github.com/acmesh-official/acme.sh) and your DNS provider's
API:

```bash
acme.sh --issue --server letsencrypt --dns dns_<provider> \
  -d 'oh.example.com' -d '*.oh.example.com' \
  -d '*.app.oh.example.com' -d '*.runtime.oh.example.com'
```

Create a GitHub App for login (its callbacks embed the base domain):

```bash
python scripts/create_github_app/create_github_app.py --base-domain oh.example.com
```

You also need a Replicated license with Helm installs enabled (email +
license ID) and an Anthropic API key.

## Bring it up

```bash
export BASE_DOMAIN=oh.example.com
export TLS_CERT_FILE=~/certs/fullchain.cer TLS_KEY_FILE=~/certs/oh.example.com.key

./local-kind/create-cluster.sh

export GITHUB_APP_ID=... GITHUB_APP_SLUG=... GITHUB_APP_CLIENT_ID=... \
       GITHUB_APP_CLIENT_SECRET=... GITHUB_APP_WEBHOOK_SECRET=... \
       GITHUB_APP_PRIVATE_KEY_FILE=./scripts/create_github_app/keys/<app>.pem \
       ANTHROPIC_API_KEY=sk-ant-...
./local-kind/create-secrets.sh

export LICENSE_EMAIL=you@example.com LICENSE_ID=...
./local-kind/install.sh    # first run pulls several GB of images
```

Then open `https://app.$BASE_DOMAIN`, log in with GitHub, and start a
conversation.

## Sizing

The cluster VM (Docker Desktop / OrbStack) needs **~16 GB**: the stack
requests ~7 GB and every conversation spawns a 3 Gi sandbox. Sandboxes are
not garbage-collected here (`runtime-api.cleanup` is disabled); reclaim
memory with `kubectl delete deploy -n openhands runtime-<id>` when
conversations pile up.

## Tear down

```bash
./local-kind/delete-cluster.sh
```

Removes all listeners and in-cluster secrets. DNS records, certificates, the
GitHub App, and the license survive for the next run.
