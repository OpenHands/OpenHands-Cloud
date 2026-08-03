{{/*
PostgreSQL backend selection.

The chart supports three backends, in this precedence order:

  cnpg      a CloudNativePG Cluster managed by this chart (cnpg.enabled)
  bitnami   the bundled Bitnami postgresql subchart (postgresql.enabled)
  external  an operator-supplied server (externalDatabase.*)

CNPG wins over Bitnami when both are enabled, which is the state a migration
runs in: the old server stays deployed so its data survives for rollback while
every consumer is already pointed at the new one.

Consumers reach whichever in-cluster backend is active through a single alias
Service, so subchart values that hardcode the hostname need no per-backend
changes. See templates/service.yaml.
*/}}

{{- define "openhands.postgres.backend" -}}
{{- if .Values.cnpg.enabled -}}
cnpg
{{- else if .Values.postgresql.enabled -}}
bitnami
{{- else -}}
external
{{- end -}}
{{- end -}}

{{/* Name of the CNPG Cluster, and therefore of its <name>-rw/-ro/-r Services. */}}
{{- define "openhands.cnpg.clusterName" -}}
{{- .Values.cnpg.clusterName | default (printf "%s-pg" .Release.Name) -}}
{{- end -}}

{{/* Alias Service every consumer connects to for an in-cluster backend. */}}
{{- define "openhands.postgres.aliasService" -}}
oh-main-postgresql
{{- end -}}

{{- define "openhands.postgres.host" -}}
{{- $backend := include "openhands.postgres.backend" . -}}
{{- if eq $backend "cnpg" -}}
{{- include "openhands.postgres.aliasService" . -}}
{{- else if eq $backend "bitnami" -}}
{{- printf "%s-postgresql" .Release.Name -}}
{{- else -}}
{{- .Values.externalDatabase.host -}}
{{- end -}}
{{- end -}}

{{- define "openhands.postgres.port" -}}
{{- $backend := include "openhands.postgres.backend" . -}}
{{- if eq $backend "cnpg" -}}
5432
{{- else if eq $backend "bitnami" -}}
{{- .Values.postgresql.primary.service.ports.postgresql | default 5432 -}}
{{- else -}}
{{- .Values.externalDatabase.port | default 5432 -}}
{{- end -}}
{{- end -}}

{{/*
Application database user and name. Under CNPG both fall back to the Bitnami
values so that turning cnpg.enabled on carries the existing names over with no
further configuration; set cnpg.username / cnpg.database to override.
*/}}
{{- define "openhands.postgres.username" -}}
{{- $backend := include "openhands.postgres.backend" . -}}
{{- if eq $backend "cnpg" -}}
{{- .Values.cnpg.username | default .Values.postgresql.auth.username -}}
{{- else if eq $backend "bitnami" -}}
{{- .Values.postgresql.auth.username -}}
{{- else -}}
{{- .Values.externalDatabase.username -}}
{{- end -}}
{{- end -}}

{{- define "openhands.postgres.database" -}}
{{- $backend := include "openhands.postgres.backend" . -}}
{{- if eq $backend "cnpg" -}}
{{- .Values.cnpg.database | default .Values.postgresql.auth.database -}}
{{- else if eq $backend "bitnami" -}}
{{- .Values.postgresql.auth.database -}}
{{- else -}}
{{- .Values.externalDatabase.database -}}
{{- end -}}
{{- end -}}

{{/*
Secret holding the application's PostgreSQL password. CNPG names it explicitly
rather than reading it out of a subchart that may be disabled; the Bitnami and
external paths keep sourcing it as they always have.
*/}}
{{- define "openhands.postgres.passwordSecret" -}}
{{- if .Values.cnpg.enabled -}}
{{- .Values.cnpg.credentials.existingSecret -}}
{{- else -}}
{{- .Values.postgresql.auth.existingSecret -}}
{{- end -}}
{{- end -}}

{{/* sslMode for connections. In-cluster backends are unencrypted by default. */}}
{{- define "openhands.postgres.sslMode" -}}
{{- if eq (include "openhands.postgres.backend" .) "external" -}}
{{- .Values.externalDatabase.sslMode | default "prefer" -}}
{{- else -}}
prefer
{{- end -}}
{{- end -}}
