# EKS test Helm install

Scripts to stand up an OpenHands Enterprise Helm install on a
throwaway [EKS](https://aws.amazon.com/eks/) cluster — real EBS storage with
expansion, an NLB in front of Traefik, and publicly trusted Let's Encrypt certs.
The AWS counterpart of `../local-kind/` and the GKE flow.

Read `.agents/skills/eks-install.md` for the reasoning and the constraints
that bite; this README is the quick command reference.

## Prerequisites

- `awscli` (v2), `eksctl`, `kubectl`, `helm`, `envsubst`, `python3`.
- AWS credentials with EKS/EC2/IAM/Route53 permission for the target account.
- A **Route53 hosted zone** you control for the base domain, and its zone id.
- A GitHub App from `scripts/create_github_app --base-domain <base>`.
- An Anthropic API key.

## Bring it up

```bash
export CLUSTER=openhands-eks-install REGION=us-east-1
export BASE_DOMAIN=oh.example.com HOSTED_ZONE_ID=ZXXXXXXXXXXXXX

# Edit letsencrypt-issuer.yaml's email before this creates the ClusterIssuer.
./create-cluster.sh

export GITHUB_APP_ID=... GITHUB_APP_SLUG=... GITHUB_APP_CLIENT_ID=... \
       GITHUB_APP_CLIENT_SECRET=... GITHUB_APP_WEBHOOK_SECRET=... \
       GITHUB_APP_PRIVATE_KEY_FILE=../scripts/create_github_app/keys/<app>.pem \
       ANTHROPIC_API_KEY=sk-ant-...
../local-kind/create-secrets.sh          # reused unchanged

export CTX=${CLUSTER}.${REGION}.eksctl.io STORAGE_CLASS=gp3 CLUSTER_ISSUER=letsencrypt
./install.sh                             # first run pulls several GB of images
```

Then open `https://app.$BASE_DOMAIN`, log in with GitHub, and start a
conversation. Add `REGISTRY=replicated LICENSE_EMAIL=... LICENSE_ID=...` to
`install.sh` to install from `registry.replicated.com` instead of ghcr.

## Files

| File | Purpose |
|---|---|
| `create-cluster.sh` | eksctl cluster, EBS CSI addon (IRSA), gp3 default class, Traefik/NLB, Route53 wildcard, cert-manager |
| `gp3-storageclass.yaml` | gp3 CSI storage class with `allowVolumeExpansion: true`, set default |
| `letsencrypt-issuer.yaml` | HTTP-01 ClusterIssuer (+ optional Route53 DNS-01 for the sandbox wildcard) |
| `values.yaml.tmpl` | chart values, rendered with `envsubst` |
| `install.sh` | render values and `helm upgrade --install` (ghcr or replicated) |
| `delete-cluster.sh` | ordered teardown (release NLB → cluster → Route53 → check for orphaned EBS) |

## Tear down

```bash
export CLUSTER=openhands-eks-install REGION=us-east-1 \
       BASE_DOMAIN=oh.example.com HOSTED_ZONE_ID=ZXXXXXXXXXXXXX
./delete-cluster.sh
```

Order matters: the NLB must be released before the VPC/cluster can delete, and
PVCs must be uninstalled (not just the cluster deleted) or their EBS volumes leak.
The script handles both and prints anything left over.

## Not yet validated

The sandbox subdomain routing path (wildcard cert via Route53 DNS-01,
`RUNTIME_CERT_SECRET`) has not been confirmed working end to end on EKS. The
app/auth/runtime-api hosts and GitHub login use ordinary HTTP-01 per-host certs
and are the validated surface.
