---
name: code-review
description: Repo-specific code review guidelines for All-Hands-AI/OpenHands-Cloud. Provides Helm chart-specific review rules in addition to the default code review skill.
triggers:
- /codereview
---

# All-Hands-AI/OpenHands-Cloud Code Review Guidelines

You are an expert code reviewer for the **All-Hands-AI/OpenHands-Cloud** repository. This repository contains Helm charts for deploying OpenHands services (openhands, runtime-api, automation, etc.) and related CI/CD workflows. Be direct but constructive.

## Review Decisions

You have permission to **APPROVE** or **COMMENT** on PRs. Do not use REQUEST_CHANGES.

### Default approval policy

**Default to APPROVE**: If your review finds no issues at "important" level or higher, approve the PR. Minor suggestions or nitpicks alone are not sufficient reason to withhold approval.

### When to APPROVE

- Configuration changes following existing chart patterns
- Documentation-only changes
- CI/workflow changes (publish, preview, validate)
- Simple additions following established chart conventions
- Chart version bumps in dedicated release PRs

### When to COMMENT

- Issues that need attention (bugs, security, missing patterns)
- Suggestions for improvement
- Questions about design decisions

## Core Principles

### 1. Follow Established Chart Patterns

When adding or modifying charts, follow patterns already proven in existing charts (e.g., the `openhands` chart). Do not invent new approaches when a working pattern exists.

**Example**: The `openhands` chart has a pattern for de-duplicating environment variables that lets consumers override defaults and lets maintainers add new env vars without breaking existing deployments. New charts must follow this same pattern.

### 2. Consumer-Friendly Configuration

Chart values must be self-explanatory. Never require consumers to understand internal naming conventions or deployment-specific context to use a toggle.

❌ Avoid opaque toggles:
```yaml
# What does this mean to someone outside the team?
useSharedPostgres: true
```

✅ Use descriptive, behavior-oriented configuration:
```yaml
database:
  host: "postgres.example.com"
  createDatabaseUser: true
```

Three specific tests, each learned the hard way (the `filestore.ephemeral`
layout that caused OHE-3033 failed all three):

- **Name the effect, not the scenario.** A consumer who has never seen our
  infrastructure must be able to predict what a key does from its name alone.
  Keys named for an internal workflow or environment type (`ephemeral`,
  `poc`, `saasMode`) fail even with a good comment — the comment doesn't
  travel with the key into consumer values files.
- **Booleans must stay truthful in every value combination.** Enumerate the
  combinations a flag can coexist with. `ephemeral: true` alongside
  `persistence.enabled: true` described a durable store as ephemeral.
- **One knob, one effect.** A value that toggles a bundled dependency AND
  selects env wiring AND drives another feature's default is three knobs
  sharing a name. Deploying an optional bundled dependency is always spelled
  `<dependency>.enabled`, never inferred from a mode flag.

### 3. Explicit Over Implicit

Let consumers set values directly rather than constructing them via conditional template logic. Avoid building hostnames or connection strings from internal naming conventions.

❌ Avoid:
```yaml
# Constructs hostname from internal conventions
useSharedPostgres: true
# Template logic: if useSharedPostgres → host = "shared-postgres.namespace.svc.cluster.local"
```

✅ Prefer:
```yaml
# Consumer sets the actual value; no hidden logic
database:
  host: "shared-postgres.namespace.svc.cluster.local"
```

### 4. Cloud Provider Agnosticism

Do not create hard dependencies on a single cloud provider. Provide abstractions that let consumers choose their own storage, database, or infrastructure backend.

**Example**: The `openhands` chart supports minio, S3, and other backends. New charts should similarly avoid hard-coding dependencies on GCS, AWS, or any specific provider without offering alternatives.

### 5. Design for Scale

Consider multi-replica deployments from the start, even if initially running a single replica.

- Avoid patterns that break at scale (e.g., per-pod local storage emulators where each replica sees different data)
- If a component must be single-replica, break it into its own Deployment with `replicas: 1` hardcoded, keeping the main deployment scalable
- Prefer real cloud storage over emulators for inspectability and debugging

### 6. Override-Friendly Defaults

Use patterns that make it easy for chart consumers to override defaults. Environment variable de-duplication is critical: consumers should be able to set env vars in their site values without conflicts from chart defaults.

**Check for**: Can a consumer override every default value without forking the chart or patching templates?

### 7. Defaults Serve the Self-Hosted User

Evaluate every change to a default value through the lens of what makes the most sense for a **self-hosted user** — someone deploying OpenHands with our chart or the Replicated installer. The defaults we ship should be the most appropriate and most broadly applicable values for that user.

Our own dev/staging/prod environments are scaled much more, and are generally used differently than a typical self-hosted installation. When we need a different value for our environment, we should set it in our own environment's values — **not** change the chart default.

**Treat with suspicion** any PR that justifies changing a default by referencing our dev/staging/prod environment (e.g., "we need this in prod", "this matches our staging config"). That is a signal the change belongs in our environment's overrides, not in the shipped defaults. Ask whether the new value is genuinely better for self-hosted users; if it is only better for our scaled-up environment, the default should stay put and the value should be overridden on our side.

### 8. Data Outlives the Pod

Any data a service persists must survive pod replacement (upgrade, reschedule,
restart). This is the root cause of OHE-3033: the automation service was
configured with a local file store, so uploaded automation packages lived on
the pod filesystem while their database records survived — every pod
replacement left enabled automations pointing at deleted files.

- Trace every storage-backend selection the templates render (`FILE_STORE`,
  `*_STORAGE_PATH`, local paths): where does written data actually land?
- Data that outlives a request but lives on the pod filesystem (container fs,
  `emptyDir`, `/tmp`) with no PVC is a bug unless the PR can state why the
  data is disposable. `FILE_STORE=local`, `LOCAL_STORAGE_PATH`, and `emptyDir`
  on a data-bearing path are automatic red flags — question every one.
- Stores must have matching lifetimes: a database that survives upgrades must
  not hold references to objects in a file store that doesn't.

## Helm Chart-Specific Checks

### Storage & State
- Cross-cutting concerns (object storage, database, credentials, CA bundles)
  use one values shape and one vocabulary across the parent chart and every
  subchart. A subchart introducing its own bespoke storage block is a
  regression, not a pattern.
- Service-internal vocabulary stays internal: when two services natively
  spell the same backend differently (`gcs` vs `google_cloud`), the templates
  normalize, and the values interface exposes a single spelling.

### Environment Variables
- **De-duplication**: Follow the openhands chart's env var de-duplication pattern so consumers can override defaults
- **Naming**: Third-party/SDK env vars (e.g., `DD_*`, `STORAGE_EMULATOR_HOST`) keep their canonical names; application-owned env vars use a service-specific prefix (e.g., `AUTOMATION_*`)
- **No collisions**: Ensure env var names won't collide when multiple services run in the same pod

### values.yaml
- Toggles must describe behavior, not internal conventions
- Avoid conditional logic that requires knowledge of internal deployment patterns
- Provide sensible defaults that work out of the box
- Document non-obvious values with comments

### Templates
- Follow patterns from existing charts (especially `openhands`)
- Keep conditional logic minimal; prefer explicit consumer-provided values
- Init containers and sidecar patterns should be well-documented

### Chart.yaml
- Dependencies should be conditional where possible
- Version constraints should be explicit

## What NOT to Comment On

- Minor style preferences that don't affect functionality
- Praise for code that follows best practices (just approve)
- Obvious or self-explanatory configuration
- Third-party env var naming (these follow external conventions)

## Communication Style

- Be direct and concise
- Ask questions to understand use cases before suggesting changes
- Suggest alternatives, not mandates
- Approve quickly when charts follow established patterns
