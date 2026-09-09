#!/usr/bin/env bash
# Install the openhands chart onto the EKS cluster. Defaults to the public ghcr
# chart/images (no pull secret, no Replicated license). Set REGISTRY=replicated
# with LICENSE_EMAIL/LICENSE_ID to install from registry.replicated.com instead.
#
# Required env:
#   BASE_DOMAIN     same value used for create-cluster.sh
#   CTX             kube context (default: $CLUSTER.$REGION.eksctl.io if CLUSTER/REGION set)
# Optional env:
#   NAMESPACE       target namespace (default: openhands)
#   STORAGE_CLASS   EBS class with expansion (default: gp3)
#   CLUSTER_ISSUER  cert-manager ClusterIssuer for HTTP-01 (default: letsencrypt)
#   LLM_MODEL       Anthropic model id (default: claude-sonnet-4-5-20250929)
#   CHART_VERSION   chart version to install (default: latest)
#   REGISTRY        "ghcr" (default) or "replicated"
#   LICENSE_EMAIL, LICENSE_ID  required when REGISTRY=replicated
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
namespace="${NAMESPACE:-openhands}"
model="${LLM_MODEL:-claude-sonnet-4-5-20250929}"
export STORAGE_CLASS="${STORAGE_CLASS:-gp3}"
export CLUSTER_ISSUER="${CLUSTER_ISSUER:-letsencrypt}"
export AWS_REGION="${AWS_REGION:-us-west-2}"
registry="${REGISTRY:-ghcr}"

# Bedrock upstream (via LiteLLM IRSA). LLM_MODEL stays the LiteLLM alias
# (model_name); BEDROCK_INFERENCE_PROFILE_ID is the real upstream, e.g.
# us.anthropic.claude-sonnet-4-5-20250929-v1:0
: "${BEDROCK_ROLE_ARN:?set BEDROCK_ROLE_ARN (IAM role ARN for Bedrock IRSA)}"
: "${BEDROCK_INFERENCE_PROFILE_ID:?set BEDROCK_INFERENCE_PROFILE_ID (e.g. us.anthropic.claude-sonnet-4-5-20250929-v1:0)}"
export BEDROCK_ROLE_ARN BEDROCK_INFERENCE_PROFILE_ID

: "${BASE_DOMAIN:?set BASE_DOMAIN}"
if [ -z "${CTX:-}" ]; then
  : "${CLUSTER:?set CTX or CLUSTER+REGION}"; : "${REGION:?set CTX or CLUSTER+REGION}"
  CTX="${CLUSTER}.${REGION}.eksctl.io"
fi

case "$registry" in
  ghcr)
    chart="oci://ghcr.io/openhands/helm-charts/openhands" ;;
  replicated)
    : "${LICENSE_EMAIL:?}"; : "${LICENSE_ID:?}"
    helm registry login registry.replicated.com --username "$LICENSE_EMAIL" --password "$LICENSE_ID"
    chart="oci://registry.replicated.com/openhands/${CHANNEL_SLUG:-unstable}/openhands" ;;
  *) echo "REGISTRY must be ghcr or replicated" >&2; exit 1 ;;
esac

version_flag=()
[ -n "${CHART_VERSION:-}" ] && version_flag=(--version "$CHART_VERSION")

values="$(mktemp)"
BASE_DOMAIN="$BASE_DOMAIN" LLM_MODEL="$model" \
  STORAGE_CLASS="$STORAGE_CLASS" CLUSTER_ISSUER="$CLUSTER_ISSUER" \
  envsubst <"$script_dir/values.yaml.tmpl" >"$values"

helm --kube-context "$CTX" upgrade --install openhands "$chart" \
  ${version_flag[@]+"${version_flag[@]}"} \
  --namespace "$namespace" --create-namespace \
  --values "$values" \
  --timeout 20m

echo "Waiting for workloads (first install pulls several GB of images)..."
kubectl --context "$CTX" wait -n "$namespace" --for=condition=ready pod --all --timeout=20m || {
  echo "Some pods are not ready yet; inspect with: kubectl --context $CTX get pods -n $namespace"
  exit 1
}

echo
echo "OpenHands is up: https://app.$BASE_DOMAIN"
