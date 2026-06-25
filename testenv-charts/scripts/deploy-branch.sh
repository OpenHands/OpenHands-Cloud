#!/bin/bash
# =============================================================================
# OpenHands Branch Deployment Script
# =============================================================================
# Deploys OpenHands to the platform-team-sandbox test environment with full
# configuration for shared Keycloak authentication.
#
# This script handles:
#   1. Namespace creation and secret setup
#   2. Helm deployment with proper values
#   3. Keycloak redirect URI registration (post-install hook)
#   4. Deployment verification
#
# Usage:
#   ./deploy-branch.sh <branch-name> [options]
#
# Options:
#   --image-tag <tag>     Override image tag (default: sha-115237f)
#   --namespace <ns>      Override namespace (default: openhands-<branch>)
#   --dry-run             Show what would be deployed without applying
#   --skip-hooks          Skip Keycloak redirect URI hook
#   --upgrade             Upgrade existing release instead of install
#   --values <file>       Additional values file to merge
#
# Examples:
#   ./deploy-branch.sh my-feature
#   ./deploy-branch.sh pr-14343 --image-tag pr-14343
#   ./deploy-branch.sh console-message --image-tag pr-14343 --namespace openhands-console-message
#
# Prerequisites:
#   - kubectl configured for ohe-staging cluster
#   - Helm 3 installed
#   - ghcr-login-secret exists in target namespace (or will be copied from openhands)
#   - keycloak-admin secret exists for redirect URI registration
# =============================================================================

set -o pipefail

# Configuration - platform-team-sandbox environment
DOMAIN="ohe-staging.platform-team.all-hands.dev"
SHARED_KEYCLOAK_URL="https://auth.ohe-staging.platform-team.all-hands.dev"
REALM_NAME="allhands"
CLIENT_ID="allhands"

# Script directory for relative paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CHART_PATH="$REPO_ROOT/charts/openhands"
VALUES_FILE="$REPO_ROOT/testenv-charts/helm/environments/platform-team-sandbox/values-openhands.yaml"

# Defaults
IMAGE_TAG=""
NAMESPACE=""
DRY_RUN=false
SKIP_HOOKS=false
UPGRADE=false
EXTRA_VALUES_FILE=""
BRANCH_NAME=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

usage() {
    echo "Usage: $0 <branch-name> [options]"
    echo ""
    echo "Options:"
    echo "  --image-tag <tag>     Override image tag"
    echo "  --namespace <ns>      Override namespace (default: openhands-<branch>)"
    echo "  --dry-run             Show what would be deployed"
    echo "  --skip-hooks          Skip Keycloak redirect URI registration"
    echo "  --upgrade             Upgrade existing release"
    echo "  --values <file>       Additional values file"
    echo ""
    exit 1
}

log_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1"
}

log_header() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════════${NC}"
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
        --namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --skip-hooks)
            SKIP_HOOKS=true
            shift
            ;;
        --upgrade)
            UPGRADE=true
            shift
            ;;
        --values)
            EXTRA_VALUES_FILE="$2"
            shift 2
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            ;;
    esac
done

# Sanitize branch name for k8s (lowercase, replace invalid chars)
BRANCH_SANITIZED=$(echo "$BRANCH_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | sed 's/--*/-/g' | sed 's/^-//' | sed 's/-$//')

# Default namespace if not specified
if [[ -z "$NAMESPACE" ]]; then
    NAMESPACE="openhands-$BRANCH_SANITIZED"
fi

# Derive release name from namespace or branch
RELEASE_NAME="$BRANCH_SANITIZED"

log_header "OpenHands Branch Deployment"
echo ""
log_info "Branch:       $BRANCH_NAME"
log_info "Sanitized:    $BRANCH_SANITIZED"
log_info "Namespace:    $NAMESPACE"
log_info "Release:      $RELEASE_NAME"
log_info "Domain:       $BRANCH_SANITIZED.$DOMAIN"
if [[ -n "$IMAGE_TAG" ]]; then
    log_info "Image tag:    $IMAGE_TAG"
fi
echo ""

# =============================================================================
# Pre-flight Checks
# =============================================================================
log_header "Pre-flight Checks"

# Check kubectl
if ! command -v kubectl &> /dev/null; then
    log_error "kubectl not found"
    exit 1
fi
log_success "kubectl installed"

# Check helm
if ! command -v helm &> /dev/null; then
    log_error "helm not found"
    exit 1
fi
log_success "helm installed"

# Check cluster connectivity
if ! kubectl cluster-info &> /dev/null; then
    log_error "Cannot connect to Kubernetes cluster"
    exit 1
fi
log_success "Cluster accessible"

# Check chart exists
if [[ ! -d "$CHART_PATH" ]]; then
    log_error "Chart not found at $CHART_PATH"
    exit 1
fi
log_success "Chart found: $CHART_PATH"

# Check values file exists
if [[ ! -f "$VALUES_FILE" ]]; then
    log_error "Values file not found: $VALUES_FILE"
    exit 1
fi
log_success "Values file: $VALUES_FILE"

# =============================================================================
# Namespace Setup
# =============================================================================
log_header "Namespace Setup"

if kubectl get namespace "$NAMESPACE" &> /dev/null; then
    log_info "Namespace '$NAMESPACE' already exists"
else
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would create namespace: $NAMESPACE"
    else
        kubectl create namespace "$NAMESPACE"
        log_success "Created namespace: $NAMESPACE"
    fi
fi

# =============================================================================
# Copy Required Secrets
# =============================================================================
log_header "Secret Setup"

copy_secret_if_missing() {
    local secret_name=$1
    local source_ns=$2
    
    if kubectl get secret "$secret_name" -n "$NAMESPACE" &> /dev/null; then
        log_info "Secret '$secret_name' already exists in $NAMESPACE"
        return 0
    fi
    
    if ! kubectl get secret "$secret_name" -n "$source_ns" &> /dev/null; then
        log_warn "Source secret '$secret_name' not found in $source_ns"
        return 1
    fi
    
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would copy secret '$secret_name' from $source_ns to $NAMESPACE"
    else
        kubectl get secret "$secret_name" -n "$source_ns" -o yaml | \
            sed "s/namespace: $source_ns/namespace: $NAMESPACE/" | \
            kubectl apply -f -
        log_success "Copied secret '$secret_name' to $NAMESPACE"
    fi
}

# Copy secrets from openhands namespace (or traefik for TLS)
copy_secret_if_missing "ghcr-login-secret" "openhands"
copy_secret_if_missing "keycloak-admin" "openhands"
copy_secret_if_missing "keycloak-realm" "openhands"
copy_secret_if_missing "ohe-staging-wildcard-tls" "traefik"

# Create postgres-password secret if missing
if ! kubectl get secret postgres-password -n "$NAMESPACE" &> /dev/null; then
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would create postgres-password secret"
    else
        kubectl create secret generic postgres-password \
            -n "$NAMESPACE" \
            --from-literal=password="$(openssl rand -base64 24)"
        log_success "Created postgres-password secret"
    fi
else
    log_info "Secret 'postgres-password' already exists"
fi

# Create redis secret if missing  
if ! kubectl get secret redis -n "$NAMESPACE" &> /dev/null; then
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would create redis secret"
    else
        kubectl create secret generic redis \
            -n "$NAMESPACE" \
            --from-literal=redis-password="$(openssl rand -base64 24)"
        log_success "Created redis secret"
    fi
else
    log_info "Secret 'redis' already exists"
fi

# =============================================================================
# Build Helm Command
# =============================================================================
log_header "Helm Deployment"

HELM_CMD="helm"
if [[ "$UPGRADE" == "true" ]]; then
    HELM_CMD="$HELM_CMD upgrade --install"
else
    # Check if release exists
    if helm status "$RELEASE_NAME" -n "$NAMESPACE" &> /dev/null; then
        log_warn "Release '$RELEASE_NAME' already exists, using upgrade"
        HELM_CMD="$HELM_CMD upgrade"
    else
        HELM_CMD="$HELM_CMD install"
    fi
fi

HELM_CMD="$HELM_CMD $RELEASE_NAME $CHART_PATH"
HELM_CMD="$HELM_CMD --namespace $NAMESPACE"
HELM_CMD="$HELM_CMD -f $VALUES_FILE"

# Add extra values file if specified
if [[ -n "$EXTRA_VALUES_FILE" ]]; then
    HELM_CMD="$HELM_CMD -f $EXTRA_VALUES_FILE"
fi

# Core overrides for branch deployment
HELM_CMD="$HELM_CMD --set fullnameOverride=$RELEASE_NAME"
HELM_CMD="$HELM_CMD --set branchSanitized=$BRANCH_SANITIZED"
HELM_CMD="$HELM_CMD --set ingress.host=$BRANCH_SANITIZED.$DOMAIN"
HELM_CMD="$HELM_CMD --set ingress.prefixWithBranch=false"

# Image tag override
if [[ -n "$IMAGE_TAG" ]]; then
    HELM_CMD="$HELM_CMD --set image.tag=$IMAGE_TAG"
fi

# Keycloak redirect URI registration hook
# The values file has redirectUriRegistration.enabled=true by default for this environment
# Only need to explicitly disable if --skip-hooks is set
if [[ "$SKIP_HOOKS" == "true" ]]; then
    HELM_CMD="$HELM_CMD --set keycloak.redirectUriRegistration.enabled=false"
fi

# Runtime API overrides for branch deployment
HELM_CMD="$HELM_CMD --set runtime-api.env.K8S_NAMESPACE=$NAMESPACE"
# Skip ClusterRole creation - branch deployments share the ClusterRole from main deployment
HELM_CMD="$HELM_CMD --set runtime-api.serviceAccount.skipClusterRBAC=true"
HELM_CMD="$HELM_CMD --set runtime-api.serviceAccount.existingClusterRole=openhands-runtime-api-clusterrole"

# Dry run flag
if [[ "$DRY_RUN" == "true" ]]; then
    HELM_CMD="$HELM_CMD --dry-run --debug"
fi

echo ""
log_info "Executing helm command:"
echo ""
echo "$HELM_CMD" | sed 's/ --set /\n  --set /g'
echo ""

# Execute
if [[ "$DRY_RUN" == "true" ]]; then
    log_info "[DRY-RUN] Would execute the above command"
    eval "$HELM_CMD" 2>&1 | head -100
else
    if eval "$HELM_CMD"; then
        log_success "Helm deployment completed"
    else
        log_error "Helm deployment failed"
        exit 1
    fi
fi

# =============================================================================
# Post-Deployment Verification
# =============================================================================
if [[ "$DRY_RUN" == "false" ]]; then
    log_header "Post-Deployment Verification"
    
    log_info "Waiting for deployment to become ready..."
    
    # Wait for main deployment
    if kubectl rollout status deployment/$RELEASE_NAME -n "$NAMESPACE" --timeout=300s 2>/dev/null; then
        log_success "Main deployment ready"
    else
        log_warn "Main deployment not ready yet"
    fi
    
    # Check for redirect URI hook job
    if [[ "$SKIP_HOOKS" == "false" ]]; then
        log_info "Checking Keycloak redirect URI registration..."
        sleep 5  # Give job time to start
        
        JOB_NAME="$RELEASE_NAME-keycloak-redirect-uri"
        if kubectl get job "$JOB_NAME" -n "$NAMESPACE" &> /dev/null; then
            # Wait for job to complete
            if kubectl wait --for=condition=complete job/"$JOB_NAME" -n "$NAMESPACE" --timeout=120s 2>/dev/null; then
                log_success "Keycloak redirect URI registered"
            else
                log_warn "Redirect URI hook may have failed. Check logs:"
                log_info "  kubectl logs job/$JOB_NAME -n $NAMESPACE"
            fi
        else
            log_warn "Redirect URI hook job not found"
        fi
    fi
    
    echo ""
    log_header "Deployment Summary"
    echo ""
    log_info "Namespace:    $NAMESPACE"
    log_info "Release:      $RELEASE_NAME"
    log_info "URL:          https://$BRANCH_SANITIZED.$DOMAIN"
    log_info "Auth URL:     $SHARED_KEYCLOAK_URL"
    echo ""
    log_info "Useful commands:"
    echo "  kubectl get pods -n $NAMESPACE"
    echo "  kubectl logs -n $NAMESPACE -l app=$RELEASE_NAME -f"
    echo "  helm status $RELEASE_NAME -n $NAMESPACE"
    echo ""
fi
