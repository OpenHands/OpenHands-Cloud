#!/usr/bin/env bash
# Create the local KinD cluster with ingress, TLS, and in-cluster DNS wired up.
#
# Required env:
#   BASE_DOMAIN    e.g. oh.example.com — DNS for app.$BASE_DOMAIN,
#                  auth.app.$BASE_DOMAIN, runtime-api.$BASE_DOMAIN and
#                  *.runtime.$BASE_DOMAIN must resolve to 127.0.0.1
#   TLS_CERT_FILE  full-chain certificate covering all hostnames above
#   TLS_KEY_FILE   matching private key
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cluster="${KIND_CLUSTER:-openhands-local-kind}"

: "${BASE_DOMAIN:?set BASE_DOMAIN (e.g. oh.example.com)}"
: "${TLS_CERT_FILE:?set TLS_CERT_FILE (full chain covering *.$BASE_DOMAIN, *.app.$BASE_DOMAIN, *.runtime.$BASE_DOMAIN)}"
: "${TLS_KEY_FILE:?set TLS_KEY_FILE}"

kind create cluster --name "$cluster" --config "$script_dir/kind-config.yaml" --wait 120s

# Ingress controller pinned to the ports mapped in kind-config.yaml.
helm repo add traefik https://traefik.github.io/charts >/dev/null
helm repo update traefik >/dev/null
helm upgrade --install traefik traefik/traefik --namespace traefik --create-namespace \
  --set service.spec.type=NodePort \
  --set ports.web.nodePort=30080 \
  --set ports.websecure.nodePort=30443 \
  --wait --timeout 10m

# Serve the real certificate for every host by default.
kubectl create secret tls local-kind-tls -n traefik \
  --cert="$TLS_CERT_FILE" --key="$TLS_KEY_FILE" \
  --dry-run=client -o yaml | kubectl apply -f -
cat <<EOF | kubectl apply -f -
apiVersion: traefik.io/v1alpha1
kind: TLSStore
metadata:
  name: default
  namespace: traefik
spec:
  defaultCertificate:
    secretName: local-kind-tls
EOF

# In-cluster DNS: pods resolving the base domain must reach traefik, not
# 127.0.0.1 (their own loopback). Sandboxes post webhooks to the app URL and
# verify its TLS certificate, so this route is load-bearing.
traefik_ip="$(kubectl get svc -n traefik traefik -o jsonpath='{.spec.clusterIP}')"
corefile="$(kubectl get configmap coredns -n kube-system -o jsonpath='{.data.Corefile}')"
if ! grep -q "$BASE_DOMAIN" <<<"$corefile"; then
  printf '%s\n%s:53 {\n    template IN A {\n        answer "{{ .Name }} 60 IN A %s"\n    }\n}\n' \
    "$corefile" "$BASE_DOMAIN" "$traefik_ip" >/tmp/local-kind-corefile
  kubectl create configmap coredns -n kube-system --from-file=Corefile=/tmp/local-kind-corefile \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl rollout restart -n kube-system deploy/coredns
  kubectl rollout status -n kube-system deploy/coredns --timeout=2m
fi

echo "Cluster '$cluster' ready. Next: create-secrets.sh, then install.sh"
