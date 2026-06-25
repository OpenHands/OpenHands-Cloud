# Deploy OpenHands Branch to Platform Team Sandbox

## Description
Deploy an OpenHands branch or PR to the Platform Team Sandbox environment (`ohe-staging.platform-team.all-hands.dev`). This skill helps developers quickly test their changes on a real Kubernetes cluster with a dedicated URL.

## Triggers
- "deploy my branch"
- "deploy PR #<number>"
- "deploy to staging"
- "test my changes on staging"
- "create a branch deployment"
- "deploy OpenHands PR"

## Prerequisites Check

Before deploying, verify the user has:

1. **GCloud Authentication**: Run `gcloud auth login` if kubectl commands fail
2. **Cluster Access**: 
   ```bash
   gcloud container clusters get-credentials ohe-staging-cluster \
     --region us-central1 \
     --project platform-team-sandbox
   ```
3. **GitHub CLI**: Required to look up PR details
   ```bash
   gh auth status
   ```

## Information Gathering

Ask the user for the following (in order):

### 1. Image Source
Ask: "What would you like to deploy? Please provide one of:"
- A PR number from the **OpenHands repo** (e.g., `#14343`) - I'll look up the image tag from CI
- A branch name (e.g., `main`, `feature-xyz`) - I'll find the latest image
- A specific image tag (e.g., `0.48.0`, `sha-abc1234`)

### 2. Branch Name for URL
Ask: "What should the branch deployment be called? This becomes your URL subdomain."
- Must be lowercase, alphanumeric, and hyphens only
- Example: `pr-14343` → `pr-14343.ohe-staging.platform-team.all-hands.dev`
- Default: `pr-<number>` format for PR deployments

## Image Tag Lookup from OpenHands PR

**IMPORTANT**: The PR is from the **OpenHands/OpenHands** repository, NOT this repo (OpenHands-Cloud).

### Step 1: Look up the PR details
```bash
# Get PR info from the OpenHands repo
gh pr view <PR_NUMBER> --repo All-Hands-AI/OpenHands --json headRefName,headRefOid,commits

# Example output:
# {
#   "headRefName": "sv-console-message-test",
#   "headRefOid": "abc1234567890...",
#   "commits": [...]
# }
```

### Step 2: Determine the image tag
The enterprise-server images are built from OpenHands commits. The tag format is:
- **For releases**: `0.48.0`, `1.0.0`, etc.
- **For commits**: First 7 characters of commit SHA, e.g., `abc1234`

```bash
# Get the latest commit SHA from the PR
COMMIT_SHA=$(gh pr view <PR_NUMBER> --repo All-Hands-AI/OpenHands --json headRefOid -q '.headRefOid')
IMAGE_TAG="${COMMIT_SHA:0:7}"
echo "Image tag: ${IMAGE_TAG}"
```

### Step 3: Verify the image exists
```bash
# Check if the image was built by CI
# The image should be at: ghcr.io/openhands/enterprise-server:<tag>
# You can verify by checking the PR's CI status or the GHCR registry
```

## Deployment Steps

### Step 1: Set Environment Variables

```bash
export BRANCH_NAME="pr-<PR_NUMBER>"  # e.g., pr-14343
export NAMESPACE="openhands-${BRANCH_NAME}"
export IMAGE_TAG="<commit-sha-7-chars>"  # from PR lookup
```

### Step 2: Create Namespace

```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```

### Step 3: Copy Required Secrets

```bash
# These secrets must be copied from the all-hands-system namespace
SECRETS=(
  "ghcr-login-secret"
  "postgres-password"
  "redis"
  "keycloak-admin"
  "keycloak-db-secret"
  "lite-llm-api-key"
  "stripe-api-key"
  "resend-api-key"
  "github-app"
  "bitbucket-app"
  "gitlab-auth"
  "automation-webhook-secret"
  "automation-service-key"
  "automation-db-secret"
)

for secret in "${SECRETS[@]}"; do
  kubectl get secret "$secret" -n all-hands-system -o yaml 2>/dev/null | \
    sed "s/namespace: all-hands-system/namespace: ${NAMESPACE}/" | \
    kubectl apply -n ${NAMESPACE} -f - 2>/dev/null || \
    echo "Warning: Secret $secret not found in all-hands-system"
done
```

### Step 4: Deploy with Helm

```bash
cd /path/to/OpenHands-Cloud  # Repository root

helm upgrade --install openhands-${BRANCH_NAME} ./charts/openhands \
  --namespace ${NAMESPACE} \
  --values testenv-charts/helm/environments/staging/base-values.yaml \
  --set image.tag="${IMAGE_TAG}" \
  --set branchSanitized="${BRANCH_NAME}" \
  --set ingress.host="ohe-staging.platform-team.all-hands.dev" \
  --set keycloak.ingress.hostname="${BRANCH_NAME}.auth.ohe-staging.platform-team.all-hands.dev" \
  --set keycloak.externalDatabase.host="openhands-${BRANCH_NAME}-postgresql" \
  --set automation.database.host="openhands-${BRANCH_NAME}-postgresql" \
  --set automation.minio.external.endpoint="openhands-${BRANCH_NAME}-minio:9000" \
  --set automationService.url="https://${BRANCH_NAME}.ohe-staging.platform-team.all-hands.dev/api/automation" \
  --wait --timeout 15m
```

### Step 5: Verify Deployment

```bash
# Check pods are running
kubectl get pods -n ${NAMESPACE}

# Check ingress is created
kubectl get ingress -n ${NAMESPACE}

# Wait for main pod to be ready
kubectl wait --for=condition=Ready pods -l app.kubernetes.io/name=openhands -n ${NAMESPACE} --timeout=300s
```

### Step 6: Provide Access URL

Once deployed, the application is available at:
```
https://<branch-name>.ohe-staging.platform-team.all-hands.dev
```

Auth (Keycloak) is at:
```
https://<branch-name>.auth.ohe-staging.platform-team.all-hands.dev
```

## Troubleshooting

### Pods stuck in Init or Pending state
```bash
# Check init container logs
kubectl logs -n ${NAMESPACE} <pod-name> -c wait-for-db
kubectl logs -n ${NAMESPACE} <pod-name> -c wait-for-redis

# Check events
kubectl get events -n ${NAMESPACE} --sort-by='.lastTimestamp'
```

Common causes:
- Missing secrets (re-run secret copy step)
- Database not ready (wait for PostgreSQL pod)
- Image pull failure (check ghcr-login-secret)

### Image pull errors (ImagePullBackOff)
```bash
# Verify ghcr-login-secret exists and is valid
kubectl get secret ghcr-login-secret -n ${NAMESPACE}

# Check pod events for specific error
kubectl describe pod -n ${NAMESPACE} <pod-name> | grep -A5 "Events:"

# Common fix: Re-copy the secret
kubectl get secret ghcr-login-secret -n all-hands-system -o yaml | \
  sed "s/namespace: all-hands-system/namespace: ${NAMESPACE}/" | \
  kubectl apply -n ${NAMESPACE} -f -
```

### CrashLoopBackOff
```bash
# Check application logs
kubectl logs -n ${NAMESPACE} <pod-name> --previous

# Common causes:
# - Missing environment variables
# - Database connection issues
# - Redis connection issues
```

### Database connection issues
```bash
# Verify PostgreSQL is running
kubectl get pods -n ${NAMESPACE} | grep postgresql

# Test connectivity
kubectl run pg-test -n ${NAMESPACE} --rm -it --image=postgres:15 -- \
  psql -h openhands-${BRANCH_NAME}-postgresql -U postgres -d openhands -c '\l'
```

## Cleanup

When done testing, remove the deployment:

```bash
# Remove Helm release
helm uninstall openhands-${BRANCH_NAME} -n ${NAMESPACE}

# Delete namespace (removes all resources)
kubectl delete namespace ${NAMESPACE}
```

## Example Conversation Flow

**User**: Deploy OpenHands PR #14343 to staging

**Agent**: I'll help you deploy PR #14343 from the OpenHands repository. Let me look up the PR details...

```bash
gh pr view 14343 --repo All-Hands-AI/OpenHands --json headRefName,headRefOid
```

Found:
- **Branch**: `sv-console-message-test`
- **Commit**: `abc123456789...`
- **Image tag**: `abc1234` (first 7 chars)

I'll create a deployment called `pr-14343`:
- **URL**: `https://pr-14343.ohe-staging.platform-team.all-hands.dev`
- **Auth URL**: `https://pr-14343.auth.ohe-staging.platform-team.all-hands.dev`

Should I proceed with the deployment?

**User**: Yes, deploy it

**Agent**: Deploying...

1. ✅ Created namespace `openhands-pr-14343`
2. ✅ Copied secrets from `all-hands-system`
3. ⏳ Installing Helm chart...

[After deployment completes]

✅ **Deployment complete!**

Your branch is now available at:
- **App**: https://pr-14343.ohe-staging.platform-team.all-hands.dev
- **Auth**: https://pr-14343.auth.ohe-staging.platform-team.all-hands.dev

Pod status:
```
NAME                          READY   STATUS    
openhands-pr-14343-xxx        1/1     Running
openhands-pr-14343-minio-xxx  1/1     Running
openhands-pr-14343-redis-xxx  1/1     Running
keycloak-xxx                  1/1     Running
```

To clean up when done: `kubectl delete namespace openhands-pr-14343`

## Quick Reference

| Item | Value |
|------|-------|
| Cluster | `ohe-staging-cluster` (us-central1) |
| Project | `platform-team-sandbox` |
| Base domain | `ohe-staging.platform-team.all-hands.dev` |
| Auth domain | `auth.ohe-staging.platform-team.all-hands.dev` |
| Runtime domain | `runtime.ohe-staging.platform-team.all-hands.dev` |
| LLM Proxy | `llm-proxy.ohe-staging.platform-team.all-hands.dev` |
| Base values file | `testenv-charts/helm/environments/staging/base-values.yaml` |
| Secrets source namespace | `all-hands-system` |
| OpenHands Repo | `All-Hands-AI/OpenHands` |
| Enterprise Image | `ghcr.io/openhands/enterprise-server` |
