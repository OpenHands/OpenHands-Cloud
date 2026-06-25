# Deploying Your Branch to Staging

This guide explains how to deploy your own branch of OpenHands to the shared staging cluster (`ohe-staging.platform-team.all-hands.dev`).

## Quick Start (TL;DR)

```bash
# 1. Set your branch name (lowercase, alphanumeric, hyphens only)
export BRANCH_NAME="my-feature"
export NAMESPACE="openhands-${BRANCH_NAME}"
export IMAGE_TAG="sha-abc1234"  # Your image tag from CI

# 2. Create namespace and copy secrets
kubectl create namespace ${NAMESPACE}
for secret in ghcr-login-secret postgres-password redis keycloak-realm keycloak-admin lite-llm-api-key litellm-env-secrets admin-password; do
  kubectl get secret $secret -n openhands -o yaml | sed "s/namespace: openhands/namespace: ${NAMESPACE}/" | kubectl apply -n ${NAMESPACE} -f -
done

# 3. Deploy!
helm upgrade --install openhands-${BRANCH_NAME} ./charts/openhands \
  --namespace ${NAMESPACE} \
  --values testenv-charts/helm/base-values.yaml \
  --set image.tag="${IMAGE_TAG}" \
  --set branchSanitized="${BRANCH_NAME}"

# 4. Access at: https://${BRANCH_NAME}.ohe-staging.platform-team.all-hands.dev
```

## Overview

Branch deployments use **shared infrastructure** (PostgreSQL, Redis, Keycloak, LiteLLM) from the main `openhands` namespace, so you only deploy the OpenHands application itself. This makes deployments fast and resource-efficient.

| Component | Source |
|-----------|--------|
| PostgreSQL | Shared from `openhands` namespace |
| Redis | Shared from `openhands` namespace |
| Keycloak | Shared (`auth.ohe-staging.platform-team.all-hands.dev`) |
| LiteLLM | Shared from `openhands` namespace |
| Minio | Per-branch (ephemeral) |

Your deployment URL: `https://<branch-name>.ohe-staging.platform-team.all-hands.dev`

## Prerequisites

1. **Cluster Access**:
   ```bash
   gcloud container clusters get-credentials ohe-staging-cluster \
     --region us-central1 \
     --project staging-092324
   ```

2. **Helm 3.x**: https://helm.sh/docs/intro/install/

3. **Docker Image**: Your branch needs a published image. CI builds images as:
   - `ghcr.io/openhands/openhands:sha-<commit>`
   - `ghcr.io/openhands/enterprise-server:sha-<commit>`

## Step-by-Step Deployment

### 1. Create Your Namespace

```bash
export BRANCH_NAME="your-feature"  # lowercase, alphanumeric, hyphens only
export NAMESPACE="openhands-${BRANCH_NAME}"

kubectl create namespace ${NAMESPACE}
```

### 2. Copy Required Secrets

Branch deployments need secrets from the main namespace:

```bash
SECRETS="ghcr-login-secret postgres-password redis keycloak-realm keycloak-admin lite-llm-api-key litellm-env-secrets admin-password"

for secret in $SECRETS; do
  kubectl get secret $secret -n openhands -o yaml | \
    sed "s/namespace: openhands/namespace: ${NAMESPACE}/" | \
    kubectl apply -n ${NAMESPACE} -f -
done
```

### 3. Deploy with Helm

The `base-values.yaml` configures all shared infrastructure. You only need to set your image tag and branch name:

```bash
helm upgrade --install openhands-${BRANCH_NAME} ./charts/openhands \
  --namespace ${NAMESPACE} \
  --values testenv-charts/helm/base-values.yaml \
  --set image.tag="sha-abc1234" \
  --set branchSanitized="${BRANCH_NAME}"
```

**Optional overrides:**
```bash
  --set image.repository="ghcr.io/openhands/enterprise-server" \  # Different image
  --set deployment.replicas=2 \                                    # More replicas
  --set automation.enabled=false                                   # Disable automation
```

### 4. Access Your Deployment

Your app is available at:
```
https://<branch-name>.ohe-staging.platform-team.all-hands.dev
```

## Managing Your Deployment

### View Status

```bash
kubectl get pods -n ${NAMESPACE}
kubectl get ingress -n ${NAMESPACE}
kubectl logs -n ${NAMESPACE} -l app=openhands -f
```

### Update Deployment

After pushing new changes and CI builds a new image:

```bash
helm upgrade openhands-${BRANCH_NAME} ./charts/openhands \
  --namespace ${NAMESPACE} \
  --values testenv-charts/helm/base-values.yaml \
  --set image.tag="${NEW_IMAGE_TAG}" \
  --set branchSanitized="${BRANCH_NAME}"
```

### Delete Deployment

```bash
helm uninstall openhands-${BRANCH_NAME} -n ${NAMESPACE}
kubectl delete namespace ${NAMESPACE}
```

## Troubleshooting

### Pods Stuck in Init State

Check init container logs:
```bash
kubectl logs -n ${NAMESPACE} <pod-name> -c wait-for-db
kubectl logs -n ${NAMESPACE} <pod-name> -c wait-for-redis
```

**Common causes:**
- Missing secrets → Re-run the secret copy step
- Database unreachable → Check PostgreSQL in `openhands` namespace

### Missing Secrets

```bash
# Compare secrets
kubectl get secrets -n ${NAMESPACE}
kubectl get secrets -n openhands
```

### Image Pull Errors

```bash
kubectl get secret ghcr-login-secret -n ${NAMESPACE}
kubectl describe pod -n ${NAMESPACE} <pod-name>
```

### Database Connection Issues

```bash
kubectl run pg-test -n ${NAMESPACE} --rm -it --image=postgres:15 -- \
  psql -h openhands-postgresql.openhands.svc.cluster.local -U postgres
```

## Advanced: Custom Values File

For complex deployments, create a values override file:

```yaml
# my-branch-values.yaml
image:
  tag: "sha-abc1234"

branchSanitized: "my-feature"

# Disable services you don't need
automation:
  enabled: false
integrationEvents:
  deployment:
    replicas: 0
```

Deploy with:
```bash
helm upgrade --install openhands-${BRANCH_NAME} ./charts/openhands \
  --namespace ${NAMESPACE} \
  --values testenv-charts/helm/base-values.yaml \
  --values my-branch-values.yaml
```

## Best Practices

1. **Clean up when done**: Delete your namespace to free cluster resources
2. **Use descriptive branch names**: They become your URL subdomain
3. **Keep deployments minimal**: Disable services you don't need

## Quick Reference

| Item | Value |
|------|-------|
| Cluster | `ohe-staging-cluster` |
| Region | `us-central1` |
| Project | `staging-092324` |
| Base domain | `ohe-staging.platform-team.all-hands.dev` |
| Keycloak | `auth.ohe-staging.platform-team.all-hands.dev` |
| Base values | `testenv-charts/helm/base-values.yaml` |
