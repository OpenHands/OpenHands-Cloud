# ClickHouse diagnostic log retention

OpenHands Enterprise can deploy Laminar with an embedded ClickHouse instance. ClickHouse writes internal diagnostics to `system.*_log` MergeTree tables. They are not Laminar application data, and with ClickHouse's defaults they grow without bound.

## How retention is configured

Retention is set in ClickHouse's server configuration, which is the only layer that survives a restart. The Laminar chart mounts `system_logs.xml` into `/etc/clickhouse-server/config.d/` (Laminar values `clickhouse.systemLogs.*`). That file:

- disables every high-volume diagnostic table (`trace_log`, `text_log`, `metric_log`, `asynchronous_metric_log`, `part_log`, `processors_profile_log`, `query_thread_log`, `query_views_log`, `session_log`, and others), so ClickHouse never creates or writes them;
- keeps `system.query_log` and `system.error_log`, with a TTL of `clickhouse.systemLogs.retentionDays` days.

Replicated/KOTS installs set that TTL with **Analytics Configuration → ClickHouse Diagnostic Log Retention** (1, 2 or 3 days; default 3).

Do not change retention with `ALTER TABLE system.<table> MODIFY TTL`. On its next start, ClickHouse finds the table no longer matches its config, renames it to `<table>_<N>` with all its data, and creates a fresh table without the TTL. Nothing ever drops the renamed copy.

## Upgrade cleanup

Disabling a table in config stops writes but leaves the existing table and its data on disk. A config change, such as a new retention value, also leaves a renamed `<table>_<N>` copy behind. After every install and upgrade, the OpenHands chart runs a Job, `<release>-laminar-clickhouse-system-log-cleanup`. It drops:

- each `system` table that `system_logs.xml` marks `remove="1"`;
- every `system.*_log_<N>` copy.

The Job reads the table list from the same ConfigMap ClickHouse mounts, so the two cannot disagree. It drops with `max_table_size_to_drop=0`, because diagnostic tables on long-running installs often exceed ClickHouse's 50 GB drop guard. Running it again is a no-op.

## Additional ClickHouse configuration

To add other server settings, mount your own file into `config.d/` with the Laminar values `clickhouse.extraVolumes` and `clickhouse.extraVolumeMounts`. ClickHouse merges `config.d/` files in name order, so a file named after `system_logs.xml` overrides it.

```yaml
laminar:
  clickhouse:
    extraVolumes:
      - name: clickhouse-extra-config
        configMap:
          name: my-clickhouse-config
    extraVolumeMounts:
      - name: clickhouse-extra-config
        mountPath: /etc/clickhouse-server/config.d/zz-extra.xml
        subPath: extra.xml
```

## Support bundles

Support bundles collect the Laminar ClickHouse pod logs, plus the size and TTL of every `system` log table, including any leftover `_<N>` copies. They also include the 50 most recent rows of `system.error_log` and the 50 most recent failed queries from `system.query_log`.
