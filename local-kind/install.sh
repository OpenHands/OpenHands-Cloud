#!/usr/bin/env bash
# Install the openhands chart from the Replicated registry into the KinD cluster.
#
# Required env:
#   BASE_DOMAIN     same value used for create-cluster.sh
#   LICENSE_EMAIL   email registered on the Replicated license
#   LICENSE_ID      the license ID (registry password)
# Optional env:
#   CHANNEL_SLUG    Replicated channel slug (default: unstable)
#   NAMESPACE       target namespace (default: openhands)
#   LLM_MODEL       Anthropic model id (default: claude-sonnet-4-5-20250929)
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
namespace="${NAMESPACE:-openhands}"
channel="${CHANNEL_SLUG:-unstable}"
model="${LLM_MODEL:-claude-sonnet-4-5-20250929}"

: "${BASE_DOMAIN:?set BASE_DOMAIN}"
: "${LICENSE_EMAIL:?set LICENSE_EMAIL}"
: "${LICENSE_ID:?set LICENSE_ID}"

helm registry login registry.replicated.com --username "$LICENSE_EMAIL" --password "$LICENSE_ID"

values="$(mktemp)"
BASE_DOMAIN="$BASE_DOMAIN" LLM_MODEL="$model" envsubst <"$script_dir/values.yaml.tmpl" >"$values"

helm upgrade --install openhands "oci://registry.replicated.com/openhands/$channel/openhands" \
  --namespace "$namespace" \
  --values "$script_dir/kind-resources.yaml" \
  --values "$values" \
  --timeout 25m

echo "Waiting for workloads (first install pulls several GB of images)..."
kubectl wait -n "$namespace" --for=condition=ready pod --all --timeout=20m || {
  echo "Some pods are not ready yet; inspect with: kubectl get pods -n $namespace"
  exit 1
}

echo
echo "OpenHands is up: https://app.$BASE_DOMAIN"
