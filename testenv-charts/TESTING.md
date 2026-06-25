# Infrastructure Testing Guide

This document outlines the testing strategy for validating the OpenHands staging infrastructure before and after deployment.

## Testing Phases

| Phase | Description | Cloud Resources |
|-------|-------------|-----------------|
| 1 | Static Validation | None |
| 2 | Terraform Plan | API calls only |
| 3 | Deploy One Environment | Creates resources |
| 4 | Install Base Services | Helm deployments |
| 5 | Routing Smoke Tests | Test pods |
| 6 | Full OpenHands Deployment | Complete stack |
| 7 | End-to-End Verification | Manual checks |
| 8 | Multi-Environment Rollout | Remaining envs |

---

## Phase 1: Static Validation (No Cloud Resources)

### Terraform Validation

Validate Terraform configuration syntax and module references:

```bash
cd terraform/gcp/platform-team-sandbox/environments

for env in single-cluster-path single-cluster-subdomain multi-cluster-path multi-cluster-subdomain; do
  echo "=== Validating $env ==="
  cd $env
  terraform init -backend=false
  terraform validate
  cd ..
done
```

### Helm Template Rendering (Dry-Run)

Verify Helm values produce valid Kubernetes manifests:

```bash
cd testenv-charts/helm

# Add required repos
helm repo add jetstack https://charts.jetstack.io
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo add traefik https://traefik.github.io/charts
helm repo update

# cert-manager
helm template cert-manager jetstack/cert-manager \
  -f cert-manager/values.yaml \
  --namespace cert-manager

# external-dns (path routing variant)
helm template external-dns bitnami/external-dns \
  -f external-dns/values.yaml \
  -f external-dns/values-path-routing.yaml \
  --namespace external-dns \
  --set google.project=staging-092324

# external-dns (subdomain routing variant)
helm template external-dns bitnami/external-dns \
  -f external-dns/values.yaml \
  -f external-dns/values-subdomain-routing.yaml \
  --namespace external-dns \
  --set google.project=staging-092324

# traefik (path routing variant)
helm template traefik traefik/traefik \
  -f traefik/values.yaml \
  -f traefik/values-path-routing.yaml \
  --namespace traefik

# traefik (subdomain routing variant)
helm template traefik traefik/traefik \
  -f traefik/values.yaml \
  -f traefik/values-subdomain-routing.yaml \
  --namespace traefik
```

### Kubernetes Manifest Validation

Validate the middleware manifests:

```bash
kubectl apply --dry-run=client -f traefik/middlewares-path-routing.yaml
kubectl apply --dry-run=client -f cert-manager/cluster-issuer.yaml
```

---

## Phase 2: Terraform Plan (Cloud API Calls, No Resources Created)

### Prerequisites

```bash
# Authenticate to GCP
gcloud auth application-default login
gcloud config set project staging-092324

# Verify access
gcloud projects describe staging-092324
```

### Run Terraform Plan

```bash
# Start with single-cluster-path
cd terraform/gcp/platform-team-sandbox/environments/single-cluster-path

# Configure variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with:
#   project_id = "staging-092324"
#   region = "us-central1"
#   domain = "ohe-path.staging.example.com"

# Initialize and plan
terraform init
terraform plan -out=tfplan

# Review the plan for:
# - VPC network creation
# - Subnet CIDR ranges (check for conflicts)
# - GKE cluster configuration
# - Node pool sizing
# - IAM bindings
```

### Plan Checklist

- [ ] No CIDR conflicts with existing staging VPC
- [ ] Correct GCP project ID
- [ ] Appropriate machine types for node pools
- [ ] Expected number of resources to create

---

## Phase 3: Deploy One Environment (Incremental)

Start with **single-cluster-path** (simplest configuration).

### Apply Terraform

```bash
cd terraform/gcp/platform-team-sandbox/environments/single-cluster-path
terraform apply
```

### Configure kubectl

```bash
gcloud container clusters get-credentials ohe-single-path \
  --region us-central1 \
  --project staging-092324
```

### Verify Cluster Health

```bash
kubectl get nodes
kubectl get namespaces
kubectl cluster-info
```

---

## Phase 4: Install Base Services & Test Routing

### Install cert-manager

```bash
helm repo add jetstack https://charts.jetstack.io
helm repo update

helm install cert-manager jetstack/cert-manager \
  -n cert-manager --create-namespace \
  --set installCRDs=true \
  -f testenv-charts/helm/cert-manager/values.yaml

# Verify
kubectl get pods -n cert-manager
kubectl wait --for=condition=Ready pods --all -n cert-manager --timeout=120s

# Apply ClusterIssuer
kubectl apply -f testenv-charts/helm/cert-manager/cluster-issuer.yaml
```

### Install Traefik (Path Routing)

```bash
helm repo add traefik https://traefik.github.io/charts
helm repo update

helm install traefik traefik/traefik \
  -n traefik --create-namespace \
  -f testenv-charts/helm/traefik/values.yaml \
  -f testenv-charts/helm/traefik/values-path-routing.yaml

# Apply middlewares
kubectl apply -f testenv-charts/helm/traefik/middlewares-path-routing.yaml

# Get Load Balancer IP
kubectl get svc -n traefik traefik -w
# Wait for EXTERNAL-IP to be assigned (may take 1-2 minutes)
```

### Install external-dns (Optional for testing)

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

helm install external-dns bitnami/external-dns \
  -n external-dns --create-namespace \
  -f testenv-charts/helm/external-dns/values.yaml \
  -f testenv-charts/helm/external-dns/values-path-routing.yaml \
  --set google.project=staging-092324 \
  --set domainFilters[0]=your-domain.com
```

---

## Phase 5: Routing Smoke Tests

### Deploy Echo Server

```bash
kubectl create namespace test-routing

cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: echo-server
  namespace: test-routing
spec:
  replicas: 1
  selector:
    matchLabels:
      app: echo-server
  template:
    metadata:
      labels:
        app: echo-server
    spec:
      containers:
      - name: echo
        image: ealen/echo-server:latest
        ports:
        - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: echo-service
  namespace: test-routing
spec:
  selector:
    app: echo-server
  ports:
  - port: 80
    targetPort: 80
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: echo-ingress
  namespace: test-routing
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: websecure
spec:
  ingressClassName: traefik
  rules:
  - host: test.your-domain.com
    http:
      paths:
      - path: /echo
        pathType: Prefix
        backend:
          service:
            name: echo-service
            port:
              number: 80
EOF
```

### Test Routes

```bash
# Get the Load Balancer IP
LB_IP=$(kubectl get svc -n traefik traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Test with Host header (bypasses DNS)
curl -k "https://${LB_IP}/echo" -H "Host: test.your-domain.com"

# Expected: JSON response from echo server with request details
```

### Cleanup Test Resources

```bash
kubectl delete namespace test-routing
```

---

## Phase 6: Full OpenHands Deployment Test

### Create Environment Values

```bash
cat > /tmp/test-values-single-cluster-path.yaml <<EOF
ingress:
  enabled: true
  host: ohe-path.staging.all-hands.dev
  class: traefik
  
tls:
  enabled: true
  env: staging

# Add other required values from charts/openhands/values.yaml
EOF
```

### Deploy OpenHands

```bash
cd /path/to/OpenHands-Cloud

helm install openhands charts/openhands \
  -n openhands --create-namespace \
  -f /tmp/test-values-single-cluster-path.yaml
```

### Verify Deployment

```bash
kubectl get pods -n openhands
kubectl get ingress -n openhands
kubectl get certificates -n openhands
```

---

## Phase 7: End-to-End Verification Checklist

| Test | Command/Action | Expected Result |
|------|----------------|-----------------|
| Cluster health | `kubectl get nodes` | All nodes `Ready` |
| Ingress controller | `kubectl get svc -n traefik` | External IP assigned |
| TLS certificates | `kubectl get certificates -A` | All `Ready=True` |
| DNS resolution | `nslookup ohe-path.staging.all-hands.dev` | Resolves to LB IP |
| HTTPS access | `curl -I https://ohe-path.staging.all-hands.dev` | 200 OK |
| Path routing | `curl https://domain.com/api/health` | API responds |
| UI loads | Browser: `https://domain.com` | OpenHands UI renders |
| Auth flow | Click "Sign In" | Keycloak redirects correctly |
| Runtime API | Create a conversation | Runtime pod spins up |

---

## Phase 8: Multi-Environment Rollout

Once `single-cluster-path` works, repeat phases 3-7 for:

1. **single-cluster-subdomain** - Test wildcard DNS + subdomain routing
2. **multi-cluster-path** - Test cross-cluster communication
3. **multi-cluster-subdomain** - Full production-like setup

### Environment-Specific Notes

#### single-cluster-subdomain
- Requires wildcard TLS certificate (DNS-01 challenge)
- Use `values-subdomain-routing.yaml` for traefik and external-dns
- Test with multiple subdomains: `app.`, `api.`, `runtime.`

#### multi-cluster-path
- Deploy Terraform creates 2 clusters
- Core cluster: OpenHands, Keycloak, LiteLLM
- Runtime cluster: Runtime API, warm pools
- Need to configure cross-cluster networking

#### multi-cluster-subdomain
- Same as multi-cluster-path but with subdomain routing
- Each cluster can have dedicated subdomains

---

## Cleanup

### Remove OpenHands

```bash
helm uninstall openhands -n openhands
kubectl delete namespace openhands
```

### Remove Base Services

```bash
helm uninstall traefik -n traefik
helm uninstall cert-manager -n cert-manager
helm uninstall external-dns -n external-dns

kubectl delete namespace traefik
kubectl delete namespace cert-manager
kubectl delete namespace external-dns
```

### Destroy Infrastructure

```bash
cd terraform/gcp/platform-team-sandbox/environments/single-cluster-path
terraform destroy
```

---

## Troubleshooting

### Terraform Issues

```bash
# Debug provider issues
TF_LOG=DEBUG terraform plan

# Check state
terraform state list
terraform state show <resource>
```

### Helm Issues

```bash
# Check release status
helm list -A
helm status <release> -n <namespace>

# Get manifest that was deployed
helm get manifest <release> -n <namespace>
```

### Kubernetes Issues

```bash
# Check pod logs
kubectl logs -n <namespace> <pod>

# Describe resources
kubectl describe ingress -n <namespace> <ingress>
kubectl describe certificate -n <namespace> <cert>

# Check events
kubectl get events -n <namespace> --sort-by='.lastTimestamp'
```

### TLS Certificate Issues

```bash
# Check certificate status
kubectl get certificates -A
kubectl describe certificate <name> -n <namespace>

# Check cert-manager logs
kubectl logs -n cert-manager -l app=cert-manager

# Check challenges
kubectl get challenges -A
```

### DNS Issues

```bash
# Check external-dns logs
kubectl logs -n external-dns -l app.kubernetes.io/name=external-dns

# Verify DNS records in GCP
gcloud dns record-sets list --zone=<zone-name>
```

---

## Key Warnings

1. **VPC/Subnet conflicts**: Ensure CIDR ranges don't overlap with existing staging
2. **DNS propagation**: external-dns may take 2-5 minutes to create records
3. **TLS rate limits**: Let's Encrypt has rate limits - use staging issuer first
4. **Cross-cluster networking**: Multi-cluster setups need VPC peering or shared VPC
5. **Load balancer quota**: GCP has limits per project (check quotas first)
