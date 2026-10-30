# ClickHouse diagnostic log retention

When Analytics is enabled, OpenHands deploys Laminar with its own ClickHouse. ClickHouse writes internal diagnostics to `system.*_log` tables. They help with support investigations but are not Laminar data, and with stock settings they grow without limit: `trace_log` alone can reach tens of gigabytes on a single node.

Laminar's Helm chart only mounts its system log configuration when S3 storage is enabled, which Replicated/KOTS installs don't use (lmnr-ai/lmnr#2176). OpenHands therefore bounds these tables from outside ClickHouse, in one of two modes.

## Default: TTL hook

A Helm post-install/post-upgrade Job applies a `retentionDays` TTL to the high-volume system log tables.

ClickHouse does not keep that TTL across a restart. On its next restart it renames each altered table to `<name>_<N>` with all its data, creates a fresh table without the TTL, and never drops the copy. Until the next upgrade reapplies the TTL, the fresh tables grow without limit, and each restart leaves another set of copies on disk.

## Opt-in: hourly cleanup CronJob

Enabling cleanup removes the TTL hook and adds `<release>-laminar-clickhouse-system-log-cleanup`, which runs every hour at minute 7 and:

1. Drops every `system.<name>_log_<N>` copy.
2. Truncates every other `system.*_log` table except `query_log` and `error_log`.
3. Deletes `query_log` and `error_log` rows older than `retentionDays`.

ClickHouse keeps writing diagnostics between runs, so these tables hold at most about an hour of data. Laminar's own databases are never touched. Enable it on any install where ClickHouse diagnostic logs are filling the disk.

## Settings

Replicated/KOTS installs use **Analytics Configuration**:

- **ClickHouse Diagnostic Log Retention**: 1 to 3 days, default 3.
- **Hourly ClickHouse Diagnostic Log Cleanup**: off by default.

Helm installs use these values:

| Value | Default | Meaning |
| -- | -- | -- |
| `laminar.clickhouse.diagnostics.retentionDays` | `3` | TTL days in the default mode; days of `query_log` and `error_log` kept by the CronJob |
| `laminar.clickhouse.diagnostics.cleanup.enabled` | `false` | Replace the TTL hook with the cleanup CronJob |
| `laminar.clickhouse.diagnostics.cleanup.schedule` | `7 * * * *` | CronJob schedule |

## Running the cleanup now

With cleanup enabled, reclaim disk immediately, for example on a node that is already full:

```bash
kubectl -n openhands create job --from=cronjob/openhands-laminar-clickhouse-system-log-cleanup clickhouse-log-cleanup-now
kubectl -n openhands logs -f job/clickhouse-log-cleanup-now
```

Use the namespace and release name OpenHands is installed with if they differ.

## Support bundles

Support bundles collect recent Laminar ClickHouse pod logs, diagnostic table sizes, TTL metadata, and a small sample of recent `trace_log` and `text_log` rows.
