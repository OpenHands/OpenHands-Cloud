# OpenHands Enterprise Staging Environment - Full Deployment Guide

This guide provides complete step-by-step instructions for deploying the OpenHands Enterprise staging environment in the `platform-team-sandbox` GCP project from scratch.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Architecture Overview](#architecture-overview)
3. [Phase 1: GCP Project Setup](#phase-1-gcp-project-setup)
4. [Phase 2: Terraform Infrastructure](#phase-2-terraform-infrastructure)
5. [Phase 3: Kubernetes Base Components](#phase-3-kubernetes-base-components)
6. [Phase 4: Shared Services](#phase-4-shared-services)
7. [Phase 5: OpenHands Deployment](#phase-5-openhands-deployment)
8. [Phase 6: Verification](#phase-6-verification)
9. [Deploying Your Own Branch](#deploying-your-own-branch)
10. [Teardown](#teardown)
11. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Tools

```bash
# Verify all tools are installed
gcloud version          # Google Cloud SDK >= 450.0.0
terraform version       # Terraform >= 1.5.0
helm version           # Helm >= 3.12.0
kubectl version        # kubectl >= 1.28.0
```

### Required Access

- **GCP Project**: `platform-team-sandbox-62793` with Owner or Editor role
- **GitHub**: Access to `ghcr.io/all-hands-ai` container registry
- **Domain**: Access to configure DNS for `platform-team.all-hands.dev`

### Environment Variables

Set these before starting:

```bash
export GCP_PROJECT_ID="platform-team-sandbox-62793"
export GCP_REGION="us-central1"
export DOMAIN="ohe-staging.platform-team.all-hands.dev"
export ACME_EMAIL="platform-team@all-hands.dev"  # For Let's Encrypt notifications
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         platform-team-sandbox GCP Project                    │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                        GKE Cluster (ohe-staging)                        │ │
│  │                                                                          │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │ │
│  │  │   traefik    │  │ cert-manager │  │ external-dns │                  │ │
│  │  │  namespace   │  │  namespace   │  │  namespace   │                  │ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘                  │ │
│  │                                                                          │ │
│  │  ┌──────────────┐  ┌──────────────────────────────────────────────────┐│ │
│  │  │ shared-auth  │  │                  openhands                        ││ │
│  │  │  (Keycloak)  │  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐││ │
│  │  └──────────────┘  │  │OpenHands│ │Runtime  │ │PostgreSQL│ │  Redis  │││ │
│  │                     │  │  App    │ │  API    │ │         │ │         │││ │
│  │                     │  └─────────┘ └─────────┘ └─────────┘ └─────────┘││ │
│  │                     └──────────────────────────────────────────────────┘│ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  Cloud DNS Zone: ohe-staging.platform-team.all-hands.dev               │ │
│  │  └── *.ohe-staging.platform-team.all-hands.dev → Traefik LB IP         │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Components

| Component | Purpose | Location |
|-----------|---------|----------|
| GKE Cluster | Kubernetes cluster for all workloads | `terraform/gcp/platform-team-sandbox/` |
| Cloud DNS | DNS zone for staging domain | `terraform/gcp/staging-dns/` |
| Traefik | Ingress controller with TLS termination | `testenv-charts/helm/traefik/` |
| cert-manager | Automatic TLS certificate management | `testenv-charts/helm/cert-manager/` |
| external-dns | Automatic DNS record management | `testenv-charts/helm/external-dns/` |
| Keycloak | Shared authentication (optional) | `terraform/gcp/shared-auth/` |
| OpenHands | Main application | `charts/openhands/` |
| Runtime API | Runtime management service | `charts/runtime-api/` |

---

## Phase 1: GCP Project Setup

### 1.1 Authenticate with GCP

```bash
# Login to GCP
gcloud auth login
gcloud auth application-default login

# Set project
gcloud config set project ${GCP_PROJECT_ID}
```

### 1.2 Enable Required APIs

```bash
gcloud services enable \
  container.googleapis.com \
  compute.googleapis.com \
  dns.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com \
  servicenetworking.googleapis.com
```

### 1.3 Create Terraform State Bucket (Optional but Recommended)

```bash
# Create bucket for Terraform state
gsutil mb -p ${GCP_PROJECT_ID} -l ${GCP_REGION} gs://${GCP_PROJECT_ID}-terraform-state

# Enable versioning
gsutil versioning set on gs://${GCP_PROJECT_ID}-terraform-state
```

---

## Phase 2: Terraform Infrastructure

### 2.1 Deploy DNS Infrastructure

```bash
cd terraform/gcp/staging-dns

# Create terraform.tfvars
cat > terraform.tfvars << EOF
project_id = "${GCP_PROJECT_ID}"
region     = "${GCP_REGION}"

# DNS zone configuration
zone_name   = "ohe-staging-platform-team-all-hands-dot-dev"
domain_name = "ohe-staging.platform-team.all-hands.dev"

# Parent zone (for NS delegation)
parent_zone_name    = "platform-team-all-hands-dot-dev"
parent_zone_project = "${GCP_PROJECT_ID}"
EOF

# Initialize and apply
terraform init
terraform plan -out=tfplan
terraform apply tfplan

# Note the nameservers output for verification
terraform output nameservers
```

### 2.2 Deploy GKE Cluster

```bash
cd terraform/gcp/platform-team-sandbox/environments/single-cluster-subdomain

# Create terraform.tfvars
cat > terraform.tfvars << EOF
project_id       = "${GCP_PROJECT_ID}"
region           = "${GCP_REGION}"
environment_name = "ohe-staging"
domain           = "ohe-staging.platform-team.all-hands.dev"

# DNS zone is managed separately
create_dns_zone = false

# Cluster configuration
enable_autopilot    = false
deletion_protection = false

# Main node pool
node_machine_type       = "e2-standard-4"
node_pool_min_count     = 1
node_pool_max_count     = 5
node_pool_initial_count = 2

# Runtime node pool (for OpenHands agent runtimes)
create_runtime_node_pool        = true
runtime_node_machine_type       = "e2-standard-8"
runtime_node_pool_min_count     = 0
runtime_node_pool_max_count     = 10
runtime_node_pool_initial_count = 1

# Network settings
enable_private_nodes = true
master_authorized_networks = [
  {
    cidr_block   = "0.0.0.0/0"
    display_name = "All networks"
  }
]

labels = {
  team        = "platform"
  environment = "staging"
  managed-by  = "terraform"
}
EOF

# Optional: Configure remote state backend
# Uncomment and edit the backend block in main.tf:
# backend "gcs" {
#   bucket = "${GCP_PROJECT_ID}-terraform-state"
#   prefix = "openhands/single-cluster-subdomain"
# }

# Initialize and apply
terraform init
terraform plan -out=tfplan
terraform apply tfplan

# Get cluster credentials
gcloud container clusters get-credentials ohe-staging-cluster \
  --region ${GCP_REGION} \
  --project ${GCP_PROJECT_ID}

# Verify cluster access
kubectl get nodes
```

---

## Phase 3: Kubernetes Base Components

### 3.1 Add Helm Repositories

```bash
helm repo add jetstack https://charts.jetstack.io
helm repo add traefik https://traefik.github.io/charts
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update
```

### 3.2 Install cert-manager

```bash
# Install cert-manager with CRDs
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --version v1.14.4 \
  --set installCRDs=true \
  -f testenv-charts/helm/cert-manager/values.yaml

# Wait for cert-manager to be ready
kubectl wait --for=condition=Available deployment --all -n cert-manager --timeout=300s
```

### 3.3 Create DNS01 Solver Service Account

For wildcard certificates, cert-manager needs access to Cloud DNS:

```bash
# Create service account
gcloud iam service-accounts create cert-manager-dns \
  --display-name="cert-manager DNS solver" \
  --project=${GCP_PROJECT_ID}

# Grant DNS admin role
gcloud projects add-iam-policy-binding ${GCP_PROJECT_ID} \
  --member="serviceAccount:cert-manager-dns@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/dns.admin"

# Create key and store as K8s secret
gcloud iam service-accounts keys create /tmp/dns-solver-key.json \
  --iam-account=cert-manager-dns@${GCP_PROJECT_ID}.iam.gserviceaccount.com

kubectl create secret generic clouddns-dns01-solver-svc-acct \
  --from-file=key.json=/tmp/dns-solver-key.json \
  -n cert-manager

# Clean up local key file
rm /tmp/dns-solver-key.json
```

### 3.4 Apply ClusterIssuers

```bash
# Substitute variables and apply
export ACME_EMAIL="${ACME_EMAIL}"
export GCP_PROJECT_ID="${GCP_PROJECT_ID}"

envsubst < testenv-charts/helm/cert-manager/cluster-issuer.yaml | kubectl apply -f -

# Verify ClusterIssuers
kubectl get clusterissuers
```

### 3.5 Install Traefik

```bash
# Get the static IP created by Terraform
INGRESS_IP=$(gcloud compute addresses describe ohe-staging-ingress-ip \
  --global --format='get(address)' --project=${GCP_PROJECT_ID})

echo "Traefik will use IP: ${INGRESS_IP}"

# Install Traefik
helm install traefik traefik/traefik \
  --namespace traefik \
  --create-namespace \
  --version 26.1.0 \
  -f testenv-charts/helm/traefik/values.yaml \
  -f testenv-charts/helm/traefik/values-subdomain-routing.yaml \
  --set service.spec.loadBalancerIP=${INGRESS_IP}

# Wait for Traefik to be ready
kubectl wait --for=condition=Available deployment traefik -n traefik --timeout=300s

# Verify LoadBalancer has the correct IP
kubectl get svc traefik -n traefik
```

### 3.6 Create Wildcard Certificate

```bash
# Create certificate request for wildcard domain
cat << EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: ohe-staging-wildcard-cert
  namespace: traefik
spec:
  secretName: ohe-staging-wildcard-tls
  issuerRef:
    name: letsencrypt-dns
    kind: ClusterIssuer
  dnsNames:
    - "ohe-staging.platform-team.all-hands.dev"
    - "*.ohe-staging.platform-team.all-hands.dev"
EOF

# Wait for certificate to be ready (may take 1-2 minutes)
kubectl wait --for=condition=Ready certificate ohe-staging-wildcard-cert \
  -n traefik --timeout=300s

# Verify certificate
kubectl get certificate -n traefik
kubectl describe certificate ohe-staging-wildcard-cert -n traefik
```

### 3.7 Configure Traefik Default TLS

```bash
# Create TLSStore to use wildcard cert as default
cat << EOF | kubectl apply -f -
apiVersion: traefik.io/v1alpha1
kind: TLSStore
metadata:
  name: default
  namespace: traefik
spec:
  defaultCertificate:
    secretName: ohe-staging-wildcard-tls
EOF
```

### 3.8 Install external-dns (Optional)

If you want automatic DNS record management:

```bash
helm install external-dns bitnami/external-dns \
  --namespace external-dns \
  --create-namespace \
  -f testenv-charts/helm/external-dns/values.yaml \
  -f testenv-charts/helm/external-dns/values-subdomain-routing.yaml \
  --set provider=google \
  --set google.project=${GCP_PROJECT_ID} \
  --set domainFilters[0]=${DOMAIN}
```

---

## Phase 4: Shared Services

### 4.1 Create OpenHands Namespace and Secrets

```bash
# Create namespace
kubectl create namespace openhands

# Create GitHub Container Registry secret
kubectl create secret docker-registry ghcr-login-secret \
  --namespace openhands \
  --docker-server=ghcr.io \
  --docker-username=YOUR_GITHUB_USERNAME \
  --docker-password=YOUR_GITHUB_PAT

# Copy wildcard TLS cert to openhands namespace
kubectl get secret ohe-staging-wildcard-tls -n traefik -o yaml | \
  sed 's/namespace: traefik/namespace: openhands/' | \
  kubectl apply -f -
```

### 4.2 Create LiteLLM API Keys Secret (Shared Infrastructure)

LiteLLM proxies all LLM API calls and requires API keys for the configured providers.
This secret is **shared infrastructure** - all branch deployments in the cluster use this
single secret to access LLM services. You only need to create it once per cluster.

```bash
# Create the litellm-env-secrets with your LLM provider API keys
kubectl create secret generic litellm-env-secrets \
  --namespace openhands \
  --from-literal=ANTHROPIC_API_KEY="your-anthropic-api-key" \
  --from-literal=OPENAI_API_KEY="your-openai-api-key"  # Optional

# Verify the secret was created
kubectl get secret litellm-env-secrets -n openhands
```

> **Note**: Branch deployments automatically inherit access to LiteLLM through the shared
> `litellm-helm` service in the `openhands` namespace. Individual branches do NOT need
> their own API keys - the keys in `litellm-env-secrets` are used by the shared LiteLLM
> instance which all deployments connect to.

### 4.3 Deploy Shared Keycloak (Optional)

If you want shared authentication across branch deployments:

```bash
cd terraform/gcp/shared-auth

# Create terraform.tfvars (see terraform.tfvars.example)
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your SAML settings

terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

---

## Phase 5: OpenHands Deployment

### 5.1 Deploy OpenHands with Helm

```bash
cd /path/to/OpenHands-Cloud

# Deploy main OpenHands instance
helm install openhands ./charts/openhands \
  --namespace openhands \
  -f testenv-charts/helm/environments/single-cluster-subdomain/values-openhands.yaml \
  --set branchSanitized=main \
  --set image.tag=main \
  --timeout 10m

# Wait for deployment
kubectl wait --for=condition=Available deployment -l app=openhands \
  -n openhands --timeout=600s
```

### 5.2 Verify Deployment

```bash
# Check all pods are running
kubectl get pods -n openhands

# Check ingress
kubectl get ingress -n openhands

# Check services
kubectl get svc -n openhands
```

---

## Phase 6: Verification

### 6.1 DNS Verification

```bash
# Verify DNS resolution
dig +short ${DOMAIN}
dig +short test.${DOMAIN}

# Both should return the Traefik LoadBalancer IP
```

### 6.2 TLS Verification

```bash
# Check certificate
curl -vI https://${DOMAIN} 2>&1 | grep -A5 "Server certificate"

# Should show Let's Encrypt certificate for *.ohe-staging.platform-team.all-hands.dev
```

### 6.3 Application Verification

```bash
# Test root endpoint
curl -s https://${DOMAIN}/health

# Test with a subdomain
curl -s https://main.${DOMAIN}/health
```

---

## Deploying Your Own Branch

Once the infrastructure is set up, developers can deploy their own branches:

### Quick Deploy

```bash
export BRANCH_NAME="your-feature-branch"
export NAMESPACE="openhands-${BRANCH_NAME}"

# Create namespace
kubectl create namespace ${NAMESPACE}

# Copy required secrets
kubectl get secret ghcr-login-secret -n openhands -o yaml | \
  sed "s/namespace: openhands/namespace: ${NAMESPACE}/" | \
  kubectl apply -f -

kubectl get secret ohe-staging-wildcard-tls -n traefik -o yaml | \
  sed "s/namespace: traefik/namespace: ${NAMESPACE}/" | \
  kubectl apply -f -

# Create minimal values file
cat > my-branch-values.yaml << EOF
image:
  repository: ghcr.io/all-hands-ai/openhands
  tag: "${BRANCH_NAME}"

branchSanitized: "${BRANCH_NAME}"

ingress:
  enabled: true
  host: ohe-staging.platform-team.all-hands.dev
  routingMode: subdomain
  prefixWithBranch: true
  serviceRoutingMode: path

deployment:
  replicas: 1
EOF

# Deploy
helm install openhands-${BRANCH_NAME} ./charts/openhands \
  --namespace ${NAMESPACE} \
  -f testenv-charts/helm/environments/single-cluster-subdomain/values-openhands.yaml \
  -f my-branch-values.yaml

# Access at: https://${BRANCH_NAME}.ohe-staging.platform-team.all-hands.dev
```

### Cleanup Branch Deployment

```bash
helm uninstall openhands-${BRANCH_NAME} -n ${NAMESPACE}
kubectl delete namespace ${NAMESPACE}
```

For more details, see [BRANCH_DEPLOYMENTS.md](BRANCH_DEPLOYMENTS.md).

---

## Teardown

To completely remove the staging environment:

### 1. Remove OpenHands Deployments

```bash
# List all helm releases
helm list -A | grep openhands

# Uninstall each release
helm uninstall openhands -n openhands
# ... repeat for other releases

kubectl delete namespace openhands
```

### 2. Remove Base Components

```bash
helm uninstall external-dns -n external-dns
helm uninstall traefik -n traefik
helm uninstall cert-manager -n cert-manager

kubectl delete namespace external-dns traefik cert-manager
```

### 3. Remove Terraform Infrastructure

```bash
# Remove GKE cluster
cd terraform/gcp/platform-team-sandbox/environments/single-cluster-subdomain
terraform destroy

# Remove DNS infrastructure
cd terraform/gcp/staging-dns
terraform destroy
```

### 4. Cleanup GCP Resources

```bash
# Delete service account
gcloud iam service-accounts delete \
  cert-manager-dns@${GCP_PROJECT_ID}.iam.gserviceaccount.com

# Delete static IP (if not managed by Terraform)
gcloud compute addresses delete ohe-staging-ingress-ip --global
```

---

## Troubleshooting

### GKE Cluster Issues

```bash
# Check cluster status
gcloud container clusters describe ohe-staging-cluster \
  --region ${GCP_REGION} --project ${GCP_PROJECT_ID}

# Check node pool status
kubectl get nodes -o wide

# Check for resource constraints
kubectl describe nodes | grep -A5 "Allocated resources"
```

### DNS Issues

```bash
# Verify Cloud DNS zone
gcloud dns managed-zones describe ohe-staging-platform-team-all-hands-dot-dev \
  --project=${GCP_PROJECT_ID}

# List DNS records
gcloud dns record-sets list \
  --zone=ohe-staging-platform-team-all-hands-dot-dev \
  --project=${GCP_PROJECT_ID}

# Test resolution
dig +trace ${DOMAIN}
```

### Certificate Issues

```bash
# Check certificate status
kubectl get certificate -A
kubectl describe certificate ohe-staging-wildcard-cert -n traefik

# Check for ACME challenges
kubectl get challenges -A
kubectl describe challenges -A

# Check cert-manager logs
kubectl logs -n cert-manager -l app=cert-manager -f
```

### Traefik Issues

```bash
# Check Traefik logs
kubectl logs -n traefik -l app.kubernetes.io/name=traefik -f

# Check IngressRoutes
kubectl get ingressroute -A

# Check TLS configuration
kubectl get tlsstore -A
kubectl describe tlsstore default -n traefik
```

### OpenHands Issues

```bash
# Check pod status
kubectl get pods -n openhands -o wide
kubectl describe pod -n openhands -l app=openhands

# Check logs
kubectl logs -n openhands -l app=openhands -f --all-containers

# Check database connectivity
kubectl exec -it -n openhands deploy/openhands -- \
  psql -h openhands-postgresql -U postgres -c "SELECT 1"
```

### Common Errors

| Error | Likely Cause | Solution |
|-------|--------------|----------|
| `Certificate not ready` | DNS-01 challenge failing | Check Cloud DNS permissions for cert-manager SA |
| `502 Bad Gateway` | Backend pods not ready | Check pod logs and health endpoints |
| `Connection refused` | Service not exposed correctly | Verify ingress and service configurations |
| `Name or service not known` | DNS not propagated | Wait for DNS propagation (up to 5 min) |
| `TLS handshake failure` | Certificate not loaded | Check TLSStore and secret existence |

---

## Quick Reference

### Useful Commands

```bash
# Get cluster credentials
gcloud container clusters get-credentials ohe-staging-cluster \
  --region us-central1 --project ${GCP_PROJECT_ID}

# Watch all pods
watch kubectl get pods -A

# Check ingress IPs
kubectl get svc -A | grep LoadBalancer

# View all certificates
kubectl get certificate -A

# Quick health check
curl -sk https://${DOMAIN}/health | jq .
```

### Important URLs

| Service | URL |
|---------|-----|
| Main App | `https://ohe-staging.platform-team.all-hands.dev` |
| Branch Deploy | `https://<branch>.ohe-staging.platform-team.all-hands.dev` |
| Keycloak (if deployed) | `https://auth.ohe-staging.platform-team.all-hands.dev` |
| Traefik Dashboard | `kubectl port-forward -n traefik svc/traefik 9000:9000` then `http://localhost:9000/dashboard/` |

### Key Files

| Purpose | Path |
|---------|------|
| GKE Terraform | `terraform/gcp/platform-team-sandbox/environments/single-cluster-subdomain/` |
| DNS Terraform | `terraform/gcp/staging-dns/` |
| Helm values (subdomain) | `testenv-charts/helm/environments/single-cluster-subdomain/values-openhands.yaml` |
| Branch deployment guide | `testenv-charts/BRANCH_DEPLOYMENTS.md` |
| cert-manager values | `testenv-charts/helm/cert-manager/` |
| Traefik values | `testenv-charts/helm/traefik/` |
