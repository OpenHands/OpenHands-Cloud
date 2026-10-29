# OpenHands Cloud Helm Chart

This Helm chart deploys the complete OpenHands stack, including all required dependencies. It's designed to be a one-stop solution for deploying OpenHands in a Kubernetes environment.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.2.0+
- Ingress controller (recommended: Traefik)
- A TLS solution for certificates (recommended: cert-manager)

### Hardware prerequisites

- Profile the application's resource usage (CPU, memory) to establish the minimum required specifications for the cluster.

## Configuration

See the [values.yaml](values.yaml) file for the full list of configurable parameters.
Make sure to update all values marked with "REQUIRED" comments.

### Organization condenser defaults

Set `orgDefaults.condenser.maxTokens` to add a token-based condensation threshold for applicable OpenHands organization settings. Any positive integer is accepted. Condensation triggers on the smaller of this value and the agent LLM's effective input limit, so a value above that limit has no effect; it does not change the model context window, and event-count condensation may still occur first.

These values only take effect from the app version that reads the `OPENHANDS_ORG_DEFAULTS_CONDENSER_*` environment variables, which is tracked in [OpenHands/enterprise#337](https://github.com/OpenHands/enterprise/pull/337). The current chart `appVersion` does not include that reader, so on it these values render into the pod and do nothing.

```yaml
orgDefaults:
  condenser:
    maxTokens: 200000
    applyToExisting: true
    overwriteExisting: true
```

New applicable OpenHands org settings receive the configured value. Existing org rows are only updated when `applyToExisting: true`; existing non-null values are only replaced when `overwriteExisting: true`. Missing and JSON-null `condenser.max_tokens` values are treated as unset.

Reconciliation is triggered from the app server's startup lifespan, so it runs once per `saas_server` worker process every time one starts, and it stays armed for as long as these values remain in the release. The maintenance CronJobs that share this env block never trigger it, because they run standalone `python -m` entrypoints that do not start the app. A concurrent database lock serializes overlapping runs, so repeated runs converge on the same result rather than conflicting.

The practical consequence is that this is not a rollout-scoped, one-time operation. If `overwriteExisting: true` stays in a site values file, a pod restart, rollout, HPA scale-up, or node eviction re-applies it and overwrites org-admin UI changes made since the last start. Remove `applyToExisting`/`overwriteExisting` once the rollout completes. Then remove `orgDefaults.condenser.maxTokens` as well if you want to stop future defaulting and reconciliation.

`overwriteExisting: true` is destructive for prior org-level `max_tokens` values. Take a database backup before enabling it. Removing `orgDefaults.condenser.maxTokens` later does not restore previous org-specific values; restore from backup or run corrective SQL if rollback is required.

### Email (Resend)

To enable organization invitation emails via Resend, set `resend.enabled: true` and create a Kubernetes secret named `resend-api-key` with key `resend-api-key` containing your Resend API key. The secret name can be overridden with `resend.auth.existingSecret`.

### Laminar ClickHouse diagnostics

When Laminar analytics is enabled, the chart applies bounded retention to
high-volume ClickHouse diagnostic tables such as `system.trace_log`. Replicated
installs expose this as **Analytics Configuration → ClickHouse Diagnostic Log
Retention**, defaulting to 3 days. See
[ClickHouse diagnostic log retention](../../docs/clickhouse-diagnostic-log-retention.md)
for cleanup commands and support-bundle details.

### TLS and Certificate Configuration

The chart supports two methods for TLS configuration:

1. **Standard TLS**: Enable with `tls.enabled: true`. This uses a certificate with the name format `app-all-hands-{env}-tls`.

2. **Wildcard Certificate**: Enable with `certificate.enabled: true`. This creates a cert-manager Certificate resource that can use a wildcard domain (e.g., `*.prod-runtime.all-hands.dev`). This is particularly useful for runtime environments where you need a wildcard certificate.

3. **TLSStore for Traefik**: Enable with `tlsStore.enabled: true`. This creates a Traefik TLSStore resource that configures the default certificate for Traefik to use. When combined with a wildcard certificate, this allows Traefik to use the wildcard certificate for all TLS connections.

For runtime environments, see the [values.runtime-example.yaml](values.runtime-example.yaml) file for an example configuration using a wildcard certificate and TLSStore.

An [example-values.yaml](example-values.yaml) file is also provided as a starting point
for your own configuration. This example file contains the minimum set of values you need
to override when deploying the chart with the default included services
(without using external data stores). Remember to update the domain names and other
environment-specific values in the example file before using it.

## Installation

### Initial setup

#### 1. Create the openhands namespace

If you want to use a different namespace, you'll need to change the `-n` option
in all the commands below.

```bash
kubectl create namespace openhands
```

#### 2. Create a secret for your LLM

We'll assume Anthropic here, but you can set any env vars you'll need to connect to your LLM,
including e.g. OpenAPI keys, or AWS keys for Bedrock models. You can use any env var names
you want--we'll reference them again below in our LiteLLM setup.

```bash
kubectl create secret generic litellm-env-secrets -n openhands \
    --from-literal=ANTHROPIC_API_KEY=<your-anthropic-api-key>
```

#### 3. Create required secrets

There are several databases and other services that need a secret or admin password to function.
We'll create a single `$GLOBAL_SECRET` to drive all of these, but we recommend using
[SOPS](https://github.com/getsops/sops) or another solution for managing Kubernetes secrets long-term.

If you are using your own LiteLLM instance, see the NOTE.

```bash

export GLOBAL_SECRET=`head /dev/urandom | tr -dc A-Za-z0-9 | head -c 32`

kubectl create secret generic jwt-secret -n openhands --from-literal=jwt-secret=$GLOBAL_SECRET

kubectl create secret generic keycloak-realm -n openhands \
  --from-literal=realm-name=allhands \
  --from-literal=server-url=http://keycloak \
  --from-literal=client-id=allhands \
  --from-literal=client-secret=$GLOBAL_SECRET \
  --from-literal=smtp-password=

kubectl create secret generic keycloak-admin -n openhands \
  --from-literal=admin-password=$GLOBAL_SECRET

kubectl create secret generic postgres-password -n openhands \
  --from-literal=username=postgres \
  --from-literal=password=$GLOBAL_SECRET \
  --from-literal=postgres-password=$GLOBAL_SECRET

kubectl create secret generic redis -n openhands \
  --from-literal=redis-password=$GLOBAL_SECRET

# NOTE: if you are using your own LiteLLM instance, then change $GLOBAL_SECRET to your LiteLLM API Key
kubectl create secret generic lite-llm-api-key -n openhands \
  --from-literal=lite-llm-api-key=$GLOBAL_SECRET

kubectl create secret generic admin-password -n openhands \
  --from-literal=admin-password=$GLOBAL_SECRET

# NOTE: these need to be the same value
# TODO: merge these two secrets
kubectl create secret generic default-api-key -n openhands \
  --from-literal=default-api-key=$GLOBAL_SECRET
kubectl create secret generic sandbox-api-key -n openhands \
  --from-literal=sandbox-api-key=$GLOBAL_SECRET
```

You should now have these secrets in the openhands namespace:

```bash
kubectl get secret -n openhands

NAME                  TYPE     DATA   AGE
default-api-key       Opaque   1      7s
jwt-secret            Opaque   1      44s
lite-llm-api-key      Opaque   1      28s
litellm-env-secrets   Opaque   1      2m8s
postgres-password     Opaque   3      39s
redis                 Opaque   1      35s
sandbox-api-key       Opaque   1      3s
```

#### 4. Create a helm values file

Copy the example-values.yaml file to a file name of your choice. For the purposes of this document we will call this file `site-values.yaml`

We will update this file in the following sections and there will likely be customizations for your environment (see comments in the file for more information on common changes).

### Enabling IDP Authentication

You'll need to set up GitHub, GitLab, and/or BitBucket as an auth provider. We're working on email-based
authentication as well.

#### GitHub

1. Create a GitHub App:

   - Go to your GitHub organization settings or personal settings.
   - Navigate to "Developer settings" > "GitHub Apps" > "New GitHub App".
   - In the "GitHub App name" field, enter a descriptive name (e.g., Openhands app).
   - Add your "Homepage URL" `https://openhands.example.com`.
   - Add the "Callback URL" `https://auth.openhands.example.com/realms/allhands/broker/github/endpoint`.
   - In "Permissions":

     - Open "Account permissions" and select "Access: Read-only" to "Email addresses".
     - In "Repository permissions" add "Access: Read and Write" to:

      - Actions
      - Contents
      - Commit Statuses
      - Issues
      - Pull Requests
      - Workflows

   - If you want to get webhooks:

     - Generate a webhook secret `export WEBHOOK_SECRET=head /dev/urandom | tr -dc A-Za-z0-9 | head -c 32`.
     - Check the "Active" checkbox.
     - Set the "Webhook URL" `https://openhands.example.com/integration/github/events`.
     - Go to "Permissions", click in "Organization permissions" and add "Access: Read-only" to "Events". Click in "Repository permissions" and add "Access: Read and Write" to "Webhooks".
     - Set the "Secret" to `$WEBHOOK_SECRET`.

   - Create the App.
   - Generate a private key which will download a private key file.
   - Note the App ID, Client ID, and Client Secret provided by GitHub. Remember to save the Client Secret and your private key, because it is only displayed once.

2. Create a GitHub App secret with the following structure:
   This secret contains the GitHub App configuration information from your GitHub account.
   You can create it using kubectl:

   ```bash
   kubectl create secret generic github-app -n openhands \
     --from-literal=app-id=<your-github-app-id> \
     --from-literal=webhook-secret=$WEBHOOK_SECRET \
     --from-literal=client-id=<your-github-client-id> \
     --from-literal=client-secret=<your-github-client-secret> \
     --from-file=private-key=<path-to-your-private-key-file>
   ```

3. Update site-values.yaml file:

   ```yaml
   github:
     # Set this to true if you are using GitHub as your identity provider
     enabled: true
   ```

#### GitLab

1. Create a GitLab Application:

   - Go to your GitLab Group.
   - Navigate to "Settings" > "Applications"
   - Set the "Redirect URI" to `https://auth.openhands.example.com/realms/openhands/broker/gitlab/endpoint`
   - Select the following scopes: api, read_user, write_repository, openid, email, profile
   - Note the Client ID and Client Secret provided by GitLab

2. Create a GitLab App secret:

   ```bash
   kubectl create secret generic gitlab-app -n openhands \
     --from-literal=client-id=<your-gitlab-client-id> \
     --from-literal=client-secret=<your-gitlab-client-secret> \
   ```

3. Update site-values.yaml file:

   ```yaml
   gitlab:
     # Set this to true if you are using GitLab as your identity provider
     enabled: true
   ```

#### BitBucket

1. Create a BitBucket OAuth Consumer:

   - Go to your Workspace Settings.
   - Select "OAuth consumers" in the left pane
   - Set the "Callback URL" to `https://auth.openhands.example.com/realms/openhands/broker/bitbucket/endpoint`
   - Select the following permissions: account:read, workspace:read, projects:write, repositories:write, pullrequests:write, issues:write, snippets:read, pipelines:read
   - Note the Client ID and Client Secret provided by BitBucket

2. Create a BitBucket App secret:

   ```bash
   kubectl create secret generic bitbucket-app -n openhands \
     --from-literal=client-id=<your-bitbucket-client-id> \
     --from-literal=client-secret=<your-bitbucket-client-secret> \
   ```

3. Update site-values.yaml file:

   ```yaml
   bitbucket:
     # Set this to true if you are using BitBucket as your identity provider
     enabled: true
   ```

When the chart is deployed, a job will run to configure the Keycloak realm with the identity provider credentials you provided.

#### Bitbucket Data Center

Bitbucket Data Center is the self-hosted version of Bitbucket. The setup is different from the cloud version. 

1. Create a Bitbucket Data Center Application Link:

   - Follow the instructions in Bitbucket Data Center to create an [OAuth2 Application Link](https://confluence.atlassian.com/enterprise/link-to-atlassian-products-using-oauth-2-0-1688928427.html)
   - Grant **Repository: Admin** scope. (Admin is required so OpenHands can manage the per-repo webhook via Bitbucket's REST API; it implies Repository: Write.)
   - Set the application URL to `https://auth.openhands.example.com/realms/openhands/broker/bitbucket_data_center
   - Note the Client ID and Client Secret provided by Bitbucket Data Center

2. Create a Bitbucket Data Center App secret:

   ```bash
   kubectl create secret generic bitbucket-data-center-app -n openhands \
     --from-literal=client-id=<your-client-id> \
     --from-literal=client-secret=<your-client-secret> \
   ```

3. Update site-values.yaml file:

   ```yaml
   bitbucketDataCenter:
     # Set this to true if you are using Bitbucket Data Center as your identity provider
     enabled: true
     host: <your-bitbucket-data-center-host>
   ```

#### Enterprise SSO (SAML)

Enterprise SSO signs users in with a corporate SAML identity provider through the bundled Keycloak.

1. Register Keycloak with your identity provider using these SAML values:

   - ACS URL `https://auth.openhands.example.com/realms/allhands/broker/enterprise_sso/endpoint`
   - Entity ID `https://auth.openhands.example.com/realms/allhands`

2. Update site-values.yaml file:

   ```yaml
   enterpriseSSO:
     enabled: true
     displayName: "Company SSO"          # optional, defaults to "Company SSO"
     idpMetadataUrl: "https://idp.example.com/saml/metadata"
   # When idpMetadataUrl is provided, the chart automatically creates and keeps updated the
   # enterprise_sso SAML identity provider in the bundled Keycloak on every pod start.
   # The managed provider validates SAML signatures, marks the imported IdP email verified,
   # and stores an ownership marker in Keycloak. Turning enabled off disables only
   # a provider with that marker, even if idpMetadataUrl is cleared in the same rollout.
   # Leave idpMetadataUrl empty to configure the provider manually in the Keycloak admin
   # console instead; the chart does not disable providers without its ownership marker.
   ```

   For manual setup, also add this identity-provider mapper to `enterprise_sso`:

   - Name: `identity-provider`
   - Mapper type: `hardcoded-attribute-idp-mapper`
   - Attribute: `identity_provider`
   - Value: `enterprise_sso:saml`
   - Sync mode: `FORCE`

   Configure the IdP to send attributes named exactly `email`, `firstName`, and
   `lastName` (case-sensitive). With a persistent NameID, these attributes populate
   the new user's email and names. Entra/ADFS default claim URIs need explicit
   remapping. Attribute NameFormat does not change matching by attribute name.

   The chart manages `openhands-profile-email`, `openhands-profile-firstName`,
   and `openhands-profile-lastName` using `saml-user-attribute-idp-mapper` with
   `attribute.name` and `user.attribute` set to the corresponding attribute,
   `attribute.name.format: ATTRIBUTE_FORMAT_UNSPECIFIED`, and `syncMode: IMPORT`.
   Add these mappers too when setting up the provider manually. Chart-managed
   mapper edits are overwritten on startup; use distinct names for custom mappers
   and avoid multiple conflicting importers for the same user field.

   IMPORT fills profiles only for new users. It does not backfill existing accounts
   or synchronize later IdP email/name changes. The provider's `trustEmail: true`
   marks an imported email verified. This chart does not change the
   first-broker-login flow; whether a verified imported email links to an
   existing account depends on that flow and is untested. Preserve ownership
   verification and test existing-email collisions and application
   organization/invitation policies.

### LiteLLM configuration

> [!IMPORTANT]
> We recommend using the provided LiteLLM instance rather than bringing your
> own. The provided LiteLLM instance uses an admin key for automated user
> management, which is the most extensively tested scenario. Our automation
> relies on this admin key to create and delete users automatically.

#### Configure LiteLLM Models

You'll need to set your model list for LiteLLM, using the LLM secrets you set above:

```yaml
litellm-helm:
  proxy_config:
    model_list:
      - model_name: "prod/claude-sonnet-4-20250514"
        litellm_params:
          model: "anthropic/claude-sonnet-4-20250514"
          api_key: os.environ/ANTHROPIC_API_KEY
```

You will also need to set the default LLM model to use in your site-values.yaml. Find the "env:" section in your site-values.yaml and uncomment the LITELLM_DEFAULT_MODEL. Set "your-model" to one of the models you configured:

```yaml
env:
  # replace <your-model> with your LLM model and uncomment this variable
  LITELLM_DEFAULT_MODEL: "litellm_proxy/<your-model>"
```

#### Forward client headers to LLM providers

By default, the bundled LiteLLM proxy does not forward arbitrary client request headers to upstream LLM providers. If you trust callers and need provider-visible custom headers, enable LiteLLM's client header forwarding explicitly:

```yaml
litellm-helm:
  proxy_config:
    general_settings:
      forward_client_headers_to_llm_api: true
```

This forwards LiteLLM-supported client headers such as `x-*` request headers and `anthropic-beta`. Leave this disabled unless you specifically need it.

#### LiteLLM Team Configuration

To use an existing team, provide the team ID.

If you do not set a team ID, a new team with ID `openhands` will be created. If the team ID you provide doesn't
already exist, a new team with that ID will be created.

```yaml
litellm:
  teamId: "<TEAM_ID>"
```

### Install OpenHands

Now we can install the helm chart.

```bash
helm dependency update
helm upgrade --install openhands --namespace openhands oci://ghcr.io/openhands/helm-charts/openhands -f site-values.yaml
```

### Verify your Setup

After installation, you should be able to see OpenHands running with:

```bash
kubectl port-forward svc/openhands-service 3000:3000 -n openhands
```

If you visit `http://localhost:3000` you should see the login screen!

But we're not done yet...

## Setting up DNS and Ingress

We recommend traefik as an ingress controller. If you're not using traefik,
you can set ingress.class in the objects below.

You'll also need to point your DNS records to the ingress controller's IP address.
In this example, we'll use `openhands.example.com` as the base domain.

First, set up a CNAME record pointing `*.openhands.example.com` to your ingress
controller's IP address.

Next, enable ingress in site-values.yaml:

```yaml
ingress:
  enabled: true
  host: openhands.example.com
keycloak:
  url: https://auth.openhands.example.com
  ingress:
    enabled: true
    hostname: auth.openhands.example.com
runtime-api:
  ingress:
    enabled: true
    hostname: runtimes.openhands.example.com
litellm-helm:
  ingress:
    enabled: true
    hosts:
      - host: llm-proxy.example.com
        paths:
          - path: /
            pathType: Prefix
```

Upgrade the release:

```bash
helm upgrade --install openhands --namespace openhands oci://ghcr.io/openhands/helm-charts/openhands -f site-values.yaml
```

## Hardening

The above configuration should work well for a POC. However, it uses several in-cluster databases,
which creates risk of data loss.

We recommend at minimum setting up a more permanent Postgres and S3-compatible file store, e.g.
using AWS RDS and AWS S3.

### Bring Your Own PostgreSQL

To use an external PostgreSQL database instead of deploying one with the chart:

1. Disable the included PostgreSQL:

   ```yaml
   postgresql:
     enabled: false
   ```

2. Configure the external database connection:

   ```yaml
   externalDatabase:
     host: your-postgresql-host
     port: 5432
     database: openhands
     existingSecret: postgres-password
   # Make sure the secret exists with the correct credentials
   # kubectl create secret generic postgres-password \
   #   --from-literal=username=<your-db-username> \
   #   --from-literal=password=<your-db-password>
   ```

3. Update the Keycloak, LiteLLM, and runtime-api configurations to use the external database:

   ```yaml
   keycloak:
     externalDatabase:
       host: your-postgresql-host
       port: 5432
       existingSecret: postgres-password

   litellm-helm:
     db:
       deployStandalone: false
       useExisting: true
       database: litellm
       endpoint: your-postgresql-host
       secret:
         name: postgres-password

   runtime-api:
     postgresql:
       auth:
         existingSecret: postgres-password
     env:
       DB_HOST: your-postgresql-host
       DB_USER: your-db-username
       DB_NAME: runtime_api_db
   ```

### Bring Your Own S3-Compatible Storage

To use an external S3 (or S3-compatible) store instead of the bundled MinIO,
disable the bundled MinIO and configure the connection:

```yaml
minio:
  enabled: false            # stops deploying the in-cluster MinIO
filestore:
  type: s3
  bucket: your-bucket-name
  region: your-s3-region
  # endpoint: https://your-s3-endpoint   # only for S3-compatible stores (MinIO/R2/…); omit for AWS S3
  #                                      # TLS is inferred from the URL scheme
  existingSecret: s3-credentials         # omit to use IRSA / Pod Identity instead
```

Create the credentials secret (keys match the AWS SDK env var names):

```bash
kubectl create secret generic s3-credentials -n openhands \
  --from-literal=AWS_ACCESS_KEY_ID=<your-access-key-id> \
  --from-literal=AWS_SECRET_ACCESS_KEY=<your-secret-access-key>
```

For AWS S3 on EKS you can skip the secret entirely and use a pod-level AWS
identity: omit `existingSecret` and grant the app ServiceAccount an IAM role
(via IRSA or EKS Pod Identity) with access to the bucket. Keep `region` set.

### Bring Your Own Redis

To use an external Redis instance:

1. Disable the included Redis:

   ```yaml
   redis:
     enabled: false
   ```

2. Configure the external Redis connection:

   ```yaml
   externalRedis:
     host: your-redis-host
     port: 6379
     existingSecret: redis
   # Make sure the secret exists with the correct credentials
   # kubectl create secret generic redis \
   #   --from-literal=redis-password=<your-redis-password>
   ```

### Switching the Bundled Cache to Valkey

The bundled cache is Redis by default. Bitnami's license change froze the free
Redis chart and images, so they receive no further CVE patches, and the
replacement is the official Valkey chart. It ships with this chart but is off
until you turn it on.

Cut over by disabling Redis and enabling Valkey in the same upgrade:

```yaml
redis:
  enabled: false
valkey:
  enabled: true
```

Redis remains the active cache while both are enabled, so enabling Valkey on its
own deploys it without moving anything.

The cache is discarded at cutover. Conversations continue, and sign-in sessions
are unaffected because they do not live in the cache. Rate limiting briefly
allows requests it would otherwise have counted.

**Nothing else changes.** The app still reads `REDIS_HOST`, `REDIS_PORT` and
`REDIS_PASSWORD`, and the password still comes from the existing `redis` Secret's
`redis-password` key, so no secret has to be created before upgrading or kept in
order to revert. To revert, restore the two values above.

If you pin cache resources or persistence, translate the keys you set — a
leftover `redis:` block is not read once Redis is disabled, and its sizing will
not be applied:

| Redis key | Valkey key |
|---|---|
| `redis.master.resources` | `valkey.resources` |
| `redis.master.persistence.enabled` | `valkey.dataStorage.enabled` |
| `redis.replica.replicaCount: 0` | `valkey.replica.enabled: false` |
| `redis.auth.existingSecret` | `valkey.auth.usersExistingSecret` |

### Storage Class Configuration

By default, the chart expects a storage class named `standard-rwo`. If you're using EKS, which typically has a `gp2` storage class, you can configure the chart to use it instead:

```yaml
runtime-api:
  env:
    STORAGE_CLASS: "gp2" # Replace with your cluster's storage class name
```

Alternatively, you can create a storage class named `standard-rwo` that uses your cloud provider's block storage:

```bash
# For AWS EKS
kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: standard-rwo
provisioner: kubernetes.io/aws-ebs
parameters:
  type: gp2
volumeBindingMode: WaitForFirstConsumer
EOF

# For GKE
kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: standard-rwo
provisioner: pd.csi.storage.gke.io
parameters:
  type: pd-standard
volumeBindingMode: WaitForFirstConsumer
EOF
```

### VolumeSnapshotClass Configuration (Optional)

To enable PVC snapshots for cost optimization (snapshotting paused runtime PVCs instead of keeping them active), you need to create a VolumeSnapshotClass. This is optional but recommended for production deployments to reduce storage costs.

```bash
# For AWS EKS
kubectl apply -f - <<EOF
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshotClass
metadata:
  name: ebs-snapshot-class
driver: ebs.csi.aws.com
deletionPolicy: Delete
EOF

# For GKE
kubectl apply -f - <<EOF
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshotClass
metadata:
  name: pd-snapshot-class
driver: pd.csi.storage.gke.io
deletionPolicy: Delete
parameters:
  storage-locations: us-central1  # Replace with your cluster's region
EOF
```

Then configure the runtime-api to use the snapshot class:

```yaml
runtime-api:
  env:
    VOLUME_SNAPSHOT_CLASS: "pd-snapshot-class"  # or "ebs-snapshot-class" for AWS
```

> **Note:** The VolumeSnapshot CRDs must be installed in your cluster. Most managed Kubernetes services (GKE, EKS) include these by default. If not, see the [Kubernetes VolumeSnapshot documentation](https://kubernetes.io/docs/concepts/storage/volume-snapshots/).

### Sandbox Network Isolation

We recommend restricting sandbox network access with the chart's sandbox egress NetworkPolicy.
With the policy enabled, sandboxes can reach the internet, cluster DNS, your ingress controller,
and the bundled LiteLLM and Laminar. They can't reach node, pod, or VPC-internal addresses, which
include the kubelet, the Kubernetes API server, cloud metadata servers, other sandboxes, and the
application's databases. The policy selects only sandbox pods in runtime-api's sandbox namespace
(`runtime-api.sandbox_namespace`), so the other pods in that namespace are unaffected.

The policy requires a network plugin that enforces NetworkPolicy:

- **GKE**: Dataplane V2, or network policy enforcement (`--enable-network-policy`).
- **EKS**: The VPC CNI with `enableNetworkPolicy` set to `"true"`.
- **AKS**: Azure CNI Powered by Cilium, Calico, or Azure Network Policy Manager.
- **OpenShift**: OVN-Kubernetes, the default plugin.

Flannel alone doesn't enforce NetworkPolicy.

To enable the policy, review these settings under `sandboxNetworkPolicy` in `values.yaml` against
your cluster:

- `internet.blockedCIDRs`: Add your node, pod, and Service CIDRs if any fall outside the defaults.
- `ingressController`: Match your ingress controller's pods and container ports. Sandboxes reach
  runtime URLs and the app at their public hostnames. If those hostnames resolve to an internal
  load balancer or to node IPs (a NodePort or hostPort ingress controller), add those addresses as
  `ipBlock` entries and add the ports that clients connect to, usually 80 and 443.
- `extraEgress`: Add the internal destinations that sandboxes need, such as git servers, proxies,
  package mirrors, and private LLM endpoints.
- `ipv6.enabled`: Set to `true` on IPv6 and dual-stack clusters. If your nodes or pods use global
  IPv6 addresses, add those ranges to `ipv6.blockedCIDRs`.

```yaml
sandboxNetworkPolicy:
  enabled: true
  extraEgress:
    - to:
        - ipBlock:
            cidr: 10.20.0.15/32  # Internal git server
      ports:
        - protocol: TCP
          port: 443
```

The `dns` and `ingressController` rules use the NetworkPolicy egress rule format. When you
override `to` or `ports`, your list replaces the default list.

After you install or upgrade, run the chart's test to confirm that the policy is enforced:

```bash
helm test openhands -n openhands --logs
```

The test runs a pod as a sandbox. It fails if the metadata server, the kubelet, or the release's
PostgreSQL or app server are reachable, or if DNS, LiteLLM, or Laminar are not.

#### GKE

The defaults work on GKE. If your cluster uses NodeLocal DNSCache without Dataplane V2 and its
Service range is private, add the kube-dns Service IP
(`kubectl -n kube-system get svc kube-dns`) to the DNS rule:

```yaml
sandboxNetworkPolicy:
  enabled: true
  dns:
    to:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: kube-system
        podSelector:
          matchExpressions:
            - key: k8s-app
              operator: In
              values: [kube-dns, node-local-dns]
      - ipBlock:
          cidr: 10.3.0.10/32  # Your kube-dns Service IP
```

#### EKS

The VPC CNI always allows a pod to reach its own node, so the test's kubelet check can't pass on
EKS. Disable it:

```yaml
sandboxNetworkPolicy:
  enabled: true
  test:
    checkKubelet: false
```

Sandboxes reach an internet-facing load balancer through the internet rule. For an internal load
balancer, add its subnets to `ingressController` as described above.

In its default `standard` mode, the VPC CNI allows all traffic from a new pod until it applies the
pod's policies, which takes a few seconds. To block traffic until then, set
`NETWORK_POLICY_ENFORCING_MODE` to `strict`. Strict mode requires a network policy for every pod
in the cluster.

#### AKS

The defaults block `168.63.129.16`, the Azure platform endpoint. If your node pools use LocalDNS,
add its addresses to the DNS rule:

```yaml
sandboxNetworkPolicy:
  enabled: true
  dns:
    to:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: kube-system
        podSelector:
          matchExpressions:
            - key: k8s-app
              operator: In
              values: [kube-dns, node-local-dns]
      - ipBlock:
          cidr: 169.254.10.10/32
      - ipBlock:
          cidr: 169.254.10.11/32
```

With Azure CNI Powered by Cilium, LocalDNS also needs the policy described in
[Microsoft's LocalDNS guidance](https://learn.microsoft.com/en-us/azure/aks/azure-cni-powered-by-cilium#is-aks-local-dns-supported-with-azure-cni-powered-by-cilium).

#### OpenShift

OpenShift serves cluster DNS from the `openshift-dns` namespace on port 5353. This example also
allows the default OpenShift router:

```yaml
sandboxNetworkPolicy:
  enabled: true
  dns:
    to:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: openshift-dns
    ports:
      - protocol: UDP
        port: 5353
      - protocol: TCP
        port: 5353
  ingressController:
    to:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: openshift-ingress
        podSelector:
          matchLabels:
            ingresscontroller.operator.openshift.io/deployment-ingresscontroller: default
    ports:
      - protocol: TCP
        port: 80
      - protocol: TCP
        port: 443
```

If the router uses the `HostNetwork` endpoint publishing strategy, allow your node CIDR instead,
as described in the next section.

#### On-Premises and Other Clusters

If your ingress controller listens on node IPs through a NodePort or hostPort, add your node CIDR
and the ports that clients connect to:

```yaml
sandboxNetworkPolicy:
  enabled: true
  ingressController:
    to:
      - namespaceSelector: {}
        podSelector:
          matchLabels:
            app.kubernetes.io/name: traefik
      - ipBlock:
          cidr: 10.0.1.0/24  # Your node CIDR
    ports:
      - protocol: TCP
        port: 8000
      - protocol: TCP
        port: 8443
      - protocol: TCP
        port: 80
      - protocol: TCP
        port: 443
```

## Upgrading

To upgrade the chart:

```bash
helm upgrade openhands -n openhands . -f my-values.yaml -n openhands
```

### Keycloak 26.3 to 26.8

The bundled Keycloak moves from `bitnamilegacy/keycloak:26.3.0` to Keycloak 26.8.0. On first start, Keycloak migrates its database to the 26.8 schema, and Keycloak 26.3 cannot run against it afterwards.

`helm upgrade` backs the database up first. A pre-upgrade hook (`keycloak.preUpgradeBackup`) reads the schema version from Keycloak's `migration_model` table and, when it differs from the version in `keycloak.image.tag`, copies `bitnami_keycloak` to `bitnami_keycloak_backup_26_3_0` on the same server while Keycloak 26.3 is still running. It runs after the other pre-upgrade hooks, so a failed check such as a Strict budget preflight stops the upgrade before the copy. If the copy fails, the upgrade stops before anything changes; the reason is in the log of the `<release>-keycloak-backup` Job. That includes a copy still running after `keycloak.preUpgradeBackup.activeDeadlineSeconds` minus 60 seconds (240 by default), for example one blocked by a lock on a Keycloak table; raise it for a large database. If the backup database already exists, the hook keeps it rather than copying again. So if an earlier attempt stopped after the backup but before Keycloak 26.8 started (for example, a manifest failed to apply or the 26.8 image could not be pulled), drop `bitnami_keycloak_backup_26_3_0` before retrying, or the backup will miss changes made since that attempt. The hook needs a database user that can `CREATE DATABASE` (the bundled `postgres` user can) and PostgreSQL 17 or older, since it runs `pg_dump` 17. If yours does not, back up the database yourself and upgrade with `keycloak.preUpgradeBackup.enabled=false` (on Replicated, untick **Back Up Keycloak Database Before Upgrade** under **Upgrade Checks**). The hook does not run under `helm template`, so ArgoCD and similar tools must back up by hand:

```bash
kubectl exec -n openhands <release>-postgresql-0 -- \
  sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U postgres -Fc -d bitnami_keycloak' > keycloak-26.3.dump
```

To roll back, scale the `keycloak` StatefulSet to 0, swap the databases, and upgrade with `keycloak.image` set back to `docker.io/bitnamilegacy/keycloak:26.3.0-debian-12-r0`:

```sql
ALTER DATABASE bitnami_keycloak RENAME TO bitnami_keycloak_26_8_0;
ALTER DATABASE bitnami_keycloak_backup_26_3_0 RENAME TO bitnami_keycloak;
```

If you skip the swap, the hook stops the upgrade with a message, because Keycloak 26.3 cannot start on the 26.8 schema.

The rollback does not keep changes made on 26.8, such as new Keycloak users. They stay in `bitnami_keycloak_26_8_0`.

On Replicated, the admin console has no rollback button, so run these steps on the cluster. For an online install, keep the image proxy path: run `helm upgrade` with `--reuse-values`, `keycloak.image.repository=proxy/openhands/docker.io/bitnamilegacy/keycloak`, and `keycloak.image.tag=26.3.0-debian-12-r0`. The admin console does not know about this `helm upgrade` and continues to show the new release. The next deploy from the admin console applies Keycloak 26.8 again. This includes a **Redeploy**, a config change, and an update. The hook backs up the restored 26.3 database again before Keycloak migrates it.

To go forward to 26.8 again, find the cause in the `keycloak-0` log of the failed 26.8 start. If the cause is in your setup, fix it, and then upgrade without the 26.3 image setting. On Replicated, deploy from the admin console. If the cause is in Keycloak 26.8 or in this chart, contact OpenHands support. Do not deploy until a release with a fix is available. If automatic deployments are on, turn them off until then.

With a manual dump, restore it into an empty `bitnami_keycloak` with `pg_restore` instead. Once 26.8 has run long enough that you will not roll back, drop `bitnami_keycloak_backup_26_3_0`. The hook never drops backups, and every later upgrade that changes the Keycloak version, patch releases included, adds another full copy of the database, so drop old `bitnami_keycloak_backup_*` databases before they fill the PostgreSQL volume.

## Uninstallation

To uninstall the chart:

```bash
helm uninstall openhands -n openhands
```

Note: This will not delete any PVCs or secrets created. You'll need to delete those manually if desired.

### Budget policy changes

The bundled LiteLLM proxy uses unmodified 1.103.6 pinned by digest and
`litellm-helm.proxy_config.general_settings.user_api_key_cache_ttl: 0`.
Both are required for a budget change (including disabling a user's limit)
to affect the next request using an existing key. Older proxies can keep enforcing
a removed limit from cached authorization data, even after a successful save.
This increases authorization database reads; production throughput has not been
load-tested. It does not enable or disable LLM response caching. If you use an
external LiteLLM proxy, configure and validate the same behavior there.
