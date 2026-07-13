# PR 862 live evidence for OpenHands/integrations-hub#378

Date: 2026-07-13 UTC

## SHAs

- OpenHands-Cloud current main tested: `8ae4ebcaa7fb6ca3a9629efc10cf272a4c6b2c11`
- OpenHands-Cloud PR chart fix tested: `a1b2ea7503ec968ec91aff50ac62e783df4b1df6`
- Original observed PR head before remediation: `a0337aff0ee68b95dfad22197e1b8b3b0f6982bf`
- OpenHands/integrations-hub app image source: `6dd48510d6c31e5e8d360a4c618d2bec33ebcc81`

## Setup

GHCR returned 403 for `ghcr.io/openhands/integrations-hub:latest` with the available token, so the app image was built locally from the linked `OpenHands/integrations-hub` repo instead:

```bash
gh repo clone OpenHands/integrations-hub /tmp/integrations-hub-pr862 -- --depth 1
docker build -f containers/Dockerfile -t integrations-hub:evidence-6dd4851 /tmp/integrations-hub-pr862
kind create cluster --name pr862 --image kindest/node:v1.30.0
docker run -d --restart=always -p 127.0.0.1:5001:5000 --name kind-registry-pr862 registry:2
docker tag integrations-hub:evidence-6dd4851 localhost:5001/integrations-hub:evidence-6dd4851
docker push localhost:5001/integrations-hub:evidence-6dd4851
```

Both `main` and PR chart installs used the same values:

```bash
--set image.repository=localhost:5001/integrations-hub
--set image.tag=evidence-6dd4851
--set database.host=postgres
--set database.sslMode=disable
--set datadog.enabled=true
--set datadog.env=evidence
--set datadog.serviceName=integrations-hub
--set disableAuth=true
```

Each namespace used an isolated local Postgres deployment plus local-only Kubernetes secrets for the DB password, credential encryption key, and cron secret. Secret values are intentionally not recorded.

## Results

- `origin/main` rendered startup, liveness, and readiness probes as `/integrations-hub/api/health` with Kubernetes default `timeoutSeconds: 1`. The real app process served pod-local `/api/health`, `/api/live`, and `/api/ready`, while the prefixed probe paths returned 404. Helm timed out with `context deadline exceeded`.
- The remediated PR chart rendered startup/liveness as `/api/live`, readiness as `/api/ready`, and `timeoutSeconds: 2` on all three probes. Helm installed successfully and the pod reached `1/1 Running`.
- With Postgres scaled to zero under the PR chart, `/api/live` stayed 200, `/api/ready` returned 503, kubelet marked the pod NotReady, and the container restart count stayed 0. Restoring Postgres returned `/api/ready` to 200 and the pod to Ready.
- Runtime Datadog env on `main` included only `DD_AGENT_HOST`, `DD_TRACE_AGENT_PORT`, `DD_SERVICE`, `DD_ENV`, and `DD_TRACE_ENABLED`.
- Runtime Datadog env on the PR chart additionally included `DD_DOGSTATSD_PORT`, `DD_VERSION`, `DD_LOGS_INJECTION`, `DD_TRACE_SAMPLING_RULES`, `LOG_JSON`, and `LOG_JSON_LEVEL_KEY`.

## Evidence files

- `logs/tool-and-sha-summary.txt`: tool versions and source SHAs.
- `rendered/main.yaml`, `rendered/pr.yaml`: full rendered standalone chart manifests.
- `json/main-deployment-summary.json`, `json/pr-deployment-summary.json`: redacted probe and Datadog env summaries from the live Kubernetes deployments.
- `logs/helm-install-main-timeout.log`, `logs/main-events-after-timeout.txt`, `logs/main-endpoint-statuses.txt`: current-main failure evidence.
- `logs/helm-install-pr.log`, `logs/pr-pods-after-install.txt`, `logs/pr-endpoint-statuses-db-up.txt`: PR install success and DB-up endpoint evidence.
- `logs/pr-db-failure-watch.txt`, `logs/pr-events-after-db-down.txt`, `logs/pr-pods-after-db-down.txt`: PR DB-readiness failure evidence.
- `logs/summary-observations.txt`: compact combined observation summary.
