{{/* Prevent an online upgrade from silently repointing existing automation
records or uploaded packages. A deliberate migration is a separate operation. */}}
{{- define "openhands.automationUpgradeGuard" -}}
{{- if .Release.IsUpgrade -}}
{{- $existing := lookup "apps/v1" "Deployment" .Release.Namespace "automation" -}}
{{- if $existing -}}
{{- $keys := list "FILE_STORE" "AWS_S3_BUCKET" "GCS_BUCKET_NAME" "AWS_S3_ENDPOINT" "AUTOMATION_DB_HOST" "AUTOMATION_DB_PORT" "AUTOMATION_DB_NAME" "AUTOMATION_DB_USER" -}}
{{- $old := dict -}}{{- $new := dict -}}
{{- range $existing.spec.template.spec.containers -}}
{{- if eq .name "automation" -}}
{{- range .env -}}{{- if has .name $keys -}}{{- $_ := set $old .name (.value | default "") -}}{{- end -}}{{- end -}}
{{- end -}}{{- end -}}
{{- range (include "automation.env" (index .Subcharts "automation") | fromYamlArray) -}}
{{- if has .name $keys -}}{{- $_ := set $new .name (.value | default "") -}}{{- end -}}
{{- end -}}
{{- if not (deepEqual $old $new) -}}
{{- fail "Refusing to repoint the existing Automations database or package store during upgrade. Retain the deployed automation.database/filestore (including effective env overrides), or complete a separately planned data migration. automation.upgradeAcknowledged does not bypass this guard." -}}
{{- end -}}
{{- end -}}
{{/* Enabling persistence on the shared ephemeral PostgreSQL changes immutable
StatefulSet storage and does not migrate its data. Catch it before Helm patches. */}}
{{- if .Values.postgresql.enabled -}}
{{- $pg := lookup "apps/v1" "StatefulSet" .Release.Namespace (include "postgresql.v1.primary.fullname" (index .Subcharts "postgresql")) -}}
{{- if and $pg .Values.postgresql.primary.persistence.enabled (not $pg.spec.volumeClaimTemplates) -}}
{{- fail "Bundled PostgreSQL is currently ephemeral. Enabling persistence changes immutable StatefulSet storage and does not migrate data. Back up and migrate the database separately before enabling Automations." -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
