# PRD: Enterprise Staging Environments

**Author:** Saurya Velagapudi  
**Date:** 2026-04-14  
**Updated:** 2026-05-10  
**Status:** Implemented  
**Stakeholders:** Engineering, DevOps, QA

---

## Executive Summary

We need staging environments that accurately replicate what enterprise customers experience when running OpenHands in production. This PRD defines the staging infrastructure that enables both automated CI testing and individual developer validation of customer-facing features.

---

## Current Progress (as of 2026-05-10)

### ✅ Completed: Full Staging Infrastructure

We have implemented a complete staging environment on the **Platform Team Sandbox** infrastructure (PR #580), with two continuously-deployed routing environments.

**Infrastructure (from PR #580 - `SV-OHE-staging-Deploy-Infra`):**

| Component | Status | Details |
|-----------|--------|---------|
| GKE Cluster | ✅ Running | `ohe-staging-cluster` in `platform-team-sandbox` |
| Traefik Ingress | ✅ Running | LoadBalancer with wildcard TLS |
| cert-manager | ✅ Running | ClusterIssuer with Let's Encrypt DNS-01 |
| external-dns | ✅ Running | Automatic DNS record management |
| Cloud DNS | ✅ Configured | `ohe-staging.platform-team.all-hands.dev` |
| Shared Keycloak | ✅ Running | `auth.ohe-staging.platform-team.all-hands.dev` |

**Continuous Deployment Environments:**

| Environment | URL | Routing | Namespace |
|-------------|-----|---------|-----------|
| **pathroute** | `pathroute.ohe-staging.platform-team.all-hands.dev` | Path-based | `openhands-pathroute` |
| **subdomain** | `subdomain.ohe-staging.platform-team.all-hands.dev` | Subdomain-based | `openhands-subdomain` |

**Architecture:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      Platform Team Sandbox GCP Project                       │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                    GKE: ohe-staging-cluster                             │ │
│  │                                                                          │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │ │
│  │  │   traefik    │  │ cert-manager │  │ external-dns │                  │ │
│  │  │  namespace   │  │  namespace   │  │  namespace   │                  │ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘                  │ │
│  │                                                                          │ │
│  │  ┌──────────────┐  ┌─────────────────────────────────────────────────┐ │ │
│  │  │ shared-auth  │  │        openhands-pathroute namespace            │ │ │
│  │  │  (Keycloak)  │  │  pathroute.ohe-staging.platform-team.all-hands.dev│ │ │
│  │  │              │  └─────────────────────────────────────────────────┘ │ │
│  │  │   auth.ohe-  │                                                       │ │
│  │  │   staging... │  ┌─────────────────────────────────────────────────┐ │ │
│  │  │              │  │        openhands-subdomain namespace            │ │ │
│  │  └──────────────┘  │ subdomain.ohe-staging.platform-team.all-hands.dev │ │ │
│  │                     └─────────────────────────────────────────────────┘ │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  Cloud DNS Zone: ohe-staging.platform-team.all-hands.dev               │ │
│  │  └── *.ohe-staging.platform-team.all-hands.dev → Traefik LB IP         │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key Design Decisions:**

1. **Shared infrastructure** - Both environments run on the same cluster as developer branch deployments
2. **Namespace isolation** - Each environment in its own namespace
3. **Shared Keycloak** - Single authentication provider for all deployments
4. **Branch-like deployment** - Uses the same `branchSanitized` mechanism as developer deployments
5. **Secrets from all-hands-system** - Centralized secret management, copied to namespaces at deploy time

**Deployment:**

Via GitHub Actions workflow:
1. Go to **Actions** → **Deploy to Staging**
2. Click **Run workflow**
3. Select environment: `both`, `pathroute`, or `subdomain`
4. Enter the image tag to deploy

**Related Documentation:**

- [Branch Deployments Guide](../testenv-charts/BRANCH_DEPLOYMENTS.md)
- [Full Deployment Guide](../testenv-charts/FULL_DEPLOYMENT_GUIDE.md)
- [Staging Base Values](../testenv-charts/helm/environments/staging/base-values.yaml)

---

## Problem Statement

### Current State

Today, we lack staging environments that accurately reflect enterprise deployments:

1. **No CI environment for integration testing** - We cannot run end-to-end tests against a real Kubernetes deployment with enterprise features (SAML, HA, TLS).

2. **No developer sandbox for customer issue reproduction** - When debugging customer issues, engineers have no environment that mirrors customer infrastructure.

3. **No validation of routing patterns** - Customers deploy with either path-based routing (`app.example.com/api/automation`) or subdomain-based routing (`automation.app.example.com`). We have no way to test both patterns.

### Why Replicated Is Not the Solution

Replicated is valuable for customer POCs and single-VM deployments, but it does not solve this problem:

| Requirement | Replicated | Our Staging Infra |
|-------------|------------|-------------------|
| High-availability deployment | ❌ Single VM | ✅ Multi-node K8s cluster |
| Scale-up customer simulation | ❌ Not supported yet | ✅ Mirrors production topology |
| Rapid iteration on infra changes | ❌ Full redeploy cycle | ✅ Incremental Helm updates |
| CI/CD integration | ❌ Manual process | ✅ GitHub Actions automation |
| Multiple concurrent environments | ❌ One at a time | ✅ Namespace isolation |

Replicated remains our solution for customer self-hosted POCs. These staging environments are for **internal engineering validation** of features that enterprise customers depend on.

---

## Goals

### Primary Goals

1. **Validate enterprise features before release** - SAML SSO, TLS, high-availability configurations
2. **Enable CI integration testing** - Automated tests against real infrastructure
3. **Support customer issue debugging** - Quickly spin up environments that mirror customer setups
4. **Test both routing patterns** - Path-based and subdomain-based routing

### Non-Goals

- Replacing Replicated for customer deployments
- Full production parity (we accept some cost optimizations)
- Multi-region testing (future scope)

---

## Proposed Solution

### Four Staging Environments

| Environment | Purpose | Routing | Deployment Trigger |
|-------------|---------|---------|-------------------|
| `staging-ci-pathroute` | Automated CI testing | Path-based | On PR merge to main |
| `staging-ci-subdomain` | Automated CI testing | Subdomain-based | On PR merge to main |
| `staging-dev-pathroute` | Developer sandbox | Path-based | Manual / feature branch |
| `staging-dev-subdomain` | Developer sandbox | Subdomain-based | Manual / feature branch |

### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     GCP Project: staging-092324                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────┐  ┌─────────────────┐                           │
│  │ staging-ci-     │  │ staging-ci-     │   CI Environments         │
│  │ pathroute       │  │ subdomain       │   (auto-deployed)         │
│  │ namespace       │  │ namespace       │                           │
│  └────────┬────────┘  └────────┬────────┘                           │
│           │                    │                                     │
│  ┌────────┴────────┐  ┌────────┴────────┐                           │
│  │ staging-dev-    │  │ staging-dev-    │   Dev Environments        │
│  │ pathroute       │  │ subdomain       │   (manual deploy)         │
│  │ namespace       │  │ namespace       │                           │
│  └─────────────────┘  └─────────────────┘                           │
│                                                                      │
│  ┌──────────────────────────────────────┐                           │
│  │         Shared Infrastructure         │                           │
│  │  • cert-manager (ClusterIssuer)      │                           │
│  │  • external-dns                       │                           │
│  │  • traefik ingress controller        │                           │
│  │  • Keycloak (SAML IdP)               │                           │
│  └──────────────────────────────────────┘                           │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### DNS Structure

**Path-based routing environments:**
```
staging-ci-pathroute.all-hands.dev
  └── /                        → openhands-service
  └── /api/automation          → automation-service
  └── /integration/*           → integration-events-service
  └── /mcp/mcp                 → mcp-service

staging-dev-pathroute.all-hands.dev
  └── (same structure)
```

**Subdomain-based routing environments:**
```
staging-ci-subdomain.all-hands.dev          → openhands-service
automation.staging-ci-subdomain.all-hands.dev → automation-service
integrations.staging-ci-subdomain.all-hands.dev → integration-events-service
mcp.staging-ci-subdomain.all-hands.dev      → mcp-service

staging-dev-subdomain.all-hands.dev
  └── (same structure with subdomains)
```

---

## Technical Requirements

### 1. TLS Certificates and cert-manager

**Requirement:** All four environments must have valid TLS certificates.

**Implementation:**
```yaml
# Cluster-scoped ClusterIssuer (shared across namespaces)
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-staging
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: devops@all-hands.dev
    privateKeySecretRef:
      name: letsencrypt-staging-account-key
    solvers:
      - http01:
          ingress:
            class: traefik
```

**Wildcard certificates for subdomain routing:**
```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: staging-ci-subdomain-wildcard
  namespace: staging-ci-subdomain
spec:
  secretName: staging-ci-subdomain-tls
  issuerRef:
    name: letsencrypt-staging
    kind: ClusterIssuer
  dnsNames:
    - "staging-ci-subdomain.all-hands.dev"
    - "*.staging-ci-subdomain.all-hands.dev"
```

**Tasks:**
- [ ] Install cert-manager in staging cluster
- [ ] Create ClusterIssuer for Let's Encrypt
- [ ] Configure DNS-01 solver for wildcard certs (subdomain envs)
- [ ] Configure HTTP-01 solver for standard certs (pathroute envs)

**Estimated Effort:** 1 day

---

### 2. Helm Chart Installation Complexity

**Current Assessment:**

The OpenHands Helm chart (`charts/openhands/`) has the following dependencies:

| Dependency | Required | Notes |
|------------|----------|-------|
| PostgreSQL | Yes | External or in-cluster |
| Redis | Yes | External or in-cluster |
| Keycloak | Yes | For authentication |
| LiteLLM Proxy | Yes | For LLM routing |
| cert-manager | Yes | For TLS |
| traefik | Yes | Ingress controller |

**Installation Order:**
1. **Cluster prerequisites** (one-time):
   - cert-manager
   - traefik ingress controller
   - external-dns
   - Keycloak (shared SAML IdP)

2. **Per-environment** (each namespace):
   - PostgreSQL (or connection to shared instance)
   - Redis (or connection to shared instance)
   - OpenHands chart
   - LiteLLM Proxy

**Complexity Assessment:**

| Task | Complexity | Notes |
|------|------------|-------|
| Fresh cluster setup | High | ~2-3 days for prerequisites |
| New environment in existing cluster | Medium | ~2-4 hours |
| Updating existing environment | Low | ~5-10 minutes |

**Tasks:**
- [ ] Document cluster prerequisites installation
- [ ] Create shared infrastructure Helm chart or Terraform
- [ ] Validate Helm chart works with namespace isolation

**Estimated Effort:** 3-5 days (one-time cluster setup)

---

### 3. Incremental Deployment Strategy

**Problem:** How do we deploy only the components that changed instead of redeploying everything?

**Solution:** Helm upgrade with selective value overrides

```bash
# Deploy only if openhands chart changed
helm upgrade --install openhands-staging charts/openhands \
  -n staging-ci-pathroute \
  -f envs/common/values.yaml \
  -f envs/staging-ci-pathroute/values.yaml \
  --set image.tag=$NEW_TAG

# Deploy only runtime-api if that changed
helm upgrade --install runtime-api-staging charts/runtime-api \
  -n staging-ci-pathroute \
  -f envs/staging-ci-pathroute/runtime-api-values.yaml \
  --set image.tag=$NEW_TAG
```

**GitHub Actions Integration:**

```yaml
jobs:
  detect-changes:
    outputs:
      openhands: ${{ steps.changes.outputs.openhands }}
      runtime-api: ${{ steps.changes.outputs.runtime-api }}
      automation: ${{ steps.changes.outputs.automation }}
    steps:
      - uses: dorny/paths-filter@v2
        id: changes
        with:
          filters: |
            openhands:
              - 'charts/openhands/**'
              - 'envs/**/values.yaml'
            runtime-api:
              - 'charts/runtime-api/**'
            automation:
              - 'charts/automation/**'

  deploy-openhands:
    needs: detect-changes
    if: needs.detect-changes.outputs.openhands == 'true'
    # ... deploy only openhands chart
```

**Tasks:**
- [ ] Implement path-based change detection in CI
- [ ] Create per-chart deployment jobs
- [ ] Add rollback on failure

**Estimated Effort:** 2 days

---

### 4. SAML Identity Provider Setup

**Requirement:** Enterprise customers use SAML SSO. We need a SAML IdP in staging to test this flow.

**Options:**

| Option | Pros | Cons |
|--------|------|------|
| **Keycloak** (recommended) | Full-featured, widely used, already in our stack | More complex setup |
| Mock SAML IdP | Simple, fast | Not production-realistic |
| Okta Developer | Real IdP | External dependency, cost |

**Keycloak Implementation:**

```yaml
# Shared Keycloak deployment (one per cluster)
apiVersion: v1
kind: Namespace
metadata:
  name: keycloak
---
# Keycloak Helm deployment
helm install keycloak bitnami/keycloak \
  -n keycloak \
  --set auth.adminUser=admin \
  --set auth.adminPassword=$KEYCLOAK_ADMIN_PASSWORD \
  --set ingress.enabled=true \
  --set ingress.hostname=auth.staging.all-hands.dev
```

**SAML Realm Configuration:**
- Create realm: `openhands-staging`
- Create client for each environment (4 total)
- Configure SAML assertions with required attributes
- Create test users with various roles

**Optional: GitHub/GitLab OAuth:**
```yaml
# In Keycloak, configure identity providers:
# - GitHub OAuth App
# - GitLab OAuth App
# - Google OAuth (if needed)
```

**Tasks:**
- [ ] Deploy Keycloak to staging cluster
- [ ] Create SAML realm and clients
- [ ] Configure test users with various enterprise roles
- [ ] Document SAML configuration for each environment
- [ ] (Optional) Add GitHub/GitLab OAuth providers

**Estimated Effort:** 2-3 days

---

### 5. Integration Test Suite

**Requirement:** Create integration tests that validate enterprise features using the staging environments.

**Test Categories:**

| Category | Tests | Environment |
|----------|-------|-------------|
| **Authentication** | SAML login, logout, session refresh | All |
| **Authorization** | Role-based access, team permissions | All |
| **Routing** | Path-based ingress, subdomain routing | Split by type |
| **Conversations** | Create, resume, attach runtime | All |
| **Integrations** | GitHub webhooks, GitLab webhooks | All |
| **Billing** | Stripe webhook handling | CI only |

**Test Framework:**

```python
# tests/integration/conftest.py
import pytest

@pytest.fixture
def staging_env():
    """Configure test to run against staging environment."""
    return {
        "base_url": os.environ.get("STAGING_URL", "https://staging-ci-pathroute.all-hands.dev"),
        "saml_idp": os.environ.get("SAML_IDP_URL", "https://auth.staging.all-hands.dev"),
        "test_user": os.environ.get("TEST_USER_EMAIL"),
        "test_password": os.environ.get("TEST_USER_PASSWORD"),
    }

@pytest.fixture
async def authenticated_client(staging_env):
    """Get authenticated client via SAML."""
    client = OpenHandsClient(staging_env["base_url"])
    await client.login_saml(
        idp_url=staging_env["saml_idp"],
        username=staging_env["test_user"],
        password=staging_env["test_password"],
    )
    return client
```

**Example Test:**

```python
# tests/integration/test_saml_auth.py
import pytest

@pytest.mark.integration
async def test_saml_login_creates_session(authenticated_client):
    """Verify SAML login creates valid session."""
    user = await authenticated_client.get_current_user()
    assert user is not None
    assert user.email == os.environ["TEST_USER_EMAIL"]

@pytest.mark.integration
async def test_saml_logout_invalidates_session(authenticated_client):
    """Verify SAML logout invalidates session."""
    await authenticated_client.logout()
    with pytest.raises(AuthenticationError):
        await authenticated_client.get_current_user()
```

**CI Integration:**

```yaml
# .github/workflows/integration-tests.yml
name: Integration Tests

on:
  workflow_run:
    workflows: ["Deploy to Staging"]
    types: [completed]

jobs:
  integration-tests:
    if: ${{ github.event.workflow_run.conclusion == 'success' }}
    runs-on: ubuntu-latest
    strategy:
      matrix:
        env: [staging-ci-pathroute, staging-ci-subdomain]
    steps:
      - uses: actions/checkout@v4
      
      - name: Run integration tests
        env:
          STAGING_URL: https://${{ matrix.env }}.all-hands.dev
          SAML_IDP_URL: https://auth.staging.all-hands.dev
          TEST_USER_EMAIL: ${{ secrets.STAGING_TEST_USER }}
          TEST_USER_PASSWORD: ${{ secrets.STAGING_TEST_PASSWORD }}
        run: |
          pytest tests/integration/ -v --tb=short
```

**Tasks:**
- [ ] Define integration test framework (pytest-asyncio recommended)
- [ ] Implement SAML authentication helper
- [ ] Write core authentication tests
- [ ] Write routing validation tests
- [ ] Write conversation lifecycle tests
- [ ] Integrate with CI pipeline
- [ ] Extract patterns from Tim's SaaS feature tests

**Estimated Effort:** 5-7 days

---

### 6. External DNS Routing

**Requirement:** DNS records should be automatically created/updated when ingresses are created.

**Implementation: external-dns**

```yaml
# external-dns deployment
apiVersion: apps/v1
kind: Deployment
metadata:
  name: external-dns
  namespace: kube-system
spec:
  template:
    spec:
      containers:
        - name: external-dns
          image: registry.k8s.io/external-dns/external-dns:v0.14.0
          args:
            - --source=ingress
            - --domain-filter=all-hands.dev
            - --provider=google
            - --google-project=staging-092324
            - --registry=txt
            - --txt-owner-id=staging-cluster
          env:
            - name: GOOGLE_APPLICATION_CREDENTIALS
              value: /etc/secrets/gcp-credentials.json
```

**DNS Records Created:**

| Ingress Host | DNS Record | Type |
|--------------|------------|------|
| `staging-ci-pathroute.all-hands.dev` | → Load Balancer IP | A |
| `staging-ci-subdomain.all-hands.dev` | → Load Balancer IP | A |
| `*.staging-ci-subdomain.all-hands.dev` | → Load Balancer IP | A |
| `auth.staging.all-hands.dev` | → Keycloak LB IP | A |

**Tasks:**
- [ ] Deploy external-dns to staging cluster
- [ ] Configure GCP Cloud DNS permissions
- [ ] Validate automatic DNS record creation
- [ ] Set appropriate TTL values (low for staging)

**Estimated Effort:** 1 day

---

## Implementation Plan

### Phase 0: Developer Testbed (✅ COMPLETED 2026-04-16)
- [x] Create GKE cluster (`openhands-testbed`) in Platform Team Sandbox
- [x] Deploy cert-manager with ClusterIssuer
- [x] Configure traefik ingress controller
- [x] Create DNS zone (`sandbox.all-hands.dev`)
- [x] Create deployment scripts (`scripts/testbed/deploy.sh`)
- [x] Deploy test instance and validate OpenHands functionality
- [x] Write documentation (`scripts/testbed/README.md`)

### Phase 1: Foundation (Week 1)
- [x] Deploy cert-manager with ClusterIssuer *(done in Phase 0)*
- [ ] Deploy external-dns
- [x] Configure traefik ingress controller *(done in Phase 0)*
- [ ] Create 4 namespaces with base RBAC

### Phase 2: CI Environments (Week 2)
- [ ] Deploy `staging-ci-pathroute` environment
- [ ] Deploy `staging-ci-subdomain` environment
- [ ] Validate deployments with smoke tests
- [ ] Integrate with GitHub Actions

### Phase 3: Authentication (Week 2-3)
- [x] Deploy Keycloak instance *(per-namespace in testbed)*
- [ ] Configure shared SAML realm and clients
- [ ] Create test users
- [ ] Validate SAML login flow

### Phase 4: Dev Environments (Week 3)
- [x] Deploy developer testbed *(done in Phase 0)*
- [ ] Deploy `staging-dev-pathroute` environment (routing variant)
- [ ] Deploy `staging-dev-subdomain` environment (routing variant)
- [x] Create manual deployment workflow *(done in Phase 0)*
- [x] Document feature branch deployment process *(done in Phase 0)*

### Phase 5: Integration Tests (Week 3-4)
- [ ] Set up test framework
- [ ] Implement core test suite
- [ ] Integrate with CI pipeline
- [ ] Document test coverage

---

## Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| CI test pass rate | >95% | GitHub Actions |
| Deployment time | <10 min | Workflow duration |
| Environment availability | >99% | Uptime monitoring |
| Customer issue repro time | <1 hour | Engineering feedback |

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Certificate rate limiting | Medium | High | Use Let's Encrypt staging for dev envs |
| Resource costs | Medium | Medium | Auto-scale down dev envs after hours |
| Configuration drift | High | Medium | GitOps with ArgoCD (future) |
| Keycloak complexity | Medium | Medium | Start with minimal SAML config |

---

## Open Questions

1. **Shared vs isolated databases?**
   - Shared PostgreSQL cluster with per-env databases?
   - Or isolated PostgreSQL per environment?

2. **LiteLLM Proxy sharing?**
   - One LiteLLM proxy for all staging envs?
   - Or per-environment (higher cost but better isolation)?

3. **Runtime cluster?**
   - Same cluster as application?
   - Separate cluster (mirrors production)?

4. **Cost budget?**
   - What's the monthly budget for staging infrastructure?

---

## Appendix

### A. Environment URLs

**Developer Testbed (✅ LIVE):**

| Environment | Main URL | Access |
|-------------|----------|--------|
| testbed-{name} | https://testbed-{name}.sandbox.all-hands.dev | Private (`/etc/hosts` + GCP access) |

**Planned CI/Staging Environments:**

| Environment | Main URL | Automation URL |
|-------------|----------|----------------|
| staging-ci-pathroute | https://staging-ci-pathroute.all-hands.dev | https://staging-ci-pathroute.all-hands.dev/api/automation |
| staging-ci-subdomain | https://staging-ci-subdomain.all-hands.dev | https://automation.staging-ci-subdomain.all-hands.dev |
| staging-dev-pathroute | https://staging-dev-pathroute.all-hands.dev | https://staging-dev-pathroute.all-hands.dev/api/automation |
| staging-dev-subdomain | https://staging-dev-subdomain.all-hands.dev | https://automation.staging-dev-subdomain.all-hands.dev |

### B. Related Documents

- [PR #542: Staging Infrastructure](https://github.com/All-Hands-AI/OpenHands-Cloud/pull/542)
- [ARCHITECTURE.md](./ARCHITECTURE.md)
- [Testbed README](../scripts/testbed/README.md) - Developer testbed documentation
- [Testbed Deploy Script](../scripts/testbed/deploy.sh) - One-command deployment

### C. Infrastructure Details

**Developer Testbed (Platform Team Sandbox):**

| Resource | Value |
|----------|-------|
| GCP Project | `platform-team-sandbox-62793` |
| GKE Cluster | `openhands-testbed` |
| Region | `us-central1` |
| LoadBalancer IP | `34.28.75.102` |
| DNS Zone | `sandbox.all-hands.dev` (private) |

### D. Glossary

- **Path-based routing**: All services accessed via paths on a single domain
- **Subdomain-based routing**: Each service gets its own subdomain
- **ClusterIssuer**: Cluster-wide certificate issuer (cert-manager)
- **external-dns**: Kubernetes operator that creates DNS records from Ingress resources
- **Testbed**: Developer sandbox environment for testing OpenHands deployments
