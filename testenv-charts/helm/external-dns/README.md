# external-dns Configuration

This directory contains Helm values for external-dns, which automatically manages DNS records in Google Cloud DNS based on Kubernetes Ingress and Service resources.

## Installation

```bash
# Add Bitnami Helm repository
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

# For path-based routing environments
helm install external-dns bitnami/external-dns \
  --namespace external-dns \
  --create-namespace \
  --set google.project=staging-092324 \
  --set domainFilters[0]=your-domain.com \
  --set txtOwnerId=single-cluster-path \
  -f values.yaml \
  -f values-path-routing.yaml

# For subdomain-based routing environments
helm install external-dns bitnami/external-dns \
  --namespace external-dns \
  --create-namespace \
  --set google.project=staging-092324 \
  --set domainFilters[0]=your-domain.com \
  --set txtOwnerId=single-cluster-subdomain \
  -f values.yaml \
  -f values-subdomain-routing.yaml
```

## Environment-Specific Values

| File | Use Case |
|------|----------|
| `values.yaml` | Base configuration (shared) |
| `values-path-routing.yaml` | Path-based routing (single host, multiple paths) |
| `values-subdomain-routing.yaml` | Subdomain routing (multiple hosts) |

## GKE Workload Identity Setup

```bash
# Create GCP service account
gcloud iam service-accounts create external-dns \
  --project=staging-092324 \
  --display-name="external-dns"

# Grant DNS admin permissions
gcloud projects add-iam-policy-binding staging-092324 \
  --member="serviceAccount:external-dns@staging-092324.iam.gserviceaccount.com" \
  --role="roles/dns.admin"

# Allow Kubernetes SA to impersonate GCP SA
gcloud iam service-accounts add-iam-policy-binding \
  external-dns@staging-092324.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:staging-092324.svc.id.goog[external-dns/external-dns]"
```

Then update the values.yaml with the annotation:

```yaml
serviceAccount:
  annotations:
    iam.gke.io/gcp-service-account: external-dns@staging-092324.iam.gserviceaccount.com
```

## Ingress Annotations

To have external-dns manage DNS for an Ingress:

```yaml
# Path-based routing (requires explicit annotation)
annotations:
  external-dns.alpha.kubernetes.io/managed: "true"
  external-dns.alpha.kubernetes.io/hostname: your-domain.com

# Subdomain routing (automatic based on host field)
# No annotations needed - external-dns watches all Ingress hosts
```

## Multi-Cluster Considerations

For multi-cluster environments, use separate `txtOwnerId` values per cluster:

```bash
# Core cluster
--set txtOwnerId=multi-cluster-path-core

# Runtime cluster
--set txtOwnerId=multi-cluster-path-runtime
```

This prevents clusters from overwriting each other's DNS records.
