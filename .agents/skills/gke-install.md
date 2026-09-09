---
name: gke-install
description: Stand up an OpenHands Enterprise Helm install on a throwaway GKE cluster (ghcr chart source, Traefik, cert-manager, real Let's Encrypt TLS, embedded Postgres on a standard-rwo PVC) using gke-install/values.yaml.tmpl. Use when a reproduction needs real cloud behaviour that KinD cannot provide - PVC expansion, enforced volume capacity, real ingress and TLS - or when reproducing a customer helm-install issue that KinD has already failed to reproduce faithfully.
---

# GKE test Helm install

Use this over `local-kind-install` only when the reproduction needs something KinD
genuinely cannot do. KinD is faster, free, and adequate for most chart work.

**What KinD cannot reproduce** (all measured, not assumed):

- `df` inside the Postgres pod reports the node filesystem (621G), not the volume,
  because `local-path` is a host bind mount. On GKE it reports the real PD.
- Volume capacity is not enforced, so a "full disk" cannot be reproduced.
- `kubectl patch pvc` is a **silent no-op**. `local-path` accepts the patch and
  changes nothing: `requested` moves, `capacity` does not, no conditions appear.
  Patching `allowVolumeExpansion: true` onto that class makes it look like it works.
- Real ingress, real DNS, and publicly trusted certificates.

## Ask the user before starting

- **GCP project** and **cluster name**. Never invent these.
- **Base domain**, which must be a Cloud DNS zone in that project so records can be
  created without a manual step. Check with `gcloud dns managed-zones list`.
- **GitHub App credentials** from `scripts/create_github_app`, plus the base domain
  it was created with. See the callback section below - this is the single most
  common cause of a broken login.
- **Anthropic API key**, placed in a file or env var by the user, not pasted in chat.

`gcloud auth login` is interactive and cannot be driven from a tool call. When the
token has expired, ask the user to run it.

## Sequence

Substitute your own values. Every command pins `--project` and `--context`.

```bash
P=<project>; CLUSTER=<name>; ZONE=us-central1-a
BASE_DOMAIN=<zone in Cloud DNS>; NAMESPACE=openhands
CTX=gke_${P}_${ZONE}_${CLUSTER}

# 1. cluster (~5.5 min)
gcloud container clusters create "$CLUSTER" --project "$P" --zone "$ZONE" \
  --num-nodes 3 --machine-type e2-standard-4 --disk-type pd-balanced --disk-size 100 \
  --labels owner=<you>,purpose=<why>
gcloud container clusters get-credentials "$CLUSTER" --project "$P" --zone "$ZONE"

# 2. confirm the storage class supports expansion before relying on it
kubectl --context "$CTX" get sc standard-rwo \
  -o custom-columns=NAME:.metadata.name,PROV:.provisioner,EXPAND:.allowVolumeExpansion

# 3. traefik
helm repo add traefik https://traefik.github.io/charts && helm repo update traefik
helm --kube-context "$CTX" upgrade --install traefik traefik/traefik \
  --version 41.3.0 --namespace traefik --create-namespace \
  --set service.type=LoadBalancer --timeout 5m
LB_IP=$(kubectl --context "$CTX" -n traefik get svc traefik \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# 4. one wildcard record covers every host. See the DNS warning below.
gcloud dns record-sets create "*.${BASE_DOMAIN}." --zone <zone-name> --project "$P" \
  --type A --ttl 300 --rrdatas "$LB_IP"

# 5. cert-manager + issuer
helm repo add jetstack https://charts.jetstack.io && helm repo update jetstack
helm --kube-context "$CTX" upgrade --install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace --set crds.enabled=true --timeout 6m
# then apply a ClusterIssuer named "letsencrypt" with an http01 solver on
# ingressClassName: traefik

# 6. secrets - reuse local-kind/create-secrets.sh, pointed at this context
# 7. install (ghcr chart and images are both public, no pull secret needed)
helm --kube-context "$CTX" -n "$NAMESPACE" upgrade --install openhands \
  oci://ghcr.io/openhands/helm-charts/openhands --version <version> \
  -f <rendered values> --timeout 15m
```

`gke-install/values.yaml.tmpl` is the values file. Render it with `envsubst` after
exporting `APP_HOST`, `AUTH_HOST`, `RUNTIME_API_HOST`, `BASE_DOMAIN`, `NAMESPACE`
and `LLM_MODEL`.

## Constraints that bite (do not "fix" these away)

- **Never add a deeper wildcard DNS record.** A wildcard matches at *any* depth when
  no closer name exists, so `*.$BASE_DOMAIN` already covers `auth.app.$BASE_DOMAIN`.
  Adding `*.app.$BASE_DOMAIN` creates an empty non-terminal node at `app.$BASE_DOMAIN`,
  and an existing node **blocks wildcard synthesis** - so the app's own hostname
  starts returning NODATA and the site becomes unreachable while the service is
  perfectly healthy. Negative caching means it persists for the zone's SOA minimum
  (300s) after you undo it.

- **The GitHub App callback is derived from the base domain you passed to the
  script**, not from your ingress host. `create_github_app` sets
  `url: https://app.{base_domain}` and registers exactly one callback,
  `https://auth.{base_domain}/realms/allhands/broker/github/endpoint` for the
  default flat layout. Keycloak's hostname must match that string exactly.

  To recover the base domain from an existing app, mint an app JWT and read
  `external_url` from `GET /app` - it is `https://app.{base_domain}`. The API does
  **not** expose callback URLs, so this is the only way short of the settings page.

- **Do not probe GitHub to test a redirect_uri.** Hitting
  `/login/oauth/authorize` with a bogus `redirect_uri` returns HTTP 302 to the login
  page just like a valid one; validation is deferred until after authentication. A
  302 proves nothing. Only a real login round trip does.

- **`RUNTIME_ROUTING_MODE: path` requires ingress-nginx or Gateway API.** On Traefik
  with standard ingresses, runtime-api raises
  `create_ingress_manifest failed because path mode is only supported with
  ingress-nginx` and every sandbox fails to start. Use subdomain routing instead
  (`RUNTIME_URL_SEPARATOR: "-"`, `RUNTIME_URL_PATTERN:
  https://{runtime_id}-runtime.<base>`), which is what the values template does.

  Subdomain routing needs a **wildcard certificate**, since each sandbox gets its own
  hostname. HTTP-01 cannot issue wildcards, so this needs a DNS-01 solver against
  Cloud DNS, and `runtime-api.env.RUNTIME_CERT_SECRET` pointed at the resulting
  secret. **This part is not yet validated** - conversations were never confirmed
  working on GKE.

- **`postgresql.primary.resourcesPreset` is inert on its own.** The umbrella chart
  pins `postgresql.primary.resources`, and Bitnami's template prefers explicit
  resources over any preset, so the container gets a 1Gi limit no matter which preset
  is named. Add `resources: null` to delete the pinned block and let the preset apply.
  Measured: 1Gi without it, 3072 MiB with `large`, 6144 MiB with `xlarge`. The
  presets always include `ephemeral-storage`; the pinned block never does, which is
  the quickest way to tell which one is in force.

- **`shared_buffers = 1GB` in a 1Gi limit OOM-kills Postgres under load**, and looks
  perfectly healthy at idle (~60 MiB), because shared memory pages are charged to the
  cgroup as they are touched. `kubectl get pods` still reports `1/1 Running`.

- **Leaving `persistence.size` unset yields 8Gi**, the Bitnami subchart default. The
  umbrella chart never sets a size. This is deliberate in the values template because
  it reproduces what installs inherit.

- **A StatefulSet's `volumeClaimTemplates` is immutable.** Adding `size:` and
  upgrading fails whole and applies *nothing else in that upgrade either*. Grow the
  PVC first with `kubectl patch pvc`, then
  `kubectl delete sts <release>-postgresql --cascade=orphan` and upgrade. Pods survive
  - verified by unchanged pod UID and `restarts=0`.

- **MinIO deadlocks on upgrade.** Its Deployment uses `RollingUpdate` with an RWO PVC,
  so the new pod cannot attach (`Multi-Attach error`) until the old one is deleted by
  hand. It wants `Recreate`.

- **`replicated.enabled` defaults to true**, and the SDK's license is injected only at
  pull time by `registry.replicated.com`. On a ghcr install the pod crashloops with
  `either license in the config file or integration license id must be specified`.
  The values template sets it false.

- **Recreating the Postgres database breaks two things that need a manual nudge.**
  Keycloak only bootstraps its admin user on first start, so `keycloak-config` fails
  with `Couldn't login using either the "admin" or "tmpadmin" accounts` until the
  Keycloak pod is restarted. LiteLLM needs a restart too, or `litellm-config` hangs
  creating its team against an empty schema.

- **The kubeconfig will contain prod contexts.** `get-credentials` also switches the
  ambient context, so a later unpinned `kubectl` can silently read the wrong cluster -
  or worse. Pin `--context` on everything, and remember that the pod and namespace
  names are identical across clusters, which makes a wrong reading look plausible.

## Teardown

Billing is roughly $0.50 to $0.60 per hour for 3 nodes plus the load balancer, so
tear down when finished.

The sandbox project holds a dozen other people's clusters with similar names. Print
the exact match and the keep-list before deleting anything:

```bash
gcloud container clusters list --project "$P" --format='value(name,location,status)' \
  | awk -v c="$CLUSTER" '$1==c {print "TARGET: "$0} $1!=c {print "KEEP:   "$0}'
```

```bash
gcloud container clusters delete "$CLUSTER" --project "$P" --zone "$ZONE"
gcloud dns record-sets delete "*.${BASE_DOMAIN}." --zone <zone-name> --project "$P" --type A
```

**Deleting the cluster does not reclaim its persistent disks.** Every PVC leaves an
unattached PD behind, still billing and easy to miss. This is why the create command
passes `--labels` - the disks inherit `goog-k8s-cluster-name`, which makes ownership
unambiguous when the zone is full of other people's orphans:

```bash
gcloud compute disks list --project "$P" \
  --filter="labels.goog-k8s-cluster-name=$CLUSTER" --format='value(name,sizeGb)'
# then delete each by exact name
gcloud compute disks delete <name> --project "$P" --zone "$ZONE"
```

Finally confirm the load balancer address was released, since a stranded forwarding
rule keeps charging:

```bash
gcloud compute forwarding-rules list --project "$P" --filter="IPAddress=$LB_IP"
```
