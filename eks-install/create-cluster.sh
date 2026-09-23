#!/usr/bin/env bash
# Stand up a throwaway EKS cluster wired for an OpenHands Enterprise Helm
# install: EBS CSI driver (with IRSA), a gp3 default storage class that allows
# expansion, Traefik behind an NLB, a Route53 wildcard record, and cert-manager.
#
# Analogous to local-kind/create-cluster.sh, but on real AWS. Every kubectl/helm
# call pins --context so an ambient prod context cannot be hit by accident.
#
# Required env:
#   CLUSTER        cluster name (e.g. openhands-eks-install)
#   REGION         AWS region (e.g. us-east-1)
#   BASE_DOMAIN    a Route53 hosted zone you own; every host is one label under
#                  it (app., auth., runtime-api., {id}-runtime.), so a single
#                  *.$BASE_DOMAIN record covers the whole install
#   HOSTED_ZONE_ID Route53 zone id for BASE_DOMAIN
# Optional env:
#   NODE_TYPE      instance type (default m5.xlarge = 4 vCPU / 16 GiB)
#   NODES          node count (default 3)
#   OWNER,PURPOSE  tags, so orphaned volumes/LB are attributable
set -euo pipefail

: "${CLUSTER:?set CLUSTER}"
: "${REGION:?set REGION}"
: "${BASE_DOMAIN:?set BASE_DOMAIN (a Route53 zone you own)}"
: "${HOSTED_ZONE_ID:?set HOSTED_ZONE_ID}"
node_type="${NODE_TYPE:-m5.xlarge}"
nodes="${NODES:-3}"
owner="${OWNER:-$(whoami)}"
purpose="${PURPOSE:-eks-install}"
CTX="${CLUSTER}.${REGION}.eksctl.io"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. cluster (~15 min — EKS control plane + nodegroup CloudFormation). --with-oidc
#    provisions the IAM OIDC provider that IRSA (EBS CSI, cert-manager) needs.
eksctl create cluster --name "$CLUSTER" --region "$REGION" \
  --nodes "$nodes" --node-type "$node_type" \
  --node-volume-size 100 --node-volume-type gp3 \
  --with-oidc \
  --tags "owner=${owner},purpose=${purpose}"

aws eks update-kubeconfig --name "$CLUSTER" --region "$REGION" --alias "$CTX"

# 2. EBS CSI driver via IRSA. Unlike GKE's pd driver, this is NOT installed by
#    default; without it PVCs stay Pending forever.
eksctl create iamserviceaccount --cluster "$CLUSTER" --region "$REGION" \
  --namespace kube-system --name ebs-csi-controller-sa \
  --role-name "AmazonEKS_EBS_CSI_DriverRole_${CLUSTER}" \
  --attach-policy-arn arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy \
  --approve --role-only
account_id="$(aws sts get-caller-identity --query Account --output text)"
eksctl create addon --cluster "$CLUSTER" --region "$REGION" \
  --name aws-ebs-csi-driver \
  --service-account-role-arn "arn:aws:iam::${account_id}:role/AmazonEKS_EBS_CSI_DriverRole_${CLUSTER}" \
  --force

# 3. gp3 default storage class WITH allowVolumeExpansion (see the manifest).
kubectl --context "$CTX" apply -f "$script_dir/gp3-storageclass.yaml"
# Demote the shipped gp2 class so it is no longer default.
kubectl --context "$CTX" patch storageclass gp2 \
  -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"false"}}}' \
  2>/dev/null || true
kubectl --context "$CTX" get sc

# 4. Traefik behind an NLB. NOTE: on AWS the LoadBalancer exposes a *hostname*,
#    not an IP — this is the key difference from GKE.
helm repo add traefik https://traefik.github.io/charts >/dev/null
helm repo update traefik >/dev/null
helm --kube-context "$CTX" upgrade --install traefik traefik/traefik \
  --namespace traefik --create-namespace \
  --set service.type=LoadBalancer \
  --set 'service.annotations.service\.beta\.kubernetes\.io/aws-load-balancer-type=nlb' \
  --wait --timeout 10m

echo "Waiting for the NLB hostname..."
for _ in $(seq 1 60); do
  LB_HOST="$(kubectl --context "$CTX" -n traefik get svc traefik \
    -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')"
  [ -n "$LB_HOST" ] && break
  sleep 10
done
: "${LB_HOST:?NLB hostname never appeared; check the traefik service}"
echo "NLB: $LB_HOST"

# 5. One wildcard Route53 record covers every host. Because the NLB is a
#    hostname, this is a CNAME (an A-record-with-IP cannot point at an ELB).
#    An apex/alias A record is an alternative but needs the NLB's zone id.
cat >/tmp/eks-wildcard-rrset.json <<JSON
{
  "Comment": "OpenHands EKS test wildcard",
  "Changes": [{
    "Action": "UPSERT",
    "ResourceRecordSet": {
      "Name": "*.${BASE_DOMAIN}",
      "Type": "CNAME",
      "TTL": 300,
      "ResourceRecords": [{"Value": "${LB_HOST}"}]
    }
  }]
}
JSON
aws route53 change-resource-record-sets \
  --hosted-zone-id "$HOSTED_ZONE_ID" \
  --change-batch file:///tmp/eks-wildcard-rrset.json

# 6. cert-manager + issuer. Edit letsencrypt-issuer.yaml's email first.
helm repo add jetstack https://charts.jetstack.io >/dev/null
helm repo update jetstack >/dev/null
helm --kube-context "$CTX" upgrade --install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --set crds.enabled=true --wait --timeout 6m
kubectl --context "$CTX" apply -f "$script_dir/letsencrypt-issuer.yaml"

cat <<EOF

Cluster '$CLUSTER' ready.
  context:  $CTX
  NLB:      $LB_HOST
  wildcard: *.${BASE_DOMAIN} -> $LB_HOST (allow ~1-2 min for DNS to propagate)

Next:
  NAMESPACE=openhands \\
  GITHUB_APP_*=... ANTHROPIC_API_KEY=... \\
    ../local-kind/create-secrets.sh          # reused as-is
  BASE_DOMAIN=$BASE_DOMAIN STORAGE_CLASS=gp3 CLUSTER_ISSUER=letsencrypt \\
  CTX=$CTX ./install.sh
EOF
