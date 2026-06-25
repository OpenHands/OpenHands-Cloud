# OpenHands Site-Specific Infrastructure

This directory contains site-specific Terraform modules and Helm value overrides for deploying OpenHands to GKE in the staging GCP project (`staging-092324`).

> **Note:** This folder contains only site-specific configurations. The base Helm charts and their default values live in `charts/`. Only override values that differ from the defaults.

## Overview

Two independent deployment environments are supported:

| Environment | Routing | Use Case |
|------------|---------|----------|
| `single-cluster-path` | Path-based (`domain.com/api/`, `/runtime/`) | Simple deployments |
| `single-cluster-subdomain` | Subdomain-based (`api.domain.com`, `runtime.domain.com`) | Branch deployments |

## Directory Structure

```
testenv-charts/
├── helm/
│   ├── cert-manager/         # TLS certificate management
│   ├── external-dns/         # DNS record automation
│   └── traefik/              # Ingress controller
├── k8s/                      # Kubernetes manifests
│   └── sysbox/               # Sysbox runtime installation
└── scripts/                  # Deployment scripts

terraform/gcp/platform-team-sandbox/
├── modules/
│   ├── gke-cluster/          # GKE cluster module
│   └── vpc-network/          # VPC network module
├── environments/
│   ├── platform-team-sandbox/
│   ├── single-cluster-path/
│   └── single-cluster-subdomain/
├── shared-auth/              # Shared Keycloak authentication
└── staging-dns/              # DNS zone configuration
```

## Prerequisites

- GCP project: `staging-092324`
- Terraform >= 1.5.0
- Helm >= 3.0
- `gcloud` CLI configured
- `kubectl` configured

## Configuration Variables

Some manifest files use shell-style variable placeholders that must be substituted before applying:

| Variable | Description | Example |
|----------|-------------|---------|
| `${ACME_EMAIL}` | Email for Let's Encrypt certificate notifications | `admin@example.com` |
| `${GCP_PROJECT_ID}` | GCP project ID for Cloud DNS | `staging-092324` |

**Substitute variables using `envsubst`:**

```bash
# Set environment variables
export ACME_EMAIL="your-email@example.com"
export GCP_PROJECT_ID="staging-092324"

# Apply with substitution
envsubst < helm/cert-manager/cluster-issuer.yaml | kubectl apply -f -
```

Or manually edit the files before applying.

## Deployment

### 1. Terraform Infrastructure

```bash
cd terraform/gcp/platform-team-sandbox/environments/<environment-name>

# Initialize
terraform init

# Configure variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

# Plan and apply
terraform plan
terraform apply
```

### 2. Configure kubectl

```bash
gcloud container clusters get-credentials <cluster-name> \
  --region <region> \
  --project staging-092324
```

### 3. Install Helm Charts

```bash
# Install cert-manager
helm install cert-manager jetstack/cert-manager \
  -n cert-manager --create-namespace \
  -f helm/cert-manager/values.yaml

# Install external-dns
helm install external-dns bitnami/external-dns \
  -n external-dns --create-namespace \
  -f helm/external-dns/values.yaml \
  -f helm/external-dns/values-<routing-type>.yaml \
  --set google.project=staging-092324 \
  --set domainFilters[0]=your-domain.com

# Install traefik
helm install traefik traefik/traefik \
  -n traefik --create-namespace \
  -f helm/traefik/values.yaml \
  -f helm/traefik/values-<routing-type>.yaml
```

### 4. Deploy OpenHands

Use the main OpenHands Helm chart with environment-specific values:

```bash
helm install openhands ../charts/openhands \
  -n openhands --create-namespace \
  -f <environment-values.yaml>
```

## Routing Strategies

### Path-Based Routing

All services accessible via URL paths on a single domain:

```
https://domain.com/              # Main UI
https://domain.com/api/          # API endpoints
https://domain.com/runtime/      # Runtime API
https://domain.com/auth/         # Keycloak
https://domain.com/llm/          # LiteLLM proxy
```

**Advantages:**
- Single TLS certificate (HTTP-01 challenge)
- Simple DNS setup
- Lower cost (one load balancer)

**Requirements:**
- Apply path-stripping middlewares
- Configure backend services for path prefix handling

### Subdomain-Based Routing

Each service on its own subdomain:

```
https://app.domain.com           # Main UI
https://api.domain.com           # API endpoints
https://runtime.domain.com       # Runtime API
https://auth.domain.com          # Keycloak
https://llm.domain.com           # LiteLLM proxy
https://branch.domain.com        # Branch deployments
```

**Advantages:**
- Clean service separation
- Native branch deployment support
- No path rewriting needed

**Requirements:**
- Wildcard TLS certificate (DNS-01 challenge)
- Cloud DNS zone for external-dns
- Wildcard DNS record

## Existing Staging Environment

The existing `staging.all-hands.dev` deployment is **not affected** by this infrastructure. These environments use separate:
- VPC networks
- GKE clusters  
- DNS zones
- Static IPs
