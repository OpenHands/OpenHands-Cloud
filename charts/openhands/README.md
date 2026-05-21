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

### Email (Resend)

To enable organization invitation emails via Resend, set `resend.enabled: true` and create a Kubernetes secret named `resend-api-key` with key `resend-api-key` containing your Resend API key. The secret name can be overridden with `resend.auth.existingSecret`.

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
helm upgrade --install openhands --namespace openhands oci://ghcr.io/all-hands-ai/helm-charts/openhands -f site-values.yaml
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
helm upgrade --install openhands --namespace openhands oci://ghcr.io/all-hands-ai/helm-charts/openhands -f site-values.yaml
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

To use an external S3-compatible storage instead of MinIO:

1. Disable the ephemeral filestore:

   ```yaml
   filestore:
     ephemeral: false
   ```

2. Configure the S3 connection:

   ```yaml
   filestore:
     ephemeral: false
     bucket: your-bucket-name
     endpoint: https://your-s3-endpoint
     region: your-s3-region
     existingSecret: s3-credentials
   # Make sure the secret exists with the correct credentials
   # kubectl create secret generic s3-credentials \
   #   --from-literal=access-key=<your-access-key> \
   #   --from-literal=secret-key=<your-secret-key>
   ```

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

## Upgrading

To upgrade the chart:

```bash
helm upgrade openhands -n openhands . -f my-values.yaml -n openhands
```

## Uninstallation

To uninstall the chart:

```bash
helm uninstall openhands -n openhands
```

Note: This will not delete any PVCs or secrets created. You'll need to delete those manually if desired.
