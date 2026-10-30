# ClickHouse diagnostic log retention

When Analytics is enabled, OpenHands deploys Laminar with its own ClickHouse. ClickHouse writes internal diagnostics to `system.*_log` tables. They help with support investigations but are not Laminar data, and with stock settings they grow without limit: `trace_log` alone can reach tens of gigabytes on a single node.

Laminar's Helm chart only mounts its system log configuration when S3 storage is enabled, which Embedded Cluster installs don't use (lmnr-ai/lmnr#2176). Until that changes, OpenHands bounds these tables with a CronJob instead of ClickHouse configuration.

## What the CronJob does

`<release>-laminar-clickhouse-system-log-cleanup` runs every hour at minute 7 and:

1. Drops every `system.<name>_log_<N>` table. ClickHouse leaves these copies, with all their data, when a system log table no longer matches its configuration, for example after a ClickHouse upgrade or the `MODIFY TTL` hook earlier releases ran.
2. Truncates every other `system.*_log` table except `query_log` and `error_log`.
3. Deletes `query_log` and `error_log` rows older than the retention setting.

ClickHouse keeps writing diagnostics between runs, so these tables hold at most about an hour of data. Laminar's own databases are never touched.

## Settings

Replicated/KOTS installs set the retention under **Analytics Configuration → ClickHouse Diagnostic Log Retention** (1 to 3 days, default 3).

Helm installs use these values:

| Value | Default | Meaning |
| -- | -- | -- |
| `laminar.clickhouse.diagnostics.retentionDays` | `3` | Days of `query_log` and `error_log` kept |
| `laminar.clickhouse.diagnostics.cleanup.enabled` | `true` | Set `false` to remove the CronJob |
| `laminar.clickhouse.diagnostics.cleanup.schedule` | `7 * * * *` | Cron schedule |

## Running the cleanup now

To reclaim disk immediately, for example on a node that is already full:

```bash
kubectl -n openhands create job --from=cronjob/openhands-laminar-clickhouse-system-log-cleanup clickhouse-log-cleanup-now
kubectl -n openhands logs -f job/clickhouse-log-cleanup-now
```

Use the namespace and release name OpenHands is installed with if they differ.
