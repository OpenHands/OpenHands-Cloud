# cert-manager Configuration

This directory contains Helm values and Kubernetes manifests for cert-manager, which provides automatic TLS certificate management for OpenHands staging environments.

## Installation

```bash
# Add Jetstack Helm repository
helm repo add jetstack https://charts.jetstack.io
helm repo update

# Install cert-manager
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --version v1.14.4 \
  -f values.yaml
```

## ClusterIssuers

After installing cert-manager, create the ClusterIssuers:

```bash
# Replace environment variables and apply
export ACME_EMAIL="your-email@example.com"
export GCP_PROJECT_ID="staging-092324"

envsubst < cluster-issuer.yaml | kubectl apply -f -
```

### Available Issuers

| Issuer Name | Use Case | Solver |
|------------|----------|--------|
| `letsencrypt-staging` | Testing (higher rate limits) | HTTP-01 |
| `letsencrypt-production` | Production certificates | HTTP-01 |
| `letsencrypt-dns` | Wildcard certificates (subdomain routing) | DNS-01 (Cloud DNS) |

## Usage in Ingress

Reference the ClusterIssuer in your Ingress annotations:

```yaml
# Path-based routing (HTTP-01 solver)
annotations:
  cert-manager.io/cluster-issuer: letsencrypt-production

# Subdomain routing with wildcard cert (DNS-01 solver)
annotations:
  cert-manager.io/cluster-issuer: letsencrypt-dns
```

## GKE Workload Identity Setup

For DNS-01 solver with Cloud DNS:

```bash
# Create GCP service account
gcloud iam service-accounts create cert-manager \
  --project=staging-092324 \
  --display-name="cert-manager"

# Grant DNS admin permissions
gcloud projects add-iam-policy-binding staging-092324 \
  --member="serviceAccount:cert-manager@staging-092324.iam.gserviceaccount.com" \
  --role="roles/dns.admin"

# Allow Kubernetes SA to impersonate GCP SA
gcloud iam service-accounts add-iam-policy-binding \
  cert-manager@staging-092324.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:staging-092324.svc.id.goog[cert-manager/cert-manager]"

# Annotate the Kubernetes service account (done in values.yaml)
```
