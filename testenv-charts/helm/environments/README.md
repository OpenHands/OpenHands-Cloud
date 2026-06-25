# OpenHands Staging Helm Environments

This directory contains Helm values files for different staging deployment configurations on GKE.

## Available Environments

| Environment | Routing Strategy | Use Case |
|-------------|------------------|----------|
| `single-cluster-path` | Path-based | Single shared hostname, services differentiated by URL path |
| `single-cluster-subdomain` | Subdomain-based | Each branch/release gets its own subdomain |

## Architecture Overview

### Path-Based Routing (`single-cluster-path`)
All services share a single hostname with different URL paths:
```
https://34.46.229.222.nip.io/              → OpenHands UI (openhands-service:3000)
https://34.46.229.222.nip.io/auth/         → Keycloak (keycloak:80)
https://34.46.229.222.nip.io/runtime/      → Runtime API (runtime-api:5000)
https://34.46.229.222.nip.io/api/automation → Automation service
https://34.46.229.222.nip.io/integration/  → Integration events
https://34.46.229.222.nip.io/mcp/mcp       → MCP service
```

**Pros**: Simpler DNS setup, single TLS certificate
**Cons**: Cannot run multiple isolated deployments simultaneously

### Subdomain-Based Routing (`single-cluster-subdomain`)
Each branch/release gets its own subdomain:
```
https://main.ohe-staging.platform-team.all-hands.dev/          → main branch
https://feature-x.ohe-staging.platform-team.all-hands.dev/     → feature-x branch
https://pr-123.ohe-staging.platform-team.all-hands.dev/        → PR #123
```

**Pros**: Multiple isolated deployments, branch-specific URLs
**Cons**: Requires wildcard DNS and certificate

## Deployment Instructions

### Prerequisites

1. **GKE Cluster**: Ensure you have a GKE cluster in `platform-team-sandbox-62793`
2. **Traefik Ingress**: Install Traefik ingress controller
3. **cert-manager**: Install cert-manager for TLS certificate automation
4. **DNS Zone**: Configure Cloud DNS zone (see `terraform/gcp/staging-dns/`)
5. **Secrets**: Create required Kubernetes secrets

### Required Secrets

```bash
# Create namespace
kubectl create namespace openhands

# GHCR pull secret
kubectl create secret docker-registry ghcr-login-secret \
  --docker-server=ghcr.io \
  --docker-username=YOUR_GITHUB_USERNAME \
  --docker-password=YOUR_GITHUB_PAT \
  -n openhands

# PostgreSQL password
kubectl create secret generic postgres-password \
  --from-literal=username=postgres \
  --from-literal=password=YOUR_POSTGRES_PASSWORD \
  -n openhands

# JWT secret for sessions
kubectl create secret generic jwt-secret \
  --from-literal=jwt-secret=YOUR_JWT_SECRET_KEY \
  -n openhands

# Keycloak admin password
kubectl create secret generic keycloak-admin \
  --from-literal=admin-password=YOUR_KEYCLOAK_ADMIN_PASSWORD \
  -n openhands
```

### Deploying Path-Based Environment

```bash
# Add Helm repo dependencies
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

# Install from charts directory
cd /path/to/OpenHands-Cloud

helm install openhands ./charts/openhands \
  -f testenv-charts/helm/environments/single-cluster-path/values-openhands.yaml \
  -n openhands \
  --create-namespace
```

### Deploying Subdomain-Based Environment

For subdomain-based routing, you must provide the `branchSanitized` value:

```bash
# Deploy a specific branch
BRANCH_NAME="my-feature"

# Sanitize branch name (lowercase, replace invalid chars)
BRANCH_SANITIZED=$(echo "$BRANCH_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | sed 's/--*/-/g' | sed 's/^-//' | sed 's/-$//')

helm install "openhands-${BRANCH_SANITIZED}" ./charts/openhands \
  -f testenv-charts/helm/environments/single-cluster-subdomain/values-openhands.yaml \
  --set branchSanitized="${BRANCH_SANITIZED}" \
  -n openhands \
  --create-namespace
```

#### Automated Branch Deployment (CI/CD Example)

```yaml
# .github/workflows/deploy-staging.yaml
name: Deploy to Staging
on:
  push:
    branches: ['*']

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Sanitize branch name
        id: branch
        run: |
          BRANCH="${GITHUB_REF_NAME}"
          SANITIZED=$(echo "$BRANCH" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | sed 's/--*/-/g' | head -c 63)
          echo "sanitized=$SANITIZED" >> $GITHUB_OUTPUT
      
      - name: Deploy to GKE
        run: |
          helm upgrade --install "openhands-${{ steps.branch.outputs.sanitized }}" ./charts/openhands \
            -f testenv-charts/helm/environments/single-cluster-subdomain/values-openhands.yaml \
            --set branchSanitized="${{ steps.branch.outputs.sanitized }}" \
            -n openhands
```

## TLS Certificate Provisioning

### Path-Based (Manual or cert-manager HTTP-01)
For path-based routing, you can use cert-manager with HTTP-01 challenge:
```yaml
# TLS is disabled by default for path-based (uses nip.io for testing)
tls:
  enabled: false
```

### Subdomain-Based (Wildcard with DNS-01)
Subdomain-based routing requires wildcard certificates via DNS-01 challenge:

1. Apply the ClusterIssuer:
```bash
kubectl apply -f terraform/gcp/staging-dns/k8s-manifests/cert-manager-issuer.yaml
```

2. The values file references this issuer:
```yaml
ingress:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-dns01
```

3. cert-manager automatically provisions `*.ohe-staging.platform-team.all-hands.dev`

## Ingress Templates Reference

The path-based routing logic is defined in these Helm templates:

| Template | Path | Target Service |
|----------|------|----------------|
| `ingress-root.yaml` | `/` | openhands-service:3000 |
| `ingress-automation.yaml` | `/api/automation` | automation:80 |
| `ingress-integrations.yaml` | `/integration/*`, `/slack`, `/oauth/device` | openhands-integrations-service:3000 |
| `ingress-mcp.yaml` | `/mcp/mcp` | openhands-mcp-service:3000 |
| `runtime-api/ingress.yaml` | `/runtime` (configurable) | runtime-api |

### Key Configuration Values

```yaml
ingress:
  enabled: true
  host: "example.com"           # Base hostname
  prefixWithBranch: true        # Enable subdomain routing
  class: traefik                # Ingress controller class

branchSanitized: "my-branch"    # Results in: my-branch.example.com
```

## Troubleshooting

### Check Ingress Resources
```bash
kubectl get ingress -n openhands
kubectl describe ingress openhands-root-ingress -n openhands
```

### Check TLS Certificates
```bash
kubectl get certificates -n openhands
kubectl describe certificate app-all-hands-staging-tls -n openhands
```

### Check cert-manager Logs
```bash
kubectl logs -n cert-manager deploy/cert-manager -f
```

### DNS Resolution
```bash
# Check wildcard DNS
dig +short my-branch.ohe-staging.platform-team.all-hands.dev
```
