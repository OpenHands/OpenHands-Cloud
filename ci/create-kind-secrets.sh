#!/usr/bin/env bash

# Create deterministic, non-sensitive secrets for the KinD test installation.
set -euo pipefail

namespace="${1:-openhands}"

apply_secret() {
  kubectl -n "$namespace" create secret generic "$@" --dry-run=client -o yaml |
    kubectl -n "$namespace" apply -f -
}

apply_secret jwt-secret \
  --from-literal=jwt-secret=ci-jwt-secret

apply_secret keycloak-realm \
  --from-literal=realm-name=openhands \
  --from-literal=server-url=http://keycloak \
  --from-literal=client-id=openhands \
  --from-literal=client-secret=ci-keycloak-client-secret \
  --from-literal=smtp-password=ci-smtp-password

apply_secret keycloak-admin \
  --from-literal=admin-password=ci-keycloak-admin-password

apply_secret postgres-password \
  --from-literal=username=postgres \
  --from-literal=password=ci-postgres-password \
  --from-literal=postgres-password=ci-postgres-password

apply_secret redis \
  --from-literal=redis-password=ci-redis-password

apply_secret lite-llm-api-key \
  --from-literal=lite-llm-api-key=sk-ci-litellm-master-key

apply_secret admin-password \
  --from-literal=admin-password=ci-openhands-admin-password

apply_secret default-api-key \
  --from-literal=default-api-key=ci-runtime-api-key

apply_secret sandbox-api-key \
  --from-literal=sandbox-api-key=ci-runtime-api-key

apply_secret litellm-env-secrets \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-ci-placeholder
