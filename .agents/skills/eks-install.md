---
name: eks-install
description: Stand up an OpenHands Enterprise Helm install on a throwaway AWS EKS cluster (ghcr chart source, Traefik behind an NLB, cert-manager, real Let's Encrypt TLS, embedded Postgres on a gp3 EBS PVC) using eks-install/. Use when a reproduction needs real cloud behaviour KinD cannot provide - PVC expansion, enforced volume capacity, real ingress and TLS - and specifically when the target environment is AWS/EKS.
---

# EKS test Helm install

Reach for this over `local-kind-install` only when the reproduction needs
something KinD genuinely cannot do (real EBS capacity/expansion, a real load
balancer, publicly trusted certs) and the target environment is AWS/EKS. KinD is
faster and free for most chart work.

Everything lives in `eks-install/`. The scripts encode the non-obvious AWS fixes
below — run them rather than reimplementing. `eks-install/values.yaml.tmpl` is the
values file and `../local-kind/create-secrets.sh` is reused unchanged.

**What KinD cannot reproduce** (all real on EBS):

- `df` inside the Postgres pod reports the node filesystem, not the volume,
  because `local-path` is a host bind mount. On EBS it reports the real volume.
- Volume capacity is not enforced, so a "full disk" cannot be reproduced. EBS
  enforces it.
- `kubectl patch pvc` is a silent no-op on `local-path`. With the gp3 CSI class
  (`allowVolumeExpansion: true`) expansion actually happens.
- Real ingress, real DNS, and publicly trusted certificates.

## Non-obvious EKS setup (read this first)

These are the EKS specifics that actually bite; the chart-level constraints are
shared and listed further down.

- **The load balancer is a hostname, not an IP.** AWS's Traefik `LoadBalancer`
  yields `.status.loadBalancer.ingress[0].hostname` (an ELB DNS name), not
  `[0].ip`. So the DNS record is a **CNAME** (or a Route53 alias A record), never
  an `A` with a literal IP. `create-cluster.sh` reads `.hostname` and writes a
  wildcard CNAME.
- **The EBS CSI driver is not installed by default.** EKS does not ship EBS CSI,
  so without the addon (and its IRSA role) every PVC stays `Pending` forever. `create-cluster.sh` installs it via
  `eksctl create iamserviceaccount` + `eksctl create addon`.
- **The default `gp2` StorageClass does not allow expansion.** It uses the
  deprecated in-tree provisioner `kubernetes.io/aws-ebs` with
  `allowVolumeExpansion` unset — so a `kubectl patch pvc` to grow is *rejected*
  (not silently ignored like KinD, but it still won't grow). Create a gp3 CSI
  class (`gp3-storageclass.yaml`) and make it default; point
  `runtime-api.env.STORAGE_CLASS` at it.
- **IRSA needs the cluster's OIDC provider.** `eksctl create cluster --with-oidc`
  provisions it. Both the EBS CSI role and any cert-manager Route53 DNS-01 role
  depend on it.
- **DNS-01 uses Route53, not Cloud DNS.** The wildcard sandbox cert (if you get
  that far) comes from a cert-manager issuer with a `route53` solver bound to an
  IRSA service account; flat per-host certs use HTTP-01 over the NLB.

## Ask the user before starting

- **AWS region** and **cluster name**. Never invent these.
- **Base domain**, which must be a **Route53 hosted zone** in that account so the
  wildcard record can be created without a manual step. Have the **hosted zone
  id** too (`aws route53 list-hosted-zones`).
- **GitHub App credentials** from `scripts/create_github_app`, plus the base
  domain it was created with. This is the single most common cause of a broken
  login — see the callback constraint below.
- **Anthropic API key**, placed in a file or env var by the user, not pasted in
  chat.

`aws sso login` / credential setup is the user's responsibility; if the CLI
returns `ExpiredToken`, ask them to refresh rather than trying to drive it.

## Sequence

Substitute your own values. Every `kubectl`/`helm` call pins `--context`.

```bash
export CLUSTER=openhands-eks-install REGION=us-east-1
export BASE_DOMAIN=oh.example.com HOSTED_ZONE_ID=ZXXXXXXXXXXXXX
export NAMESPACE=openhands

# 1. cluster + EBS CSI + gp3 default class + Traefik/NLB + Route53 wildcard +
#    cert-manager. ~15 min (EKS control plane is the slow part).
./eks-install/create-cluster.sh

# 2. secrets — the KinD script works as-is against any context/namespace.
export GITHUB_APP_ID=... GITHUB_APP_SLUG=... GITHUB_APP_CLIENT_ID=... \
       GITHUB_APP_CLIENT_SECRET=... GITHUB_APP_WEBHOOK_SECRET=... \
       GITHUB_APP_PRIVATE_KEY_FILE=./scripts/create_github_app/keys/<app>.pem \
       ANTHROPIC_API_KEY=sk-ant-...
./local-kind/create-secrets.sh

# 3. install (public ghcr chart + images; no pull secret, no license).
export CTX=${CLUSTER}.${REGION}.eksctl.io STORAGE_CLASS=gp3 CLUSTER_ISSUER=letsencrypt
./eks-install/install.sh
```

Then open `https://app.$BASE_DOMAIN`, log in with GitHub, and start a conversation.
Set `REGISTRY=replicated LICENSE_EMAIL=... LICENSE_ID=...` on `install.sh` to pull
from `registry.replicated.com` instead of ghcr.

## Constraints that bite (do not "fix" these away)

### AWS-specific

- **Tear the NLB down before the cluster.** An orphaned NLB keeps ENIs and
  security groups attached to the VPC, and eksctl's VPC CloudFormation stack
  cannot delete while they exist — the teardown hangs or fails half-done.
  `delete-cluster.sh` `helm uninstall`s Traefik (releasing the NLB) and waits
  before `eksctl delete cluster`.
- **A straight `eksctl delete cluster` can orphan EBS volumes.** PVCs on the gp3
  class use `reclaimPolicy: Delete`, so `helm uninstall` removes their volumes —
  but deleting the cluster without uninstalling first leaves them behind, still
  billing. `delete-cluster.sh` uninstalls first and then lists anything tagged
  `kubernetes.io/cluster/$CLUSTER` so you can confirm nothing leaked.
- **Never add a deeper wildcard record.** `*.$BASE_DOMAIN` already matches at any
  depth when no closer name exists, so it covers `auth.app.$BASE_DOMAIN` and every
  `{id}-runtime.$BASE_DOMAIN`. Adding `*.app.$BASE_DOMAIN` creates an empty
  non-terminal node at `app.$BASE_DOMAIN` that **blocks wildcard synthesis**, and
  the app's own hostname starts returning NODATA — the site goes unreachable while
  the service is healthy. Route53 negative caching makes it persist past the undo.
- **`get-credentials`/`update-kubeconfig` will leave a usable context behind, and
  a shared account holds other people's clusters with similar names.** Pin
  `--context` on everything; pod and namespace names are identical across
  clusters, which makes a wrong reading look plausible. Print the exact match and
  the keep-list before deleting (the teardown script does this).
- **Let's Encrypt HTTP-01 validates over the NLB, so the wildcard record must
  resolve first.** If certs stay `Pending`, check the CNAME propagated
  (`dig +short app.$BASE_DOMAIN`) before blaming cert-manager. Use the LE staging
  issuer while iterating to avoid rate limits.

### Chart-level (shared with the local-kind skill)

- **`RUNTIME_ROUTING_MODE: path` requires ingress-nginx or Gateway API.** On
  Traefik with standard ingresses, runtime-api raises `create_ingress_manifest
  failed because path mode is only supported with ingress-nginx` and every sandbox
  fails to start. Use subdomain routing (`RUNTIME_URL_SEPARATOR: "-"`,
  `RUNTIME_URL_PATTERN: https://{runtime_id}-runtime.<base>`), which the values
  template already does. Subdomain routing needs a **wildcard certificate**;
  HTTP-01 cannot issue wildcards, so it needs the Route53 DNS-01 solver and
  `runtime-api.env.RUNTIME_CERT_SECRET`. **This wildcard/sandbox path is not yet
  validated on EKS** — conversations were never confirmed working end to end.
- **The GitHub App callback is derived from the base domain passed to
  `create_github_app`**, not your ingress host. It sets `url:
  https://app.{base_domain}` and registers exactly one callback,
  `https://auth.{base_domain}/realms/allhands/broker/github/endpoint` for the flat
  layout. Keycloak's hostname must match that string exactly. To recover the base
  domain from an existing app, mint an app JWT and read `external_url` from
  `GET /app`. Do **not** probe GitHub with a bogus `redirect_uri` to test it — a
  302 proves nothing; only a real login round trip does.
- **`postgresql.primary.resourcesPreset` is inert on its own.** The umbrella chart
  pins `postgresql.primary.resources`, and Bitnami prefers explicit resources over
  any preset, so the container gets a 1Gi limit regardless. Add `resources: null`
  to delete the pinned block and let the preset apply. `shared_buffers = 1GB`
  inside a 1Gi limit OOM-kills Postgres under load while looking healthy at idle
  (`1/1 Running`, ~60 MiB), because shared pages are charged to the cgroup as they
  are touched.
- **Leaving `persistence.size` unset yields 8Gi** (the Bitnami subchart default;
  the umbrella chart never sets a size). A StatefulSet's `volumeClaimTemplates` is
  immutable — adding `size:` and upgrading fails whole and applies nothing else in
  that upgrade. Grow the PVC first with `kubectl patch pvc` (works on gp3), then
  `kubectl delete sts <release>-postgresql --cascade=orphan` and upgrade; pods
  survive.
- **MinIO deadlocks on upgrade.** Its Deployment uses `RollingUpdate` with an RWO
  PVC, so the new pod cannot attach (`Multi-Attach error`, and on EBS the volume
  is also AZ-pinned) until the old one is deleted by hand. It wants `Recreate`.
- **`replicated.enabled` defaults to true**, and the SDK license is injected only
  at pull time by `registry.replicated.com`. On a ghcr install the pod crashloops
  with `either license in the config file or integration license id must be
  specified`. The values template sets it false.
- **Recreating the Postgres database needs two manual nudges.** Keycloak only
  bootstraps its admin user on first start, so `keycloak-config` fails with
  `Couldn't login using either the "admin" or "tmpadmin" accounts` until the
  Keycloak pod is restarted. LiteLLM needs a restart too, or `litellm-config`
  hangs creating its team against an empty schema.

## Verifying an install

- `https://app.$BASE_DOMAIN` serves the login page (200, publicly trusted cert).
- Login with the GitHub App completes.
- A conversation gets a `runtime-*` pod, an ingress at `<id>-runtime.$BASE_DOMAIN`,
  and an agent reply. (Blocked until the wildcard cert path above is validated.)

## Teardown

Roughly $0.70–$0.85/hr: EKS control plane ($0.10/hr) + 3× m5.xlarge (~$0.58/hr) +
the NLB + EBS. Tear down when finished.

```bash
export CLUSTER=openhands-eks-install REGION=us-east-1 \
       BASE_DOMAIN=oh.example.com HOSTED_ZONE_ID=ZXXXXXXXXXXXXX
./eks-install/delete-cluster.sh
```

The script uninstalls the releases (releasing the NLB and EBS volumes), removes
the wildcard CNAME, deletes the cluster, and lists any orphaned volumes tagged for
the cluster so you can delete them by hand. Confirm no `elb`/`elbv2` load balancer
survived (`aws elbv2 describe-load-balancers` / `aws elb describe-load-balancers`)
— a stranded one keeps charging and can block a later VPC cleanup.
