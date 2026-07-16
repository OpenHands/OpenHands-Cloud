#!/usr/bin/env bash
# Create the secrets the openhands chart expects, in the target namespace.
#
# Required env:
#   GITHUB_APP_ID, GITHUB_APP_SLUG, GITHUB_APP_CLIENT_ID,
#   GITHUB_APP_CLIENT_SECRET, GITHUB_APP_PRIVATE_KEY_FILE, GITHUB_APP_WEBHOOK_SECRET
#     — from scripts/create_github_app (run with --base-domain $BASE_DOMAIN)
#   ANTHROPIC_API_KEY — real key so conversations can call the LLM
set -euo pipefail

namespace="${NAMESPACE:-openhands}"

: "${GITHUB_APP_ID:?run scripts/create_github_app and export its output}"
: "${GITHUB_APP_SLUG:?}"
: "${GITHUB_APP_CLIENT_ID:?}"
: "${GITHUB_APP_CLIENT_SECRET:?}"
: "${GITHUB_APP_PRIVATE_KEY_FILE:?path to the .pem the script wrote}"
: "${GITHUB_APP_WEBHOOK_SECRET:?}"
: "${ANTHROPIC_API_KEY:?}"

kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f -

apply_secret() {
  kubectl -n "$namespace" create secret generic "$@" --dry-run=client -o yaml |
    kubectl -n "$namespace" apply -f -
}

rand() { openssl rand -hex 24; }

apply_secret jwt-secret --from-literal=jwt-secret="$(rand)"

# realm-name and client-id MUST be "allhands": the app requests OAuth client
# "allhands" and GitHub Apps created by scripts/create_github_app register the
# /realms/allhands/... broker callback.
apply_secret keycloak-realm \
  --from-literal=realm-name=allhands \
  --from-literal=server-url=http://keycloak \
  --from-literal=client-id=allhands \
  --from-literal=client-secret="$(rand)" \
  --from-literal=smtp-password=local-kind-unused

apply_secret keycloak-admin --from-literal=admin-password="$(rand)"

pg_pass="$(rand)"
apply_secret postgres-password \
  --from-literal=username=postgres \
  --from-literal=password="$pg_pass" \
  --from-literal=postgres-password="$pg_pass"

apply_secret redis --from-literal=redis-password="$(rand)"
apply_secret lite-llm-api-key --from-literal=lite-llm-api-key="sk-$(rand)"
apply_secret admin-password --from-literal=admin-password="$(rand)"

runtime_key="$(rand)"
apply_secret default-api-key --from-literal=default-api-key="$runtime_key"
apply_secret sandbox-api-key --from-literal=sandbox-api-key="$runtime_key"

apply_secret litellm-env-secrets --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"

apply_secret github-app \
  --from-literal=app-id="$GITHUB_APP_ID" \
  --from-literal=app-slug="$GITHUB_APP_SLUG" \
  --from-literal=client-id="$GITHUB_APP_CLIENT_ID" \
  --from-literal=client-secret="$GITHUB_APP_CLIENT_SECRET" \
  --from-file=private-key="$GITHUB_APP_PRIVATE_KEY_FILE" \
  --from-literal=webhook-secret="$GITHUB_APP_WEBHOOK_SECRET"

echo "Secrets ready in namespace '$namespace'."
