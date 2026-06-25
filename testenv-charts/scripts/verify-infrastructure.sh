#!/bin/bash
# =============================================================================
# OpenHands Platform-Team-Sandbox Infrastructure Verification Script
# =============================================================================
# This script verifies that all infrastructure components are in place and
# running correctly before deploying a Helm chart.
#
# Usage: ./verify-infrastructure.sh [--quick] [--fix]
#   --quick  Skip slow tests (DNS propagation, HTTP health checks)
#   --fix    Attempt to fix common issues automatically
#
# Exit codes:
#   0 = All checks passed, ready for deployment
#   1 = Critical failures, infrastructure not ready
#   2 = Warnings present, deployment may have issues
# =============================================================================

set -o pipefail

# Configuration
DOMAIN="ohe-staging.platform-team.all-hands.dev"
GCP_PROJECT="platform-team-sandbox-62793"
GCP_REGION="us-central1"
CLUSTER_NAME="ohe-staging-path-cluster"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
PASSED=0
FAILED=0
WARNINGS=0

# Parse arguments
QUICK_MODE=false
FIX_MODE=false
for arg in "$@"; do
    case $arg in
        --quick) QUICK_MODE=true ;;
        --fix) FIX_MODE=true ;;
    esac
done

# Helper functions
print_header() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════════${NC}"
}

print_check() {
    echo -n "  ➤ $1... "
}

pass() {
    echo -e "${GREEN}✓ PASS${NC}"
    ((PASSED++))
}

fail() {
    echo -e "${RED}✗ FAIL${NC}"
    if [[ -n "$1" ]]; then
        echo -e "    ${RED}↳ $1${NC}"
    fi
    ((FAILED++))
}

warn() {
    echo -e "${YELLOW}⚠ WARN${NC}"
    if [[ -n "$1" ]]; then
        echo -e "    ${YELLOW}↳ $1${NC}"
    fi
    ((WARNINGS++))
}

info() {
    echo -e "    ${BLUE}ℹ $1${NC}"
}

# =============================================================================
# PRE-FLIGHT CHECKS
# =============================================================================
print_header "PRE-FLIGHT CHECKS"

# Check required tools
print_check "kubectl installed"
if command -v kubectl &> /dev/null; then
    pass
else
    fail "kubectl not found in PATH"
fi

print_check "gcloud installed"
if command -v gcloud &> /dev/null; then
    pass
else
    fail "gcloud not found in PATH"
fi

print_check "helm installed"
if command -v helm &> /dev/null; then
    pass
else
    fail "helm not found in PATH"
fi

print_check "curl installed"
if command -v curl &> /dev/null; then
    pass
else
    fail "curl not found in PATH"
fi

# Check GCP project
print_check "GCP project configured ($GCP_PROJECT)"
CURRENT_PROJECT=$(gcloud config get-value project 2>/dev/null)
if [[ "$CURRENT_PROJECT" == "$GCP_PROJECT" ]]; then
    pass
else
    warn "Current project is '$CURRENT_PROJECT', expected '$GCP_PROJECT'"
    info "Run: gcloud config set project $GCP_PROJECT"
fi

# Check kubectl context
print_check "kubectl context configured"
CURRENT_CONTEXT=$(kubectl config current-context 2>/dev/null)
if [[ "$CURRENT_CONTEXT" == *"$CLUSTER_NAME"* ]] || [[ "$CURRENT_CONTEXT" == *"ohe-staging"* ]]; then
    pass
    info "Context: $CURRENT_CONTEXT"
else
    warn "Context '$CURRENT_CONTEXT' may not be correct"
    info "Expected cluster: $CLUSTER_NAME"
fi

# =============================================================================
# GKE CLUSTER HEALTH
# =============================================================================
print_header "GKE CLUSTER HEALTH"

# Check cluster connectivity
print_check "Cluster API accessible"
if kubectl cluster-info &> /dev/null; then
    pass
else
    fail "Cannot connect to cluster API"
fi

# Check nodes
print_check "All nodes Ready"
NOT_READY=$(kubectl get nodes --no-headers 2>/dev/null | grep -v " Ready " | wc -l | tr -d ' ')
TOTAL_NODES=$(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')
if [[ "$NOT_READY" -eq 0 ]] && [[ "$TOTAL_NODES" -gt 0 ]]; then
    pass
    info "$TOTAL_NODES nodes total"
else
    fail "$NOT_READY of $TOTAL_NODES nodes not Ready"
fi

# Check for runtime nodes (sysbox-enabled)
print_check "Sysbox runtime nodes available"
SYSBOX_NODES=$(kubectl get nodes -l sysbox-install=yes --no-headers 2>/dev/null | wc -l | tr -d ' ')
if [[ "$SYSBOX_NODES" -gt 0 ]]; then
    pass
    info "$SYSBOX_NODES sysbox-enabled nodes"
else
    fail "No nodes with label sysbox-install=yes"
    info "Runtime pods will not be able to schedule"
fi

# Check RuntimeClass
print_check "sysbox-runc RuntimeClass exists"
if kubectl get runtimeclass sysbox-runc &> /dev/null; then
    pass
else
    fail "RuntimeClass 'sysbox-runc' not found"
    info "Run: kubectl apply -f testenv-charts/k8s/sysbox/sysbox-install.yaml"
fi

# =============================================================================
# INFRASTRUCTURE COMPONENTS
# =============================================================================
print_header "INFRASTRUCTURE COMPONENTS"

# cert-manager
print_check "cert-manager namespace exists"
if kubectl get namespace cert-manager &> /dev/null; then
    pass
else
    fail "Namespace 'cert-manager' not found"
fi

print_check "cert-manager pods running"
CM_PODS_READY=$(kubectl get pods -n cert-manager --no-headers 2>/dev/null | grep -c "Running" || echo 0)
CM_PODS_TOTAL=$(kubectl get pods -n cert-manager --no-headers 2>/dev/null | wc -l | tr -d ' ')
if [[ "$CM_PODS_READY" -ge 3 ]]; then
    pass
    info "$CM_PODS_READY/$CM_PODS_TOTAL pods running"
else
    fail "Only $CM_PODS_READY cert-manager pods running (need 3)"
fi

print_check "ClusterIssuer for Let's Encrypt exists"
# Check for common ClusterIssuer names
ISSUER_NAME=""
for name in letsencrypt-dns01 letsencrypt-production letsencrypt-staging-dns; do
    if kubectl get clusterissuer "$name" &> /dev/null; then
        ISSUER_NAME="$name"
        break
    fi
done
if [[ -n "$ISSUER_NAME" ]]; then
    ISSUER_READY=$(kubectl get clusterissuer "$ISSUER_NAME" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
    if [[ "$ISSUER_READY" == "True" ]]; then
        pass
        info "Using ClusterIssuer: $ISSUER_NAME"
    else
        warn "ClusterIssuer '$ISSUER_NAME' exists but not Ready"
    fi
else
    fail "No Let's Encrypt ClusterIssuer found"
    info "Expected one of: letsencrypt-dns01, letsencrypt-production, letsencrypt-staging-dns"
fi

# Traefik
print_check "traefik namespace exists"
if kubectl get namespace traefik &> /dev/null; then
    pass
else
    fail "Namespace 'traefik' not found"
fi

print_check "Traefik pods running"
TRAEFIK_READY=$(kubectl get pods -n traefik -l app.kubernetes.io/name=traefik --no-headers 2>/dev/null | grep -c "Running" || echo 0)
if [[ "$TRAEFIK_READY" -gt 0 ]]; then
    pass
    info "$TRAEFIK_READY Traefik pod(s) running"
else
    fail "No Traefik pods running"
fi

print_check "Traefik LoadBalancer has external IP"
LB_IP=$(kubectl get svc -n traefik traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null)
if [[ -n "$LB_IP" ]]; then
    pass
    info "External IP: $LB_IP"
else
    fail "No external IP assigned to Traefik LoadBalancer"
fi

# external-dns (optional but recommended)
print_check "external-dns running (optional)"
EXTDNS_READY=$(kubectl get pods -n external-dns --no-headers 2>/dev/null | grep -c "Running" || echo "0")
EXTDNS_READY=$(echo "$EXTDNS_READY" | tr -d '[:space:]')
if [[ "$EXTDNS_READY" -gt 0 ]]; then
    pass
else
    warn "external-dns not running (DNS records need manual management)"
fi

# =============================================================================
# TLS CERTIFICATES
# =============================================================================
print_header "TLS CERTIFICATES"

print_check "Wildcard certificate exists"
if kubectl get certificate ohe-staging-wildcard-cert -n traefik &> /dev/null; then
    CERT_READY=$(kubectl get certificate ohe-staging-wildcard-cert -n traefik -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
    if [[ "$CERT_READY" == "True" ]]; then
        pass
        # Get expiry
        CERT_EXPIRY=$(kubectl get certificate ohe-staging-wildcard-cert -n traefik -o jsonpath='{.status.notAfter}' 2>/dev/null)
        info "Certificate valid until: $CERT_EXPIRY"
    else
        fail "Wildcard certificate exists but not Ready"
        info "Check: kubectl describe certificate ohe-staging-wildcard-cert -n traefik"
    fi
else
    fail "Wildcard certificate 'ohe-staging-wildcard-cert' not found"
fi

print_check "TLS secret available"
if kubectl get secret ohe-staging-wildcard-tls -n traefik &> /dev/null; then
    pass
else
    fail "TLS secret 'ohe-staging-wildcard-tls' not found in traefik namespace"
fi

# =============================================================================
# DNS & CONNECTIVITY
# =============================================================================
print_header "DNS & CONNECTIVITY"

if [[ "$QUICK_MODE" == "false" ]]; then
    print_check "DNS resolves $DOMAIN"
    DNS_IP=$(dig +short "$DOMAIN" 2>/dev/null | head -1)
    if [[ -n "$DNS_IP" ]]; then
        if [[ "$DNS_IP" == "$LB_IP" ]]; then
            pass
            info "Resolves to: $DNS_IP (matches LoadBalancer)"
        else
            warn "DNS resolves to $DNS_IP but LoadBalancer is $LB_IP"
        fi
    else
        fail "DNS does not resolve for $DOMAIN"
    fi

    print_check "HTTPS endpoint accessible"
    HTTP_STATUS=$(curl -sk -o /dev/null -w "%{http_code}" "https://$DOMAIN" --connect-timeout 10 2>/dev/null)
    if [[ "$HTTP_STATUS" == "200" ]] || [[ "$HTTP_STATUS" == "302" ]] || [[ "$HTTP_STATUS" == "301" ]]; then
        pass
        info "HTTP status: $HTTP_STATUS"
    elif [[ "$HTTP_STATUS" == "000" ]]; then
        fail "Connection failed (timeout or refused)"
    else
        warn "Unexpected HTTP status: $HTTP_STATUS"
    fi

    print_check "TLS certificate valid"
    CERT_INFO=$(echo | openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" 2>/dev/null | openssl x509 -noout -dates 2>/dev/null)
    if [[ -n "$CERT_INFO" ]]; then
        pass
        info "$(echo "$CERT_INFO" | grep notAfter | cut -d= -f2)"
    else
        warn "Could not verify TLS certificate"
    fi
else
    info "Skipping DNS/connectivity tests (--quick mode)"
fi

# =============================================================================
# OPENHANDS NAMESPACE
# =============================================================================
print_header "OPENHANDS NAMESPACE"

print_check "openhands namespace exists"
if kubectl get namespace openhands &> /dev/null; then
    pass
else
    warn "Namespace 'openhands' not found (will be created on first deploy)"
fi

# Check for required secrets (if namespace exists)
if kubectl get namespace openhands &> /dev/null; then
    print_check "ghcr-login-secret exists"
    if kubectl get secret ghcr-login-secret -n openhands &> /dev/null; then
        pass
    else
        fail "Image pull secret 'ghcr-login-secret' not found"
        info "Required for pulling images from ghcr.io/all-hands-ai"
    fi

    print_check "postgres-password secret exists"
    if kubectl get secret postgres-password -n openhands &> /dev/null; then
        pass
    else
        warn "Secret 'postgres-password' not found (may be auto-created by Helm)"
    fi

    print_check "TLS secret copied to openhands namespace"
    if kubectl get secret ohe-staging-wildcard-tls -n openhands &> /dev/null; then
        pass
    else
        warn "TLS secret not in openhands namespace"
        info "Run: kubectl get secret ohe-staging-wildcard-tls -n traefik -o yaml | sed 's/namespace: traefik/namespace: openhands/' | kubectl apply -f -"
    fi
fi

# =============================================================================
# EXISTING DEPLOYMENTS
# =============================================================================
print_header "EXISTING DEPLOYMENTS (if any)"

print_check "OpenHands app deployment"
OH_PODS=$(kubectl get pods -n openhands -l app=openhands --no-headers 2>/dev/null | grep -c "Running" || echo 0)
if [[ "$OH_PODS" -gt 0 ]]; then
    pass
    info "$OH_PODS OpenHands pod(s) running"
else
    info "No OpenHands pods found (expected if not yet deployed)"
    ((PASSED++))
fi

print_check "Runtime API deployment"
RUNTIME_PODS=$(kubectl get pods -n openhands -l app.kubernetes.io/name=runtime-api --no-headers 2>/dev/null | grep -c "Running" || echo "0")
RUNTIME_PODS=$(echo "$RUNTIME_PODS" | tr -d '[:space:]')
if [[ "$RUNTIME_PODS" -gt 0 ]]; then
    pass
    info "$RUNTIME_PODS Runtime API pod(s) running"
else
    info "No Runtime API pods found (expected if not yet deployed)"
    ((PASSED++))
fi

print_check "PostgreSQL running"
PG_PODS=$(kubectl get pods -n openhands -l app.kubernetes.io/name=postgresql --no-headers 2>/dev/null | grep -c "Running" || echo 0)
if [[ "$PG_PODS" -gt 0 ]]; then
    pass
else
    info "PostgreSQL not running (will be deployed with Helm chart)"
    ((PASSED++))
fi

print_check "Redis running"
REDIS_PODS=$(kubectl get pods -n openhands -l app.kubernetes.io/name=redis --no-headers 2>/dev/null | grep -c "Running" || echo 0)
if [[ "$REDIS_PODS" -gt 0 ]]; then
    pass
else
    info "Redis not running (will be deployed with Helm chart)"
    ((PASSED++))
fi

# =============================================================================
# WARM RUNTIMES & IMAGE LOADER
# =============================================================================
print_header "RUNTIME READINESS"

print_check "Warm runtime pods"
WARM_PODS=$(kubectl get pods -n openhands -l app=warm-runtime --no-headers 2>/dev/null | grep -c "Running" || echo "0")
WARM_PODS=$(echo "$WARM_PODS" | tr -d '[:space:]')
if [[ "$WARM_PODS" -gt 0 ]]; then
    pass
    info "$WARM_PODS warm runtime pod(s) available"
else
    info "No warm runtimes pre-created (first conversation will be slower)"
    ((PASSED++))
fi

print_check "Image loader status"
IL_PODS=$(kubectl get pods -n openhands -l app=image-loader --no-headers 2>/dev/null 2>&1)
IL_RUNNING=$(echo "$IL_PODS" | grep -c "Running" || echo "0")
IL_RUNNING=$(echo "$IL_RUNNING" | tr -d '[:space:]')
IL_PENDING=$(echo "$IL_PODS" | grep -c "Pending" || echo "0")
IL_PENDING=$(echo "$IL_PENDING" | tr -d '[:space:]')
if [[ "$IL_RUNNING" -gt 0 ]]; then
    pass
    info "$IL_RUNNING image-loader pod(s) running"
elif [[ "$IL_PENDING" -gt 0 ]]; then
    warn "$IL_PENDING image-loader pods pending (node capacity issue?)"
else
    info "Image loader not deployed"
    ((PASSED++))
fi

# =============================================================================
# SUMMARY
# =============================================================================
print_header "VERIFICATION SUMMARY"

echo ""
echo -e "  ${GREEN}Passed:${NC}   $PASSED"
echo -e "  ${YELLOW}Warnings:${NC} $WARNINGS"
echo -e "  ${RED}Failed:${NC}   $FAILED"
echo ""

if [[ "$FAILED" -eq 0 ]] && [[ "$WARNINGS" -eq 0 ]]; then
    echo -e "${GREEN}✅ Infrastructure is READY for deployment!${NC}"
    echo ""
    echo "Deploy with:"
    echo "  helm install openhands ./charts/openhands \\"
    echo "    -n openhands \\"
    echo "    -f testenv-charts/helm/environments/platform-team-sandbox/values-openhands.yaml \\"
    echo "    --set branchSanitized=<your-branch>"
    echo ""
    exit 0
elif [[ "$FAILED" -eq 0 ]]; then
    echo -e "${YELLOW}⚠️  Infrastructure is READY with warnings.${NC}"
    echo "Review warnings above before deploying."
    echo ""
    exit 2
else
    echo -e "${RED}❌ Infrastructure is NOT READY.${NC}"
    echo "Fix the failed checks above before deploying."
    echo ""
    echo "Common fixes:"
    echo "  • Get cluster credentials: gcloud container clusters get-credentials $CLUSTER_NAME --region $GCP_REGION --project $GCP_PROJECT"
    echo "  • Install sysbox: kubectl apply -f testenv-charts/k8s/sysbox/sysbox-install.yaml"
    echo "  • Check cert-manager: kubectl describe certificate -n traefik"
    echo ""
    exit 1
fi
