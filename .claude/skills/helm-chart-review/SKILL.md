# Helm Chart Review

---
name: helm-chart-review
description: Evaluate Helm chart quality for public distribution. Identifies issues with structure, templates, values, security, and best practices. Review only — does not modify files. Use when reviewing or auditing a Helm chart.
triggers:
- /helm-chart-review
- /helm-review
---

You are a Helm chart quality reviewer. Your job is to evaluate a chart's suitability for **public distribution** — meaning consumers in diverse environments (cloud providers, on-prem, air-gapped, OpenShift, vanilla Kubernetes) should be able to use it effectively without assumptions about their setup.

## How to perform a review

### Step 1: Discover the chart

Locate the chart root (the directory containing `Chart.yaml`). If the user specified a path, use it. Otherwise, search the current directory and immediate subdirectories.

Read these files to understand the chart:

1. `Chart.yaml` — metadata, dependencies, version
2. `values.yaml` — all configurable parameters
3. `templates/_helpers.tpl` — helper templates
4. All files in `templates/` — resource manifests
5. `templates/NOTES.txt` — post-install notes
6. `charts/` — vendored subcharts (if present)
7. `.helmignore` — ignored files
8. `README.md` — documentation (if present)
9. `templates/tests/` — test hooks (if present)
10. `crds/` — CRD definitions (if present)

### Step 2: Run automated checks

Run `helm lint <chart-path>` and `helm template <chart-path>` to catch syntax and rendering errors. If helm is not available, skip and note it.

Also run `grep -rn "emptyDir\|/tmp\|FILE_STORE.*local\|LOCAL_STORAGE" templates/ values.yaml` — every hit on a data-bearing path must be justified in the findings (see the State lifetime checklist).

### Step 3: Evaluate against the checklist

Work through every section of the checklist below. For each item, determine:

- **PASS** — meets the standard
- **FAIL** — violates the standard (must fix)
- **WARN** — suboptimal but not broken (should fix)
- **SKIP** — not applicable to this chart

### Step 4: Report findings

Present findings organized by severity, then by category. Use this format:

```
## Helm Chart Review: <chart-name> <chart-version>

### Critical Issues (FAIL)
- [Category] Description of issue
  - File: `path/to/file.yaml`, Line: N
  - Fix: What to do

### Warnings (WARN)
- [Category] Description of issue
  - File: `path/to/file.yaml`, Line: N
  - Recommendation: What to improve

### Passed Checks
- [Category] Brief summary of what passed

### Summary
- Critical: N issues
- Warnings: N issues
- Passed: N checks
- Overall: PASS / NEEDS WORK / FAIL
```

**Do NOT modify any chart files.** This skill is review-only. Present the report and let the user decide what to fix.

---

## Review Checklist

### 1. Chart.yaml — Metadata & Structure

#### Required fields
- `apiVersion` is set (`v2` for Helm 3 charts)
- `name` uses only lowercase letters, numbers, and dashes (no underscores, dots, or uppercase)
- `version` follows SemVer 2 (e.g. `1.2.3`)
- `appVersion` is set and reflects the packaged application version
- `description` is present and meaningful (not boilerplate)
- `type` is set to `application` or `library` as appropriate

#### Recommended fields
- `home` URL is set if the project has a homepage
- `sources` lists source repositories
- `maintainers` has at least one entry with name and email
- `icon` URL is set (required for Artifact Hub / chart repositories)
- `keywords` are present for discoverability
- `kubeVersion` constraint is set if the chart requires specific Kubernetes versions

#### Dependencies
- Dependencies use version ranges (`~1.2.3` or `^1.2.3`), not exact pins, to receive patches
- Repository URLs use `https://` (not `http://`)
- Each dependency that is optional has a `condition` field (e.g. `redis.enabled`) defaulting to `true` or `false` as appropriate
- Related optional dependencies share `tags` for grouped enable/disable
- `Chart.lock` is committed if dependencies exist
- If a dependency has `repository: ""`, a matching directory exists in `charts/`

### 2. values.yaml — Configuration Design

#### Naming & structure
- All variable names start with a lowercase letter
- Multi-word names use camelCase (not `kebab-case` or `snake_case`)
- Structure is as flat as practical — nesting only where a group of related values has at least one non-optional member
- No orphaned nesting (single-child objects that could be flattened)
- Every key names its observable effect. The test: an operator who has never
  seen the vendor's infrastructure can predict what the key does from the name
  alone. Keys named for an internal scenario, environment type, or team
  workflow (`ephemeral`, `poc`, `saasMode`) FAIL even when the comment explains
  them — the comment doesn't travel with the key into consumer values files.
- A boolean's name must stay truthful in every value combination it can
  coexist with. Enumerate the combinations: if `ephemeral: true` can coexist
  with `persistence.enabled: true`, the name lies in that quadrant. FAIL.
- One knob, one effect. A value that toggles a bundled dependency AND selects
  env wiring AND drives another feature's default is three knobs sharing a
  name. Deploying an optional bundled dependency is always spelled
  `<dependency>.enabled` — never inferred from a mode flag.

#### Cross-cutting consistency (umbrella charts)
- Cross-cutting concerns (object storage, database, credentials, CA bundles)
  use one values shape and one vocabulary across the parent and every
  subchart. A subchart introducing its own bespoke storage block, or a second
  spelling for the same backend (`gcs` vs `google_cloud`), is a FAIL.
- Service-internal vocabulary stays internal: when two services natively spell
  the same backend differently, the templates normalize, and the values
  interface exposes a single spelling.

#### Type safety
- All string values are quoted (prevents YAML coercion of numbers like `012345` to octal or `1e3` to scientific notation)
- Large integers are stored as quoted strings with `{{ int $value }}` conversion in templates
- Boolean values are actual YAML booleans (`true`/`false`), not strings

#### Documentation
- Every parameter has a comment explaining its purpose
- Comments follow a consistent format (preferably `## @param` or `# --` for generator compatibility)
- Default values are sensible for a fresh install — the chart should render and deploy with zero user-supplied values

#### Image configuration
The image block should follow this structure to support private registries, air-gapped environments, and global overrides:
```yaml
image:
  registry: docker.io
  repository: org/app
  tag: "1.0.0"         # Always quoted, never "latest"
  digest: ""            # Optional SHA256 override
  pullPolicy: IfNotPresent
  pullSecrets: []       # List of imagePullSecret names
```
- `registry` is separate from `repository` (allows `global.imageRegistry` override)
- `tag` is a fixed version, never `latest`, `head`, or `canary`
- `pullSecrets` is available for private registry auth
- A helper template constructs the full image reference

#### Resource management
```yaml
resources: {}
  # limits:
  #   cpu: 250m
  #   memory: 256Mi
  # requests:
  #   cpu: 100m
  #   memory: 128Mi
```
- Resources default to empty `{}` or commented examples (not hard-coded — environments vary wildly)
- Alternatively, a `resourcesPreset` pattern (small/medium/large) with custom `resources` override is acceptable

#### Persistence
```yaml
persistence:
  enabled: true
  storageClass: ""       # Empty string = use cluster default
  accessModes:
    - ReadWriteOnce
  size: 8Gi
  existingClaim: ""      # Allow bringing your own PVC
  annotations: {}
```
- `enabled` toggle exists
- `storageClass: ""` falls back to the cluster default (not hard-coded to a specific class)
- `existingClaim` is supported so users can bring pre-provisioned PVCs
- `accessModes` is configurable (some environments lack RWX)
- `size` has a reasonable default

#### State lifetime
- For every backend-selection env the templates render (`FILE_STORE`,
  `*_STORAGE_PATH`, local paths): trace where written data lands. Data that
  outlives a request but lives on the pod filesystem (container fs,
  `emptyDir`, `/tmp`) with no PVC is a FAIL unless the finding can state why
  the data is disposable. Cite the pod-replacement scenario explicitly in the
  finding.
- If a service stores references in a database that point at objects in a
  file store, the two stores must share a lifetime. A database that survives
  upgrades pointing at a file store that doesn't means orphaned references
  and runtime failures.

#### Service configuration
```yaml
service:
  type: ClusterIP
  port: 80
  annotations: {}
```
- `type` is configurable (ClusterIP, NodePort, LoadBalancer)
- Port numbers are configurable
- Annotations are injectable (for cloud load balancer configuration)

#### Ingress configuration
```yaml
ingress:
  enabled: false
  className: ""
  annotations: {}
  hosts:
    - host: chart-example.local
      paths:
        - path: /
          pathType: ImplementationSpecific
  tls: []
```
- Disabled by default
- `className` (for Kubernetes >=1.18 `ingressClassName`) and `annotations` are configurable
- Both `hosts` and `tls` are configurable lists
- Does not assume a specific ingress controller

#### Security contexts
```yaml
podSecurityContext:
  fsGroup: 1001
containerSecurityContext:
  runAsUser: 1001
  runAsNonRoot: true
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL
  seccompProfile:
    type: RuntimeDefault
```
- Pod and container security contexts are separate and configurable
- Defaults are restrictive (non-root, read-only root fs, drop all capabilities)
- Values are overridable for environments with different UID requirements (e.g. OpenShift assigns UIDs from namespace ranges)

#### Service account & RBAC
```yaml
serviceAccount:
  create: true
  name: ""
  annotations: {}
  automountServiceAccountToken: false
rbac:
  create: true
```
- `serviceAccount` and `rbac` are separate configuration sections
- `create` booleans default to `true`
- `serviceAccount.name` falls back to a generated name via helper template
- `automountServiceAccountToken: false` by default (mount only if needed)
- When `serviceAccount.create` is false and no name given, the `default` SA is used

#### Health probes
```yaml
livenessProbe:
  enabled: true
  initialDelaySeconds: 30
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 6
  successThreshold: 1
readinessProbe:
  enabled: true
  initialDelaySeconds: 5
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 6
  successThreshold: 1
startupProbe:
  enabled: false
  initialDelaySeconds: 0
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 30
  successThreshold: 1
```
- All three probe types (liveness, readiness, startup) are configurable
- Each has an `enabled` toggle
- Timing parameters are configurable (not hardcoded in templates)
- Probe endpoints/commands are configurable or use sensible defaults

#### Extensibility
- `extraEnvVars: []` — inject additional environment variables
- `extraVolumes: []` and `extraVolumeMounts: []` — mount additional volumes
- `extraContainers: []` or `sidecars: []` — add sidecar containers
- `initContainers: []` — add custom init containers
- `podAnnotations: {}` and `podLabels: {}` — inject metadata
- `nodeSelector: {}`, `tolerations: []`, `affinity: {}` — scheduling constraints
- `topologySpreadConstraints: []` — zone-aware scheduling
- `extraArgs: []` or `extraFlags` — pass additional CLI arguments to the application
- `existingConfigmap: ""` / `existingSecret: ""` — use pre-existing resources

#### Network policies
```yaml
networkPolicy:
  enabled: false
  allowExternal: true
```
- Network policy support exists (even if disabled by default)
- When enabled, policies should not assume a specific CNI — only use standard `NetworkPolicy` resources

### 3. Templates — Structure & Quality

#### File organization
- One Kubernetes resource per template file
- Files use `.yaml` extension for YAML output, `.tpl` for non-output helpers
- File names use dashed notation reflecting the resource kind (e.g. `deployment.yaml`, `service.yaml`, `hpa.yaml`)
- `_helpers.tpl` exists with named helper templates
- `NOTES.txt` exists with post-install instructions

#### Named template conventions
- All `define` names are namespaced with the chart name (e.g. `{{ define "mychart.fullname" }}`)
- `include` is used instead of `template` (enables piping to `indent`, `nindent`, `toYaml`)
- Helper templates cover at minimum: `name`, `fullname`, `chart`, `labels`, `selectorLabels`, `serviceAccountName`
- Scope (`.`) is always passed to `include`/`template` calls

#### Labels
All resources include these standard labels:
- `app.kubernetes.io/name` — the app name (from `Chart.Name` or override)
- `app.kubernetes.io/instance` — `{{ .Release.Name }}`
- `app.kubernetes.io/version` — `{{ .Chart.AppVersion }}`
- `app.kubernetes.io/managed-by` — `{{ .Release.Service }}`
- `helm.sh/chart` — `{{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}`

Selector labels (`spec.selector.matchLabels`) include only **stable** labels:
- `app.kubernetes.io/name`
- `app.kubernetes.io/instance`
- Optionally `app.kubernetes.io/component` for multi-component charts

Selector labels must NOT include version or chart version (these change on upgrade and would break rolling updates).

#### Namespace handling
- Templates do NOT hardcode `metadata.namespace` — the namespace comes from `helm install --namespace`
- Exception: if the chart explicitly needs cross-namespace resources, use `{{ .Release.Namespace }}` or a configurable value

#### Whitespace & formatting
- Templates use 2-space indentation (no tabs)
- Template directives have spaces after `{{` and before `}}` (e.g. `{{ .Values.foo }}` not `{{.Values.foo}}`)
- Whitespace chomping (`{{-` and `-}}`) is used to avoid blank lines in rendered output
- Rendered YAML is clean — no excessive blank lines, no broken indentation

#### Template safety
- `required` function is used for values that must be user-supplied (prevents silent deployment of broken configs)
- `default` function provides fallbacks where appropriate
- `quote` or `toYaml` is used when injecting values into YAML to prevent type coercion issues
- `lookup` function is used to preserve existing secrets across upgrades (prevents password rotation on `helm upgrade`)
- `fail` is used to give clear error messages for invalid value combinations
- No YAML comments (`#`) on lines consumed by `required` or other template functions (this breaks rendering)

#### Conditional resources
- Optional resources (Ingress, HPA, PDB, NetworkPolicy, ServiceMonitor) are wrapped in `{{ if .Values.<feature>.enabled }}`
- RBAC resources are wrapped in `{{ if .Values.rbac.create }}`
- ServiceAccount creation is wrapped in `{{ if .Values.serviceAccount.create }}`

#### Deployment best practices in templates
- `spec.selector.matchLabels` is explicitly defined (not implicit from all labels)
- Image references use the helper template, not inline string concatenation
- Container ports have `name` fields
- Environment variables from secrets use `secretKeyRef` (not inline values)
- ConfigMap changes trigger pod restarts via checksum annotation:
  ```yaml
  annotations:
    checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
  ```
- PVCs with `ReadWriteOnce` use `Recreate` update strategy (avoids multi-attach errors)
- `terminationGracePeriodSeconds` is configurable for stateful apps

### 4. NOTES.txt — Post-Install Instructions

- File exists at `templates/NOTES.txt`
- Uses template syntax to provide dynamic, release-specific output
- Includes instructions for accessing the application (service URL, port-forward commands)
- Mentions the release name and namespace
- Includes any required follow-up steps (e.g. getting the admin password, setting up DNS)
- Does not include stale/generic boilerplate

### 5. Security — Hardening for Public Use

- Containers run as non-root by default
- Root filesystem is read-only where feasible (writable paths use `emptyDir` volumes)
- All capabilities are dropped, with only necessary ones re-added
- `seccompProfile` is set to `RuntimeDefault` or more restrictive
- `allowPrivilegeEscalation: false` by default
- Secrets are not stored in ConfigMaps
- Sensitive values use Kubernetes Secrets, not plain environment variables inline
- If passwords can be auto-generated, the chart uses `lookup` to avoid regenerating on upgrade
- `automountServiceAccountToken: false` unless the pod actually calls the Kubernetes API
- RBAC roles use least-privilege (no `*` wildcards on verbs/resources unless truly needed)
- Network policies restrict traffic when enabled
- Init containers that require elevated privileges have their own (minimal) security context

### 6. Portability — Works Everywhere

- No hard-coded namespaces, node names, storage classes, or ingress classes
- No cloud-provider-specific annotations baked into templates (they should come from values)
- StorageClass defaults to `""` (cluster default), not a specific provider class
- Service type defaults to `ClusterIP` (universally supported)
- Ingress is disabled by default and controller-agnostic
- The chart renders and passes `helm template` with only default values
- `kubeVersion` constraint in `Chart.yaml` if the chart uses APIs not available in all Kubernetes versions
- CRDs go in the `crds/` directory (not templates) and are not templated
- No assumptions about specific CSI drivers, admission controllers, or cluster add-ons

### 7. Documentation

- `values.yaml` has comments for every parameter
- `README.md` exists with: chart description, prerequisites, installation instructions, configuration parameter table, and upgrade notes
- `NOTES.txt` provides actionable post-install output
- Upgrade/migration notes for breaking changes between versions

### 8. Testing

- `templates/tests/` directory exists with at least one test
- Test pods use the `helm.sh/hook: test` annotation
- Tests validate the deployment actually works (e.g. connectivity check), not just that templates render
- `helm lint` passes with no errors
- `helm template` renders valid YAML with default values

### 9. Operational Excellence

- PodDisruptionBudgets are available for HA deployments
- HorizontalPodAutoscaler configuration is available if the app supports scaling
- Update strategy is configurable (`RollingUpdate` vs `Recreate`)
- Prometheus metrics exposure is available (ServiceMonitor, annotations, or dedicated metrics service)
- Log output goes to stdout/stderr (no file-based logging assumptions)
- The chart supports `helm upgrade` cleanly (no manual intervention required between versions)
- Resource annotations include `helm.sh/resource-policy: keep` for PVCs or other resources that should survive uninstall

---

## Severity Classification

When evaluating issues, use these severity levels:

**FAIL (Critical)** — Will break deployments, cause security vulnerabilities, or make the chart unusable in common environments:
- Missing required Chart.yaml fields
- Hardcoded namespaces, storage classes, or cloud-specific values in templates
- `latest` or floating image tags
- Running as root with no option to change
- Selector labels that include version (breaks upgrades)
- Templates that fail `helm template` with default values
- Secrets in ConfigMaps
- Missing `enabled` toggles on optional resources

**WARN (Should Fix)** — Reduces chart quality, portability, or user experience but won't cause immediate failures:
- Missing value documentation
- Missing extensibility points (extraEnvVars, extraVolumes, etc.)
- No NOTES.txt or generic boilerplate NOTES.txt
- No health probes or hardcoded probe config
- Missing resource request/limit configurability
- No network policy support
- No PDB support for HA workloads
- Using `template` instead of `include`
- Flat structure where nesting would improve clarity (or vice versa)

**PASS** — Meets or exceeds the standard for public distribution.
