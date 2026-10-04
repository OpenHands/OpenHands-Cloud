---
name: aks-install
description: Install and validate OpenHands Enterprise with Helm on a dedicated Azure AKS evaluation cluster. Use for Azure-specific installation reproductions, Azure Disk workspace persistence, ingress/TLS, Sysbox compatibility, and conversation or automation acceptance checks.
---

# AKS test Helm install

Use a dedicated evaluation resource group and isolated kubeconfig. Keep customer infrastructure choices explicit: subscription, region, resource group, network/API access, DNS zone ownership, TLS path, identity provider, storage backend and licensed chart source. Follow the current [Enterprise Helm guide](https://docs.openhands.dev/enterprise/k8s-install/installation) for shared chart configuration; this skill adds the Azure decisions and live checks.

Use this over `local-kind-install` when a reproduction needs real Azure Disk
binding/expansion, Azure load-balancer behavior or trusted public HTTPS, and the
target provider is Azure/AKS. KinD remains suitable for ordinary chart work.

**What KinD cannot reproduce** (verified on this AKS evaluation):

- Azure Disk CSI provisioning and attachment to a Sysbox user-namespace pod.
- Persistent workspace contents after reattachment to another sandbox node.
- Volume and filesystem expansion from 1Gi to 2Gi.
- A real Azure LoadBalancer, wildcard DNS and trusted TLS.
- AKS autoscaling with the provider's actual node image/containerd configuration.

## Non-obvious AKS setup (read this first)

- **Check subscription access separately from Entra/DevOps login.** Contributor
  was sufficient for the tested managed-VNet deployment, but cannot create Azure
  role assignments. Custom networks and workload identities need separate checks.
- **Check regional and VM-family quota separately.** The tested D4s_v3 SKU was
  available regionally while all availability zones were restricted. Do not copy
  zonal placement from another provider.
- **Use Azure Disk CSI explicitly.** The tested class is `managed-csi`, with
  `WaitForFirstConsumer`, expansion enabled and RWO. Do not copy `standard-rwo`,
  gp3 classes or EBS IRSA setup. Set the runtime workspace class as well as
  application persistence.
- **The Azure load balancer supplies an IP.** Use a wildcard A record, rather
  than the EKS NLB hostname/CNAME path. DNS may remain with an existing provider.
- **A Ready Sysbox installer is insufficient.** This node image requires the
  correction below on every new sandbox node, including autoscaled nodes.

## Ask the user before starting

Resolve choices that have not already been authorized:

- Azure subscription, dedicated resource group, region and cluster name.
- Base domain, DNS ownership and certificate issuance/renewal path.
- GitHub App credentials and the base domain used by the official helper.
- LLM credentials provided through a private file/environment and chart access.
- Managed or existing network, API access path and intended storage backend.

Refresh Azure authentication if necessary, then inspect `az account list --all`.
Check providers Compute, ContainerService, Network, Storage and ManagedIdentity;
inspect total/family vCPU quota and SKU restrictions before provisioning.

## Sequence

Substitute approved values. Every Azure call pins the subscription; every
kubectl/Helm call uses the isolated kubeconfig. These commands create billable
resources. Reuse an existing evaluation instead of creating a duplicate.

```bash
export SUBSCRIPTION=<subscription-id> RESOURCE_GROUP=<evaluation-group>
export REGION=<quota-checked-region> CLUSTER=<cluster-name>
export BASE_DOMAIN=<base-domain> NAMESPACE=openhands
export KUBECONFIG=<dedicated-kubeconfig-path>

# 1. resource group, platform pool and separate Ubuntu sandbox pool
az group create --subscription "$SUBSCRIPTION" --name "$RESOURCE_GROUP" \
  --location "$REGION"
az aks create --subscription "$SUBSCRIPTION" --resource-group "$RESOURCE_GROUP" \
  --name "$CLUSTER" --location "$REGION" --kubernetes-version 1.35 --tier free \
  --nodepool-name platform --node-count 2 --node-vm-size Standard_D4s_v3 \
  --os-sku Ubuntu --node-osdisk-type Managed --node-osdisk-size 64 \
  --nodepool-labels workload=openhands-platform --enable-managed-identity \
  --network-plugin azure --network-plugin-mode overlay --network-policy calico \
  --generate-ssh-keys
az aks nodepool add --subscription "$SUBSCRIPTION" \
  --resource-group "$RESOURCE_GROUP" --cluster-name "$CLUSTER" \
  --name sandbox --mode User --node-count 1 --node-vm-size Standard_D4s_v3 \
  --os-sku Ubuntu --node-osdisk-type Managed --node-osdisk-size 64 \
  --labels workload=openhands-sandbox sysbox-install=yes \
  --enable-cluster-autoscaler --min-count 1 --max-count 2
az aks get-credentials --subscription "$SUBSCRIPTION" \
  --resource-group "$RESOURCE_GROUP" --name "$CLUSTER" --file "$KUBECONFIG"

# 2. inspect actual node versions and Azure Disk expansion support
kubectl get nodes -o wide
kubectl get storageclass managed-csi -o yaml

# 3. install official Sysbox manifest, inspect the containerd registration,
#    then prove a hostUsers:false Sysbox pod can mount/write an Azure Disk.
#    Apply the reviewed per-node correction below only if this defect occurs.

# 4. Traefik, wildcard DNS and trusted certificate
helm repo add traefik https://traefik.github.io/charts
helm repo update traefik
helm upgrade --install traefik traefik/traefik --version 41.6.0 \
  --namespace traefik --create-namespace -f aks-install/traefik-values.yaml
kubectl get service traefik --namespace traefik
# Create *.$BASE_DOMAIN A -> external IP. Provision wildcard TLS Secret.

# 5. create installation Secrets using the current Enterprise field contract;
#    adapt the companion values, including hostnames and existingSecret refs.
helm upgrade --install openhands <licensed-chart-source> --version 0.74.0 \
  --namespace "$NAMESPACE" --create-namespace -f <adapted-values.yaml> --timeout 10m
# Provision the session object-store bucket, then verify login and conversation.

# 6. after baseline success, enable optional automation with the original values
helm upgrade openhands <licensed-chart-source> --version 0.74.0 \
  --namespace "$NAMESPACE" -f <adapted-values.yaml> \
  -f <adapted-values-automation.yaml> --timeout 10m
```

Companion files live in `aks-install/`. The versions and scope of the source
evaluation are recorded in `aks-install/evaluation-notes.md`. Use `create-cluster.sh` for the
cluster/pool setup above. Render `values.yaml.tmpl` and
`values-automation.yaml.tmpl` with `envsubst '${BASE_DOMAIN}'`; the restricted
variable list preserves the runtime URL's `{runtime_id}` and other literals.
`bootstrap-secrets.py --github-credentials <private-json-file>` reads
`ANTHROPIC_API_KEY` from the environment and retains existing Secrets.
The credential JSON must contain `id`, `slug`, `client_id`, `client_secret`,
`pem` and `webhook_secret` from the dedicated GitHub App.

Install trusted wildcard TLS into Secret `openhands-wildcard-tls` in namespace
`openhands` before enabling ingress. The evaluation used bring-your-own TLS;
automatic renewal is outside this helper's scope. Apply
`create-session-bucket.yaml` after RustFS is Ready. The chart's license/registry
credentials follow the current Enterprise install guide and are not included.

Storage manifests use namespace `openhands-runtimes`. The second-node reader, expansion and observer templates
require `SECOND_SANDBOX_NODE` and `envsubst '${SECOND_SANDBOX_NODE}'` before
applying. Wait for the first writer to complete before moving the RWO volume.
After expansion, do not reapply the original 1Gi PVC manifest. The observer pod
holds the mount while Kubernetes finishes updating PVC capacity.
`automation-smoke-request.json` is the temporary prompt-preset request.
These helpers are generalized from the successful evaluation; the new package
has not been rerun as a fresh installation.

## Constraints that bite (do not “fix” these away)

### Azure-specific

### Chart configuration

1. Create the resource group and AKS cluster with the selected subscription pinned on Azure commands. Fetch credentials into a dedicated file, then use `KUBECONFIG=<file>` on every Helm/kubectl invocation. Check actual nodes and `kubectl auth can-i`; Azure provisioning access and Kubernetes permissions are separate checks.
2. Keep application services on the platform pool via `global.scheduling.affinity`. Put `sysbox-install=yes` in the AKS sandbox pool's persistent labels, not just on an individual node. Confirm new sandbox nodes inherit the label.
3. Inspect `managed-csi` before selecting it for PostgreSQL, object-store and workspace PVCs. Check provisioner, binding mode, expansion, reclaim policy and actual pod mounts. Do not copy EKS CSI installation or IRSA annotations onto AKS.
4. Deploy Traefik with Azure-compatible values. Its LoadBalancer yielded an IPv4 address, so create a wildcard A record for the chosen flat base domain. DNS need not be hosted in Azure; the evaluation used an existing Route 53 zone.
5. Obtain a trusted wildcard certificate using DNS-01 or the documented bring-your-own-certificate path. A certificate for `*.example.com` covers `app.example.com`, `auth.example.com` and `{id}-runtime.example.com`, not a deeper hostname. Record renewal and Secret-update ownership if provisioning manually.
6. Use a dedicated GitHub App and the current helper's flat layout; the callback is `https://auth.<base>/realms/allhands/broker/github/endpoint`. Validate a real login round trip.
7. Create separate Secrets for application infrastructure, GitHub App and provider credentials. Share the same value between the Runtime API `default-api-key` and app `sandbox-api-key` Secrets. Do not put credentials in tracked values or command arguments.
8. Chart 0.74.0 does not expose an Azure Blob filestore: its supported backend values are S3-compatible or GCS. The evaluation used `rustfs.enabled: true`, `minio.enabled: false`, `filestore.type: s3`, endpoint `http://<release>-rustfs-svc:9000`, and an existing Secret containing both AWS and RustFS field names. Provision the conversation bucket before first use. Do not describe this as native Azure Blob support.
9. On Traefik standard Ingress, use flat subdomain runtime routing: `RUNTIME_BASE_URL=runtime.<base>`, `RUNTIME_URL_SEPARATOR=-`, and app `RUNTIME_URL_PATTERN=https://{runtime_id}-runtime.<base>`. Set `RUNTIME_CLASS=sysbox-runc`, `SET_HOST_USERS=true` and the selected workspace StorageClass.
10. Keep runtime callbacks configured through Helm: `global.agentServerEnv.OH_WEBHOOKS_0_BASE_URL=https://app.<base>/api/v1/webhooks` and `OH_ALLOW_CORS_ORIGINS_0=https://app.<base>`. Verify a cold runtime and existing warm runtimes separately.

### Sysbox registration: live AKS blocker

On AKS 1.35.8 with Ubuntu 24.04.5 LTS and containerd 2.3.3-2, official Sysbox installer v0.7.1-0 at manifest revision `f7c43922753bd167085c595b8ca285a03986f823` reported success and set `sysbox-runtime=running`, but AKS containerd did not know the handler. A real `runtimeClassName: sysbox-runc`, `hostUsers: false` pod failed with `FailedCreatePodSandBox`.

The installer wrote two tables beneath `plugins."io.containerd.grpc.v1.cri".containerd.runtimes.sysbox-runc`; this AKS node uses `plugins."io.containerd.cri.v1.runtime"`. See [upstream issue 997](https://github.com/nestybox/sysbox/issues/997).

For the explicitly approved evaluation workaround, back up the host containerd config, parse original/candidate TOML, move only those two Sysbox headers to the current plugin key, preserve all other AKS runtime settings, and restart containerd on the affected sandbox node. Verify config headers, service active, node Ready and a real user-namespace pod afterward. Restarting containerd can end the kubectl exec session before final output, so the command's output alone does not prove success.

The autoscaler subsequently created a second sandbox node, reproduced this exact failure, and prevented the first automation run from starting. Installer readiness and node labels are insufficient. Obtain a maintained upstream/provider solution for customer unattended installs; the manual correction must not be presented as automatically durable across scale-out, node reimage or upgrades. Do not silently change the node's container runtime as a workaround.

## Acceptance checks

- Public app, Canvas, auth OIDC discovery and Runtime API health routes must have trusted HTTPS.
- Sign in and run a normal conversation that executes `pwd`, writes a marker into the workspace, reads it back and returns a completed answer. Confirm sandbox READY and node placement. The tested Default model profile worked without an override on chart 0.74.0.
- Test Azure Disk provisioning, user-namespace mount read/write, persisted data from a second pod/node, and PVC expansion on a disposable test volume. Keep cross-zone tests distinct from same-region/no-zone tests. Never fill a live database disk to test capacity.
- Enable the automation subchart after the normal conversation passes. Configure independent database, service-key and webhook Secrets, dedicated package storage, and `createDatabaseUser: true` for the embedded PostgreSQL evaluation. `automationBaseUrl` is the public origin; `automationService.url` includes `/api/automation`. Set `filestore.createBucket: true` for its dedicated package bucket when appropriate.
- Verify `/api/automation/health`, then create a temporary prompt preset through `POST /api/automation/v1/preset/prompt`, dispatch once and disable the schedule. Poll `GET /api/automation/v1/<automation-id>/runs`. Verify actual commands, marker file, completed model answer and completion callback; a green badge alone is insufficient. Remove the temporary automation or leave it explicitly disabled after retaining evidence.
- Record chart/app/runtime versions, exact storage and network choices, failed baseline behavior, approved workarounds, test IDs/results and unresolved production concerns. Do not claim tests passed that have not completed.



## Teardown

Keep evaluation resources until the user is finished. Before authorized cleanup, inventory the dedicated resource group, AKS node resource group, public IP/load balancer, managed disks/snapshots, DNS records and certificate renewal configuration. Remove only this evaluation's resources and verify cloud inventory afterward; DNS hosted outside Azure requires separate cleanup. PVC reclaimPolicy Delete is not a backup policy.
