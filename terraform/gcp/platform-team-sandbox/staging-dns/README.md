# OpenHands Enterprise Staging DNS Infrastructure

This module creates the DNS infrastructure for OpenHands Enterprise staging environments, enabling developers to deploy via Helm and access their installations via predictable URLs with automatic TLS.

## Quick Start for Developers

**TL;DR**: Deploy your branch and access it at `https://<branch-name>.ohe-staging.platform-team.all-hands.dev`

```bash
# Deploy your branch (replace 'my-feature' with your branch name)
helm install my-feature ./charts/openhands \
  --namespace my-feature \
  --create-namespace \
  --set ingress.enabled=true \
  --set ingress.host=my-feature.ohe-staging.platform-team.all-hands.dev \
  --set ingress.class=traefik \
  --set tls.enabled=true \
  --set tls.env=staging

# Access at: https://my-feature.ohe-staging.platform-team.all-hands.dev
```

---

## Routing Patterns

This infrastructure supports two routing patterns:

### 1. Subdomain-Based Routing (Recommended for Branch Deployments)

Each deployment gets its own subdomain. This is ideal for isolated feature testing.

```
https://feature-xyz.ohe-staging.platform-team.all-hands.dev  →  feature-xyz deployment
https://pr-123.ohe-staging.platform-team.all-hands.dev       →  pr-123 deployment
https://main.ohe-staging.platform-team.all-hands.dev         →  main deployment
```

**Helm Values:**
```yaml
ingress:
  enabled: true
  host: feature-xyz.ohe-staging.platform-team.all-hands.dev
  class: traefik
tls:
  enabled: true
  env: staging  # Uses shared wildcard certificate
```

### 2. Path-Based Routing (For Multi-Service Deployments)

Multiple services share a single hostname, differentiated by URL path.

```
https://ohe-staging.platform-team.all-hands.dev/            →  main app
https://ohe-staging.platform-team.all-hands.dev/api/automation  →  automation service
https://ohe-staging.platform-team.all-hands.dev/mcp/mcp     →  MCP service
https://ohe-staging.platform-team.all-hands.dev/integration/*   →  integration events
```

**Path routing is configured via separate ingress resources:**
- `ingress-root.yaml` → `/`
- `ingress-automation.yaml` → `/api/automation`
- `ingress-mcp.yaml` → `/mcp/mcp`
- `ingress-integrations.yaml` → `/integration/*`, `/slack`, `/oauth/device`

---

## Developer Deployment Guide

### Prerequisites

1. Access to the GKE cluster: `gcloud container clusters get-credentials <cluster-name> --region us-central1 --project platform-team-sandbox-62793`
2. Helm 3.x installed
3. kubectl configured

### Option A: Simple Deployment (Subdomain)

```bash
# Set your branch/feature name (must be DNS-safe: lowercase, no special chars)
BRANCH_NAME="my-feature"

# Deploy
helm install $BRANCH_NAME ./charts/openhands \
  --namespace $BRANCH_NAME \
  --create-namespace \
  --set ingress.enabled=true \
  --set ingress.host=${BRANCH_NAME}.ohe-staging.platform-team.all-hands.dev \
  --set ingress.class=traefik \
  --set tls.enabled=true \
  --set tls.env=staging \
  -f your-values.yaml

# Verify
kubectl get ingress -n $BRANCH_NAME
curl -s https://${BRANCH_NAME}.ohe-staging.platform-team.all-hands.dev/health
```

### Option B: Using prefixWithBranch (Auto Subdomain)

The charts support automatic subdomain prefixing:

```bash
helm install openhands ./charts/openhands \
  --namespace my-feature \
  --create-namespace \
  --set ingress.enabled=true \
  --set ingress.host=ohe-staging.platform-team.all-hands.dev \
  --set ingress.prefixWithBranch=true \
  --set branchSanitized=my-feature \
  --set ingress.class=traefik \
  --set tls.enabled=true \
  --set tls.env=staging

# Results in: https://my-feature.ohe-staging.platform-team.all-hands.dev
```

### Option C: Deploy Runtime API (Separate Service)

```bash
helm install runtime-api ./charts/runtime-api \
  --namespace my-feature \
  --set ingress.enabled=true \
  --set ingress.host=my-feature.ohe-staging.platform-team.all-hands.dev \
  --set ingress.path=/runtime \
  --set ingress.className=traefik \
  --set ingress.tls=true \
  --set ingress.tlsSecretName=ohe-staging-wildcard-tls
```

### Cleanup

```bash
helm uninstall $BRANCH_NAME -n $BRANCH_NAME
kubectl delete namespace $BRANCH_NAME
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Internet                                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    DNS: *.ohe-staging.platform-team.all-hands.dev           │
│                         → 34.46.229.222 (Traefik LB)                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Traefik Ingress Controller                          │
│    ┌─────────────────────────────────────────────────────────────────┐     │
│    │  TLS Termination: Wildcard cert (Let's Encrypt)                 │     │
│    │  Certificate: *.ohe-staging.platform-team.all-hands.dev         │     │
│    └─────────────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
    ┌───────────────┐      ┌───────────────┐      ┌───────────────┐
    │  feature-a    │      │  feature-b    │      │    main       │
    │  namespace    │      │  namespace    │      │  namespace    │
    │               │      │               │      │               │
    │  ┌─────────┐  │      │  ┌─────────┐  │      │  ┌─────────┐  │
    │  │OpenHands│  │      │  │OpenHands│  │      │  │OpenHands│  │
    │  │ Service │  │      │  │ Service │  │      │  │ Service │  │
    │  └─────────┘  │      │  └─────────┘  │      │  └─────────┘  │
    └───────────────┘      └───────────────┘      └───────────────┘
```

## Components

### DNS (Terraform-managed)
- **Zone**: `ohe-staging.platform-team.all-hands.dev`
- **NS Delegation**: Parent zone delegates to child zone nameservers
- **Wildcard A Record**: `*.ohe-staging.platform-team.all-hands.dev` → `34.46.229.222`
- **Root A Record**: `ohe-staging.platform-team.all-hands.dev` → `34.46.229.222`

### TLS (cert-manager)
- **ClusterIssuer**: `letsencrypt-staging-dns` - Uses Let's Encrypt with DNS-01 challenge
- **Wildcard Certificate**: Covers root and all subdomains
- **TLSStore**: Configures Traefik to use wildcard cert as default

### Traefik Ingress Controller
- Handles TLS termination
- Routes traffic based on hostname and path
- Uses wildcard certificate for all `*.ohe-staging.platform-team.all-hands.dev` hosts

---

## Infrastructure Setup (Admin Only)

### Step 1: Deploy DNS Infrastructure

```bash
cd terraform/gcp/staging-dns
terraform init
terraform plan
terraform apply
```

### Step 2: Install cert-manager

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.4/cert-manager.yaml
kubectl wait --for=condition=Available deployment --all -n cert-manager --timeout=300s
```

### Step 3: Create GCP Service Account for DNS-01 Challenges

```bash
# Create service account
gcloud iam service-accounts create cert-manager-dns \
  --display-name="cert-manager DNS solver" \
  --project=platform-team-sandbox-62793

# Grant DNS admin role
gcloud projects add-iam-policy-binding platform-team-sandbox-62793 \
  --member="serviceAccount:cert-manager-dns@platform-team-sandbox-62793.iam.gserviceaccount.com" \
  --role="roles/dns.admin"

# Create and store key as K8s secret
gcloud iam service-accounts keys create /tmp/key.json \
  --iam-account=cert-manager-dns@platform-team-sandbox-62793.iam.gserviceaccount.com

kubectl create secret generic clouddns-dns01-solver-svc-acct \
  --from-file=key.json=/tmp/key.json \
  -n cert-manager

rm /tmp/key.json
```

### Step 4: Apply ClusterIssuer, Certificate, and TLSStore

```bash
kubectl apply -f k8s-manifests/cert-manager-issuer.yaml
```

### Step 5: Verify Setup

```bash
# Check certificate
kubectl get certificate -n traefik ohe-staging-wildcard-cert
# Should show READY: True

# Check TLS secret
kubectl get secret -n traefik ohe-staging-wildcard-tls

# Check TLSStore
kubectl get tlsstore -n traefik default

# Test DNS and TLS
curl -s https://ohe-staging.platform-team.all-hands.dev/
```

---

## URL Patterns

| Deployment Type | Helm Release | URL |
|-----------------|--------------|-----|
| Main branch | `main` | `https://main.ohe-staging.platform-team.all-hands.dev` |
| Feature branch | `feature-123` | `https://feature-123.ohe-staging.platform-team.all-hands.dev` |
| PR preview | `pr-456` | `https://pr-456.ohe-staging.platform-team.all-hands.dev` |
| Path-based root | `openhands` | `https://ohe-staging.platform-team.all-hands.dev/` |
| Path-based API | `openhands` | `https://ohe-staging.platform-team.all-hands.dev/api/automation` |

---

## Troubleshooting

### DNS not resolving

```bash
# Verify DNS resolution
dig +short test.ohe-staging.platform-team.all-hands.dev
# Should return: 34.46.229.222

# Check NS records
dig NS ohe-staging.platform-team.all-hands.dev

# Check Cloud DNS records
gcloud dns record-sets list --zone=ohe-staging-platform-team-all-hands-dot-dev \
  --project=platform-team-sandbox-62793
```

### Certificate issues

```bash
# Check certificate status
kubectl get certificate -A
kubectl describe certificate -n traefik ohe-staging-wildcard-cert

# Check for ACME challenges
kubectl get challenges -A

# Check cert-manager logs
kubectl logs -n cert-manager -l app=cert-manager --tail=100
```

### Ingress not routing

```bash
# Check ingress resources
kubectl get ingress -A

# Check Traefik logs
kubectl logs -n traefik -l app.kubernetes.io/name=traefik --tail=100

# Verify TLS secret exists
kubectl get secret ohe-staging-wildcard-tls -n traefik
```

### TLS errors

```bash
# Check certificate details
kubectl get secret ohe-staging-wildcard-tls -n traefik -o jsonpath='{.data.tls\.crt}' | \
  base64 -d | openssl x509 -noout -text | head -20

# Check TLSStore
kubectl describe tlsstore default -n traefik
```

---

## Files

| File | Purpose |
|------|---------|
| `main.tf` | Terraform DNS zone and records |
| `variables.tf` | Terraform input variables |
| `outputs.tf` | Terraform outputs (nameservers, etc.) |
| `terraform.tfvars` | Environment-specific values |
| `k8s-manifests/cert-manager-issuer.yaml` | ClusterIssuer, Certificate, TLSStore |
