#!/bin/bash
# =============================================================================
# OpenHands Branch Values File Generator
# =============================================================================
# Generates a values.yaml file for deploying an OpenHands branch to the
# platform-team-sandbox staging environment with proper Keycloak SSO setup.
#
# This script handles the tricky parts of branch deployments:
#   1. Pointing to the shared Keycloak (not spinning up a new one)
#   2. Registering redirect URIs with the shared Keycloak
#   3. Computing AUTH_URL correctly for branch subdomains
#   4. Configuring enterprise SSO provider
#   5. Connecting to shared PostgreSQL, Redis, and LiteLLM
#
# Usage:
#   ./generate-branch-values.sh <branch-name> [options]
#
# Options:
#   --image-tag <tag>      Image tag to use (default: main image tag)
#   --image-repo <repo>    Image repository (default: ghcr.io/openhands/enterprise-server)
#   --pr <number>          PR number for comments in generated file
#   --output <file>        Output file path (default: staging/branch-<name>.yaml)
#   --minimal              Generate minimal config (shares most resources)
#   --full                 Generate full config (own postgres, redis, keycloak)
#   --dry-run              Print to stdout instead of writing file
#
# Examples:
#   ./generate-branch-values.sh my-feature
#   ./generate-branch-values.sh console-message --pr 14343 --image-tag sha-3ac78c5
#   ./generate-branch-values.sh test-branch --minimal --dry-run
#
# The generated file should be used with base-values.yaml:
#   helm install openhands-<branch> ./charts/openhands \
#     -f testenv-charts/helm/environments/staging/base-values.yaml \
#     -f testenv-charts/helm/environments/staging/branch-<name>.yaml \
#     -n openhands-<branch>
# =============================================================================

set -e

# Configuration - platform-team-sandbox staging environment
DOMAIN="ohe-staging.platform-team.all-hands.dev"
SHARED_KEYCLOAK_URL="https://auth.ohe-staging.platform-team.all-hands.dev"
REALM_NAME="allhands"
CLIENT_ID="allhands"
DEFAULT_IMAGE_REPO="ghcr.io/openhands/enterprise-server"
DEFAULT_IMAGE_TAG="sha-f50653f"  # Main deployment tag

# Script directory for relative paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/../helm/environments/staging"

# Defaults
IMAGE_TAG="$DEFAULT_IMAGE_TAG"
IMAGE_REPO="$DEFAULT_IMAGE_REPO"
PR_NUMBER=""
OUTPUT_FILE=""
MODE="minimal"  # minimal or full
DRY_RUN=false
BRANCH_NAME=""

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

usage() {
    cat << 'EOF'
Usage: ./generate-branch-values.sh <branch-name> [options]

Options:
  --image-tag <tag>      Image tag to use (default: main image tag)
  --image-repo <repo>    Image repository (default: ghcr.io/openhands/enterprise-server)
  --pr <number>          PR number for comments in generated file
  --output <file>        Output file path (default: staging/branch-<name>.yaml)
  --minimal              Generate minimal config - uses shared postgres/redis (default)
  --full                 Generate full config - spins up own postgres/redis/keycloak
  --dry-run              Print to stdout instead of writing file
  -h, --help             Show this help message

Examples:
  # Generate minimal values for testing a feature branch
  ./generate-branch-values.sh my-feature

  # Generate values for a specific PR with custom image
  ./generate-branch-values.sh console-message --pr 14343 --image-tag sha-3ac78c5

  # Preview what would be generated without creating file
  ./generate-branch-values.sh test-branch --dry-run

The generated file should be used WITH base-values.yaml:

  helm install openhands-<branch> ./charts/openhands \
    -f testenv-charts/helm/environments/staging/base-values.yaml \
    -f testenv-charts/helm/environments/staging/branch-<name>.yaml \
    -n openhands-<branch> --create-namespace
EOF
    exit 1
}

log_info() {
    echo -e "${BLUE}ℹ${NC} $1" >&2
}

log_success() {
    echo -e "${GREEN}✓${NC} $1" >&2
}

log_warn() {
    echo -e "${YELLOW}⚠${NC} $1" >&2
}

log_error() {
    echo -e "${RED}✗${NC} $1" >&2
}

# Parse arguments
if [[ $# -lt 1 ]]; then
    usage
fi

BRANCH_NAME="$1"
shift

while [[ $# -gt 0 ]]; do
    case $1 in
        --image-tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        --image-repo)
            IMAGE_REPO="$2"
            shift 2
            ;;
        --pr)
            PR_NUMBER="$2"
            shift 2
            ;;
        --output)
            OUTPUT_FILE="$2"
            shift 2
            ;;
        --minimal)
            MODE="minimal"
            shift
            ;;
        --full)
            MODE="full"
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            ;;
    esac
done

# Sanitize branch name for Kubernetes naming (lowercase, replace invalid chars)
BRANCH_SANITIZED=$(echo "$BRANCH_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | sed 's/--*/-/g' | sed 's/^-//' | sed 's/-$//')

# Validate branch name
if [[ -z "$BRANCH_SANITIZED" ]]; then
    log_error "Invalid branch name: $BRANCH_NAME"
    exit 1
fi

if [[ ${#BRANCH_SANITIZED} -gt 53 ]]; then
    log_warn "Branch name is very long (${#BRANCH_SANITIZED} chars). May cause issues with Kubernetes naming."
fi

# Default output file
if [[ -z "$OUTPUT_FILE" ]]; then
    OUTPUT_FILE="$OUTPUT_DIR/branch-$BRANCH_SANITIZED.yaml"
fi

# Compute URLs
BRANCH_URL="https://$BRANCH_SANITIZED.$DOMAIN"
AUTH_URL="$SHARED_KEYCLOAK_URL"

log_info "Generating branch values file..."
log_info "  Branch:       $BRANCH_NAME"
log_info "  Sanitized:    $BRANCH_SANITIZED"
log_info "  Mode:         $MODE"
log_info "  Image:        $IMAGE_REPO:$IMAGE_TAG"
log_info "  URL:          $BRANCH_URL"
log_info "  Auth URL:     $AUTH_URL"
if [[ -n "$PR_NUMBER" ]]; then
    log_info "  PR:           #$PR_NUMBER"
fi
log_info "  Output:       $OUTPUT_FILE"

# =============================================================================
# Generate Values File
# =============================================================================

generate_minimal_values() {
    cat << EOF
# =============================================================================
# Branch deployment: $BRANCH_NAME
# =============================================================================
# Generated by generate-branch-values.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
#
# This file overrides base-values.yaml for the $BRANCH_NAME branch deployment.
# Uses shared infrastructure from the 'openhands' namespace.
#
# Deploy with:
#   helm install openhands-$BRANCH_SANITIZED ./charts/openhands \\
#     -f testenv-charts/helm/environments/staging/base-values.yaml \\
#     -f $OUTPUT_FILE \\
#     -n openhands-$BRANCH_SANITIZED --create-namespace
#
# Required secrets in namespace (copy from openhands namespace):
#   kubectl get secret ghcr-login-secret -n openhands -o yaml | \\
#     sed 's/namespace: openhands/namespace: openhands-$BRANCH_SANITIZED/' | kubectl apply -f -
#   kubectl get secret keycloak-admin -n openhands -o yaml | \\
#     sed 's/namespace: openhands/namespace: openhands-$BRANCH_SANITIZED/' | kubectl apply -f -
#   kubectl get secret keycloak-realm -n openhands -o yaml | \\
#     sed 's/namespace: openhands/namespace: openhands-$BRANCH_SANITIZED/' | kubectl apply -f -
#   kubectl get secret ohe-staging-wildcard-tls -n traefik -o yaml | \\
#     sed 's/namespace: traefik/namespace: openhands-$BRANCH_SANITIZED/' | kubectl apply -f -
# =============================================================================

# Branch-specific naming
fullnameOverride: openhands-$BRANCH_SANITIZED

# -----------------------------------------------------------------------------
# Image Configuration
# -----------------------------------------------------------------------------
$(if [[ -n "$PR_NUMBER" ]]; then
cat << PREOF
# Branch-specific images from PR #$PR_NUMBER
# NOTE: Ensure the image exists at $IMAGE_REPO:$IMAGE_TAG
PREOF
fi)
image:
  repository: $IMAGE_REPO
  tag: $IMAGE_TAG
  pullPolicy: Always

# -----------------------------------------------------------------------------
# Shared Keycloak Configuration (CRITICAL for SSO)
# -----------------------------------------------------------------------------
# Use shared Keycloak from openhands namespace
# DO NOT enable a branch-specific Keycloak - it won't have user data
keycloak:
  enabled: false
  # CRITICAL: Set authHost to point to shared Keycloak
  # Without this, the chart computes AUTH_URL as auth.$BRANCH_SANITIZED.$DOMAIN which doesn't exist
  authHost: auth.$DOMAIN
  # Register this branch's redirect URI with the shared Keycloak
  # This is required because Keycloak doesn't support wildcard subdomains
  redirectUriRegistration:
    enabled: true
    keycloakUrl: "$SHARED_KEYCLOAK_URL"
    realmName: "$REALM_NAME"
    clientId: "$CLIENT_ID"
    adminUser: "admin"
    adminPasswordSecret:
      name: "keycloak-admin"
      key: "admin-password"

keycloakConfig:
  enabled: false

# Enable Enterprise SSO button on frontend
# This adds "enterprise_sso" to OH_WEB_CLIENT_PROVIDERS_CONFIGURED via the chart template
enterpriseSSO:
  enabled: true

# -----------------------------------------------------------------------------
# Environment Overrides
# -----------------------------------------------------------------------------
# NOTE: Don't override OH_APP_MODE, CONVERSATION_MANAGER_CLASS, etc. - chart sets them correctly
# NOTE: Don't override OH_WEB_CLIENT_PROVIDERS_CONFIGURED - let chart compute from enterpriseSSO.enabled
env:
  # Runtime Configuration (use shared runtime)
  RUNTIME_URL_PATTERN: https://{runtime_id}.runtime.$DOMAIN
  
  # LLM Proxy (shared)
  OPENHANDS_PROVIDER_BASE_URL: https://llm-proxy.$DOMAIN/
  
  # Features (minimal for branch testing)
  ENABLE_BILLING: "false"
  ENABLE_EXPERIMENT_MANAGER: "false"
  ENABLE_MCP_SEARCH_ENGINE: "false"
  ENABLE_PROACTIVE_CONVERSATION_STARTERS: "false"
  ENABLE_SOLVABILITY_ANALYSIS: "false"
  ENABLE_V1_GITHUB_RESOLVER: "false"
  ENABLE_V1_SLACK_RESOLVER: "false"
  
  # UI Flags
  OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_BILLING: "false"
  OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_JIRA: "false"
  OH_WEB_CLIENT_FEATURE_FLAGS_HIDE_BILLING_PAGE: "true"
  OH_WEB_CLIENT_FEATURE_FLAGS_HIDE_INTEGRATIONS_PAGE: "false"
  OH_WEB_CLIENT_FEATURE_FLAGS_HIDE_USERS_PAGE: "false"
  
  # Access Control (open for testing)
  DUPLICATE_EMAIL_CHECK: "false"
  OH_USER_AUTHORIZER_PREVENT_DUPLICATES: "false"
  EMAIL_PATTERN_BLACKLIST: '%'
  EMAIL_PATTERN_WHITELIST: '%@openhands.dev,%@all-hands.dev'

# -----------------------------------------------------------------------------
# Disable Integrations (no secrets available)
# -----------------------------------------------------------------------------
github:
  enabled: false
githubProxy:
  enabled: false
gitlab:
  enabled: false
gitlabWebhookInstallation:
  enabled: false
slack:
  enabled: false
stripe:
  enabled: false
linear:
  enabled: false
jira:
  enabled: false
resend:
  enabled: false
resendSync:
  enabled: false
bitbucket:
  enabled: false
tavily:
  enabled: false

# -----------------------------------------------------------------------------
# Disable Services (use shared from main deployment)
# -----------------------------------------------------------------------------
runtime-api:
  enabled: false

automation:
  enabled: false
  events:
    enabled: false
automationEvents:
  enabled: false
automationServiceKey:
  enabled: false
automationWebhookSecret:
  enabled: false
automationService:
  eventForwardingEnabled: false

mcp:
  enabled: false
mcpEvents:
  enabled: false

integrations:
  enabled: false
integrationEvents:
  enabled: false

pluginDirectory:
  enabled: false

# -----------------------------------------------------------------------------
# Database (use shared PostgreSQL)
# -----------------------------------------------------------------------------
databaseMigrations:
  waitForDatabase: true
  createDatabases: false
  migrate: false  # Migrations already run by main deployment

postgresql:
  enabled: false

# Use external database connection
externalDatabase:
  host: openhands-postgresql.openhands.svc.cluster.local
  port: 5432
  username: postgres
  database: openhands
  existingSecret: postgres-password
  secretKey: postgres-password

# -----------------------------------------------------------------------------
# Redis (use shared)
# -----------------------------------------------------------------------------
redis:
  enabled: false

redisConnection:
  host: openhands-redis-master.openhands.svc.cluster.local
  port: 6379
  existingSecret: redis
  secretKey: redis-password

# -----------------------------------------------------------------------------
# MinIO (branch-specific for isolation)
# -----------------------------------------------------------------------------
minio:
  enabled: true
  fullnameOverride: openhands-$BRANCH_SANITIZED-minio

# -----------------------------------------------------------------------------
# LiteLLM (use shared)
# -----------------------------------------------------------------------------
litellm-helm:
  enabled: false

litellm:
  url: "http://openhands-litellm.openhands.svc.cluster.local:4000"

# -----------------------------------------------------------------------------
# Ingress Configuration
# -----------------------------------------------------------------------------
# IMPORTANT: Override ingress.host to use branch-specific subdomain
ingress:
  enabled: true
  className: traefik
  host: $BRANCH_SANITIZED.$DOMAIN
  prefixWithBranch: false  # Don't prefix since we're using explicit subdomain
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    traefik.ingress.kubernetes.io/router.entrypoints: websecure
  hosts:
    - host: $BRANCH_SANITIZED.$DOMAIN
      paths:
        - path: /
          pathType: Prefix
  tls:
    - hosts:
        - $BRANCH_SANITIZED.$DOMAIN
      secretName: ohe-staging-wildcard-tls

# -----------------------------------------------------------------------------
# Disable DataDog
# -----------------------------------------------------------------------------
datadog:
  enabled: false
EOF
}

generate_full_values() {
    cat << EOF
# =============================================================================
# Branch deployment: $BRANCH_NAME (Full Mode)
# =============================================================================
# Generated by generate-branch-values.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
#
# This file creates a fully isolated branch deployment with its own:
#   - PostgreSQL database
#   - Redis instance
#   - (Still uses shared Keycloak for user data)
#
# Deploy with:
#   helm install openhands-$BRANCH_SANITIZED ./charts/openhands \\
#     -f testenv-charts/helm/environments/staging/base-values.yaml \\
#     -f $OUTPUT_FILE \\
#     -n openhands-$BRANCH_SANITIZED --create-namespace
# =============================================================================

# Branch-specific naming
fullnameOverride: openhands-$BRANCH_SANITIZED

# -----------------------------------------------------------------------------
# Image Configuration
# -----------------------------------------------------------------------------
$(if [[ -n "$PR_NUMBER" ]]; then
cat << PREOF
# Branch-specific images from PR #$PR_NUMBER
PREOF
fi)
image:
  repository: $IMAGE_REPO
  tag: $IMAGE_TAG
  pullPolicy: Always

# -----------------------------------------------------------------------------
# Shared Keycloak Configuration (CRITICAL for SSO)
# -----------------------------------------------------------------------------
# Even in full mode, use shared Keycloak for user data consistency
keycloak:
  enabled: false
  authHost: auth.$DOMAIN
  redirectUriRegistration:
    enabled: true
    keycloakUrl: "$SHARED_KEYCLOAK_URL"
    realmName: "$REALM_NAME"
    clientId: "$CLIENT_ID"
    adminUser: "admin"
    adminPasswordSecret:
      name: "keycloak-admin"
      key: "admin-password"

keycloakConfig:
  enabled: false

enterpriseSSO:
  enabled: true

# -----------------------------------------------------------------------------
# Environment Overrides
# -----------------------------------------------------------------------------
env:
  RUNTIME_URL_PATTERN: https://{runtime_id}.runtime.$DOMAIN
  OPENHANDS_PROVIDER_BASE_URL: https://llm-proxy.$DOMAIN/
  
  # Enable more features in full mode
  ENABLE_BILLING: "true"
  ENABLE_EXPERIMENT_MANAGER: "true"
  ENABLE_MCP_SEARCH_ENGINE: "true"
  ENABLE_SOLVABILITY_ANALYSIS: "true"

# -----------------------------------------------------------------------------
# Database (branch-specific PostgreSQL)
# -----------------------------------------------------------------------------
postgresql:
  enabled: true
  auth:
    database: openhands
    existingSecret: postgres-password
    username: postgres
  primary:
    persistence:
      enabled: false  # Ephemeral for testing

databaseMigrations:
  waitForDatabase: true
  createDatabases: true
  migrate: true

# -----------------------------------------------------------------------------
# Redis (branch-specific)
# -----------------------------------------------------------------------------
redis:
  enabled: true
  architecture: standalone
  auth:
    enabled: true
    existingSecret: redis
  master:
    persistence:
      enabled: false

# -----------------------------------------------------------------------------
# MinIO (branch-specific)
# -----------------------------------------------------------------------------
minio:
  enabled: true
  fullnameOverride: openhands-$BRANCH_SANITIZED-minio

# -----------------------------------------------------------------------------
# LiteLLM (use shared)
# -----------------------------------------------------------------------------
litellm-helm:
  enabled: false

litellm:
  url: "http://openhands-litellm.openhands.svc.cluster.local:4000"

# -----------------------------------------------------------------------------
# Disable Integrations (no secrets)
# -----------------------------------------------------------------------------
github:
  enabled: false
githubProxy:
  enabled: false
gitlab:
  enabled: false
slack:
  enabled: false
stripe:
  enabled: false
linear:
  enabled: false
jira:
  enabled: false
resend:
  enabled: false
bitbucket:
  enabled: false
tavily:
  enabled: false

# Disable services not needed for branch testing
runtime-api:
  enabled: false
automation:
  enabled: false
mcp:
  enabled: false
integrations:
  enabled: false
pluginDirectory:
  enabled: false

# -----------------------------------------------------------------------------
# Ingress Configuration
# -----------------------------------------------------------------------------
ingress:
  enabled: true
  className: traefik
  host: $BRANCH_SANITIZED.$DOMAIN
  prefixWithBranch: false
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    traefik.ingress.kubernetes.io/router.entrypoints: websecure
  hosts:
    - host: $BRANCH_SANITIZED.$DOMAIN
      paths:
        - path: /
          pathType: Prefix
  tls:
    - hosts:
        - $BRANCH_SANITIZED.$DOMAIN
      secretName: ohe-staging-wildcard-tls

datadog:
  enabled: false
EOF
}

# Generate the appropriate values
if [[ "$MODE" == "minimal" ]]; then
    VALUES_CONTENT=$(generate_minimal_values)
else
    VALUES_CONTENT=$(generate_full_values)
fi

# Output
if [[ "$DRY_RUN" == "true" ]]; then
    echo "$VALUES_CONTENT"
    log_info "(Dry run - no file written)"
else
    # Create output directory if needed
    mkdir -p "$(dirname "$OUTPUT_FILE")"
    
    # Write file
    echo "$VALUES_CONTENT" > "$OUTPUT_FILE"
    log_success "Values file created: $OUTPUT_FILE"
    
    echo ""
    log_info "Next steps:"
    echo ""
    echo "  1. Create namespace and copy secrets:"
    echo "     kubectl create namespace openhands-$BRANCH_SANITIZED"
    echo ""
    echo "     # Copy required secrets from openhands namespace:"
    echo "     for secret in ghcr-login-secret keycloak-admin keycloak-realm; do"
    echo "       kubectl get secret \$secret -n openhands -o yaml | \\"
    echo "         sed 's/namespace: openhands/namespace: openhands-$BRANCH_SANITIZED/' | kubectl apply -f -"
    echo "     done"
    echo ""
    echo "     # Copy TLS secret from traefik namespace:"
    echo "     kubectl get secret ohe-staging-wildcard-tls -n traefik -o yaml | \\"
    echo "       sed 's/namespace: traefik/namespace: openhands-$BRANCH_SANITIZED/' | kubectl apply -f -"
    echo ""
    echo "  2. Deploy with Helm:"
    echo "     helm install openhands-$BRANCH_SANITIZED ./charts/openhands \\"
    echo "       -f testenv-charts/helm/environments/staging/base-values.yaml \\"
    echo "       -f $OUTPUT_FILE \\"
    echo "       -n openhands-$BRANCH_SANITIZED"
    echo ""
    echo "  3. Access the deployment:"
    echo "     URL: https://$BRANCH_SANITIZED.$DOMAIN"
    echo "     SSO: $SHARED_KEYCLOAK_URL (shared authentication)"
    echo ""
fi
