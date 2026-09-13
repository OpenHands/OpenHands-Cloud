# ClickHouse diagnostic log retention

OpenHands Enterprise can deploy Laminar with an embedded ClickHouse instance.
ClickHouse writes internal diagnostics to `system.*_log` MergeTree tables. These
tables are useful for recent support investigations, but they are not Laminar
application data and can grow quickly on busy installations.

The OpenHands chart applies a bounded retention policy to high-volume ClickHouse
diagnostic tables when Laminar analytics is enabled.

## Default retention

Replicated/KOTS installs expose **Analytics Configuration → ClickHouse
Diagnostic Log Retention** with these supported values:

- `1 day`
- `2 days`
- `3 days`

The default is `3 days`.

The setting is rendered into Helm values and enforced by a Helm
post-install/post-upgrade Job. Because KOTS owns the setting and the Job is part
of the chart, the retention policy is reapplied after KOTS reconciliation and
upgrades.

The Job applies TTLs to these ClickHouse system tables when they exist:

- `system.asynchronous_metric_log`
- `system.metric_log`
- `system.query_log`
- `system.query_thread_log`
- `system.query_views_log`
- `system.part_log`
- `system.session_log`
- `system.text_log`
- `system.trace_log`
- `system.crash_log`
- `system.opentelemetry_span_log`
- `system.zookeeper_log`
- `system.blob_storage_log`
- `system.processors_profile_log`

## Cleaning up an already oversized table

TTL changes do not immediately reclaim all disk from an already oversized
ClickHouse system log. ClickHouse removes expired rows during background merges.
If a node is already under disk pressure, an administrator can safely truncate
diagnostic-only system log tables.

This removes ClickHouse diagnostics, not Laminar application data:

```bash
kubectl -n openhands exec statefulset/laminar-clickhouse -c clickhouse -- \
  bash -ec 'clickhouse-client --user "${CLICKHOUSE_USER:-default}" --password="${CLICKHOUSE_PASSWORD:-}" --query "SYSTEM FLUSH LOGS"'

kubectl -n openhands exec statefulset/laminar-clickhouse -c clickhouse -- \
  bash -ec 'clickhouse-client --user "${CLICKHOUSE_USER:-default}" --password="${CLICKHOUSE_PASSWORD:-}" --query "TRUNCATE TABLE system.trace_log"'
```

To clear all managed diagnostic logs, run:

```bash
for table in \
  asynchronous_metric_log metric_log query_log query_thread_log query_views_log \
  part_log session_log text_log trace_log crash_log opentelemetry_span_log \
  zookeeper_log blob_storage_log processors_profile_log
do
  kubectl -n openhands exec statefulset/laminar-clickhouse -c clickhouse -- \
    bash -ec 'clickhouse-client --user "${CLICKHOUSE_USER:-default}" --password="${CLICKHOUSE_PASSWORD:-}" --query "$1"' -- \
    "TRUNCATE TABLE IF EXISTS system.${table}"
done
```

Use the namespace where OpenHands is installed if it is not `openhands`.

## Support bundles

Support bundles collect recent Laminar ClickHouse pod logs, diagnostic table
sizes, TTL metadata, and a small sample of recent `trace_log` and `text_log`
rows. This preserves enough recent ClickHouse diagnostics for support while the
database retention policy prevents unbounded local disk growth.
