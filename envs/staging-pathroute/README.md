# Staging Path-Route Environment Configuration

This directory contains the configuration for deploying OpenHands to the **staging-pathroute** environment on the Platform Team Sandbox infrastructure.

## Environment Overview

This environment uses **path-based routing** and deploys to the Platform Team Sandbox cluster:

- **URL:** `https://pathroute.ohe-staging.platform-team.all-hands.dev/`
- **Auth:** `https://auth.ohe-staging.platform-team.all-hands.dev` (shared Keycloak)
- **Automation API:** `https://pathroute.ohe-staging.platform-team.all-hands.dev/api/automation`
- **Integrations:** `https://pathroute.ohe-staging.platform-team.all-hands.dev/integration/*`
- **MCP:** `https://pathroute.ohe-staging.platform-team.all-hands.dev/mcp/mcp`

## Infrastructure

This environment shares infrastructure with PR #580 (`SV-OHE-staging-Deploy-Infra`):

| Component | Details |
|-----------|---------|
| **GCP Project** | `platform-team-sandbox` |
| **GKE Cluster** | `ohe-staging-cluster` |
| **Region** | `us-central1` |
| **Base Domain** | `ohe-staging.platform-team.all-hands.dev` |
| **Namespace** | `openhands-pathroute` |
| **Helm Release** | `openhands-pathroute` |

## Directory Structure

```
envs/staging-pathroute/
├── README.md           # This file
├── values.yaml         # Environment-specific overrides (routing, URLs)
└── secrets/            # (unused - secrets are managed in all-hands-system namespace)

testenv-charts/helm/environments/staging/
└── base-values.yaml    # Base configuration for all staging deployments
```

Helm is invoked with:
```bash
helm upgrade ... \
  -f testenv-charts/helm/environments/staging/base-values.yaml \
  -f envs/staging-pathroute/values.yaml \
  --set branchSanitized=pathroute
```

## Secrets Management

Secrets are **managed in the `all-hands-system` namespace** on the cluster and copied to the deployment namespace at deploy time. This follows the same pattern as branch deployments described in `testenv-charts/BRANCH_DEPLOYMENTS.md`.

Required secrets in `all-hands-system`:
- `ghcr-login-secret`
- `postgres-password`
- `redis`
- `keycloak-admin`
- `keycloak-db-secret`
- `lite-llm-api-key`
- `stripe-api-key`
- `resend-api-key`
- `github-app`
- `bitbucket-app`
- `gitlab-auth`
- `automation-webhook-secret`
- `automation-service-key`
- `automation-db-secret`

## Deployment

### Via GitHub Actions (Recommended)

1. Go to **Actions** → **Deploy to Staging**
2. Click **Run workflow**
3. Select environment: `pathroute` or `both`
4. Enter the image tag to deploy

### Manual Deployment

```bash
# Get cluster credentials
gcloud container clusters get-credentials ohe-staging-cluster \
  --region us-central1 \
  --project platform-team-sandbox

# Create namespace and copy secrets
kubectl create namespace openhands-pathroute
for secret in ghcr-login-secret postgres-password redis keycloak-admin keycloak-db-secret lite-llm-api-key; do
  kubectl get secret $secret -n all-hands-system -o yaml | \
    sed 's/namespace: all-hands-system/namespace: openhands-pathroute/' | \
    kubectl apply -n openhands-pathroute -f -
done

# Deploy
helm upgrade --install openhands-pathroute ./charts/openhands \
  --namespace openhands-pathroute \
  --values testenv-charts/helm/environments/staging/base-values.yaml \
  --values envs/staging-pathroute/values.yaml \
  --set branchSanitized=pathroute \
  --set image.tag=main
```

## Troubleshooting

```bash
# Check pods
kubectl get pods -n openhands-pathroute

# Check Helm release
helm history openhands-pathroute -n openhands-pathroute

# Check ingress
kubectl get ingress -n openhands-pathroute

# View logs
kubectl logs -n openhands-pathroute -l app=openhands -f

# Get cluster credentials
gcloud container clusters get-credentials ohe-staging-cluster \
  --region us-central1 --project platform-team-sandbox
```

## Related Documentation

- [Branch Deployments Guide](../../testenv-charts/BRANCH_DEPLOYMENTS.md)
- [Full Deployment Guide](../../testenv-charts/FULL_DEPLOYMENT_GUIDE.md)
- [Staging Base Values](../../testenv-charts/helm/environments/staging/base-values.yaml)
