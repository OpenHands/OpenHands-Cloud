---
name: aks-install
description: Stand up an OpenHands Enterprise Helm install on a dedicated Azure AKS cluster using aks-install/, standard runc sandboxes, Traefik, trusted wildcard TLS, and Azure Disk CSI. Use when a reproduction needs real Azure storage, ingress or node-pool behaviour that KinD cannot provide, and the target environment is Azure/AKS.
---

# AKS test Helm install

Use this over `local-kind-install` when the reproduction needs real Azure Disk
provisioning, reattachment or expansion, a real load balancer, or publicly trusted
TLS. KinD remains faster and adequate for ordinary chart work.

Everything lives in `aks-install/`. Use `create-cluster.sh` for the cluster and
node pools, `values.yaml.tmpl` for Helm, and `bootstrap-secrets.py` for Secrets.
Follow the [Enterprise Helm guide](https://docs.openhands.dev/enterprise/k8s-install/installation)
for licensed registry access and shared chart configuration. The revised package
has not been run end to end on a fresh cluster.

## Non-obvious AKS setup (read this first)

- **Azure login is not subscription access.** Confirm the subscription and the
  deployment identity's permissions. Creating role assignments requires additional
  permissions beyond Contributor.
- **Check regional and VM-family quota separately.** Also check SKU availability
  and zone restrictions before selecting node sizes or placement.
- **Use Azure Disk CSI explicitly.** Set `managed-csi` for workspaces, PostgreSQL
  and in-cluster object storage. Inspect its binding mode, expansion support and
  the node's disk-attachment limits; do not copy EKS CSI or IRSA setup.
- **The load balancer supplies an IP.** Create a wildcard A record pointing at the
  Traefik Service's external IP. DNS can remain with an existing provider.
- **Use the default runc runtime.** Do not install Sysbox, label nodes for its
  installer or modify containerd. The values template sets an empty
  `RUNTIME_CLASS`, `SET_HOST_USERS: "false"`, and a dedicated sandbox selector.
  Docker/Compose inside the sandbox is unavailable; runc does not provide Sysbox's
  additional isolation.

## Ask the user before starting

Resolve choices that have not already been authorized:

- Azure subscription, resource group, region and cluster name.
- Base domain, DNS ownership and certificate issuance/renewal path.
- GitHub App credentials and the base domain used to create it.
- LLM credentials in a private file/environment, and licensed chart access.
- Network/API access requirements, storage backend and expected sandbox capacity.

If Azure authentication has expired, ask the user to refresh it. Check
`az account list --all`, provider registration, quotas and SKU restrictions before
provisioning. Use a dedicated kubeconfig so commands cannot target another cluster.

## Sequence

Substitute approved values. Every Azure command pins the subscription; every
kubectl/Helm command uses the dedicated kubeconfig. Reuse an existing test cluster
rather than creating duplicate billable resources.

```bash
export SUBSCRIPTION=<subscription-id> RESOURCE_GROUP=<dedicated-resource-group>
export REGION=<region> CLUSTER=<cluster-name>
export BASE_DOMAIN=<base-domain>
export KUBECONFIG=<dedicated-kubeconfig-path>

# 1. cluster and separate Ubuntu platform/sandbox pools
./aks-install/create-cluster.sh
kubectl get nodes -o wide
kubectl get storageclass managed-csi -o yaml

# 2. Traefik and wildcard DNS
helm repo add traefik https://traefik.github.io/charts
helm repo update traefik
helm upgrade --install traefik traefik/traefik --version 41.6.0 \
  --namespace traefik --create-namespace -f aks-install/traefik-values.yaml
kubectl get service traefik --namespace traefik
# Create *.$BASE_DOMAIN A -> external IP with your DNS provider.
# Do not add *.app.$BASE_DOMAIN: it blocks wildcard synthesis for app.$BASE_DOMAIN.

# 3. installation Secrets; ANTHROPIC_API_KEY is provided privately
# Obtain a trusted wildcard certificate from a CA using DNS-01 before this step.
python3 aks-install/bootstrap-secrets.py --github-credentials <private-json-file>
kubectl -n openhands create secret tls openhands-wildcard-tls \
  --cert=<fullchain-file> --key=<private-key-file>

# 4. render the values and install from the licensed chart source
# Use explicit output paths; do not overwrite the templates.
envsubst '${BASE_DOMAIN}' < aks-install/values.yaml.tmpl > /tmp/aks-values.yaml
helm upgrade --install openhands <licensed-chart-source> --version 0.74.0 \
  --namespace openhands --create-namespace -f /tmp/aks-values.yaml --timeout 10m

# 5. wait for RustFS, then create the conversation bucket
kubectl -n openhands wait pod --for=condition=Ready \
  --selector=app.kubernetes.io/name=rustfs --timeout=10m
kubectl apply -f aks-install/create-session-bucket.yaml
kubectl -n openhands wait job/create-session-bucket \
  --for=condition=Complete --timeout=5m
```

Assign renewal ownership when using manually provisioned TLS. The helpers use
the fixed namespaces `openhands` and `openhands-runtimes`. The GitHub credential JSON must contain `id`, `slug`,
`client_id`, `client_secret`, `pem` and `webhook_secret`; the bootstrap helper reads
`ANTHROPIC_API_KEY` from the environment and retains existing Secrets.

Open `https://app.$BASE_DOMAIN`, sign in with GitHub and start a conversation.
Enable optional automations only after the baseline works: render
`values-automation.yaml.tmpl` with `envsubst '${BASE_DOMAIN}'`, then upgrade with
both the original values and that overlay:

```bash
envsubst '${BASE_DOMAIN}' < aks-install/values-automation.yaml.tmpl > /tmp/aks-automation-values.yaml
helm upgrade openhands <licensed-chart-source> --version 0.74.0 \
  --namespace openhands -f /tmp/aks-values.yaml -f /tmp/aks-automation-values.yaml --timeout 10m
```

Preserve the four runc settings on
every upgrade; later overlays must not restore the chart's Sysbox defaults.

## Constraints that bite (do not "fix" these away)

- **Separate platform and sandbox placement.** Keep services on
  `workload=openhands-platform`. Put `workload=openhands-sandbox` in the sandbox
  node pool's persistent labels so replacement and autoscaled nodes inherit it.
  Match custom pool taints with runtime tolerations.
- **Retain the runc overrides.** `RUNTIME_CLASS: ""` uses the node's default
  runtime, and `SET_HOST_USERS: "false"` leaves the host-user override unset.
  Keep `RUNTIME_NODE_SELECTOR` and `RUNTIME_TOLERATIONS` from the template.
- **Azure Disk workspaces are RWO.** Wait for the first writer pod to be removed
  before attaching its volume to another node. Account for disk topology and
  attachment limits when sizing the sandbox pool.
- **TLS and runtime routing must agree.** Use flat subdomain routing with Traefik:
  `RUNTIME_URL_SEPARATOR: "-"` and
  `RUNTIME_URL_PATTERN: https://{runtime_id}-runtime.<base>`. Provide a wildcard
  certificate; HTTP-01 cannot issue one. Do not select path routing for standard
  Traefik Ingress.
- **The GitHub callback must match Keycloak.** The flat-layout callback is
  `https://auth.<base>/realms/allhands/broker/github/endpoint`. Verify an actual
  login round trip; a redirect alone does not prove correct configuration.
- **Application and Runtime API keys must match.** Generate one value for the
  `default-api-key` and `sandbox-api-key` Secrets. Keep credentials out of tracked
  values and command arguments.
- **Azure Blob is not a filestore backend in chart 0.74.0.** The template uses
  RustFS with Azure Disk persistence and an existing credential Secret. Create
  the conversation bucket before starting conversations.
- **Keep callbacks configured through Helm.** Retain `OH_WEBHOOKS_0_BASE_URL` and
  `OH_ALLOW_CORS_ORIGINS_0` from the template.

## Verifying an install

- App, auth and Runtime API routes use publicly trusted HTTPS.
- GitHub login completes and a conversation executes `pwd`, writes a workspace
  file and reads it back.
- The sandbox pod runs in `openhands-runtimes` on the dedicated sandbox pool,
  with no `runtimeClassName` or `hostUsers` override.
- Stop the conversation runtime and reopen it; the workspace file persists.

For optional Azure Disk reattachment and expansion checks, follow
[`aks-install/README.md`](../../aks-install/README.md). These checks and the
optional automation helpers still need validation on a fresh runc installation.

The optional automation overlay uses the service/webhook Secrets created by the
bootstrap helper and configures package storage. After upgrading, check the
Deployment and Pod readiness in `openhands`. An end-to-end automation run remains
to be validated; healthy pods alone do not establish that it works.

## Teardown

Keep resources until the user is finished. Before authorized cleanup, save the
node resource group and inventory both groups; confirm this is a dedicated group.
Do not delete a shared resource group.

```bash
NODE_RESOURCE_GROUP=$(az aks show --subscription "$SUBSCRIPTION" \
  --resource-group "$RESOURCE_GROUP" --name "$CLUSTER" --query nodeResourceGroup -o tsv)
az resource list --subscription "$SUBSCRIPTION" --resource-group "$RESOURCE_GROUP" -o table
az resource list --subscription "$SUBSCRIPTION" --resource-group "$NODE_RESOURCE_GROUP" -o table
helm uninstall openhands --namespace openhands
# Stop conversation runtimes through OpenHands before deleting their claims.
# After confirming no data needs to be retained, delete only this install's PVCs.
kubectl get pvc -n openhands
kubectl get pvc -n openhands-runtimes
kubectl delete pvc <confirmed-claim-name> --namespace <claim-namespace>
helm uninstall traefik --namespace traefik
az aks delete --subscription "$SUBSCRIPTION" --resource-group "$RESOURCE_GROUP" --name "$CLUSTER"
# Only for the dedicated group created for this installation:
az group delete --subscription "$SUBSCRIPTION" --name "$RESOURCE_GROUP"
```

For noninteractive cleanup, add `--yes` to the Azure delete commands only after
the user has approved the exact targets. Remove this install's wildcard DNS record through its DNS provider. Confirm both
resource groups are gone and check for retained managed disks, snapshots and public
IPs against the saved inventory. `reclaimPolicy: Delete` is not a backup.
