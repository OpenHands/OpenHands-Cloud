{{/*
Expand the name of the chart.
*/}}
{{- define "runtime-api.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "runtime-api.cronjobName" -}}
{{- $suffix := printf "-%s" .suffix -}}
{{- $maxLen := int (sub 52 (len $suffix)) -}}
{{- $fullname := include "runtime-api.fullname" .root -}}
{{- printf "%s%s" ($fullname | trunc $maxLen | trimSuffix "-") $suffix -}}
{{- end -}}

{{- define "runtime-api.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "runtime-api.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "runtime-api.labels" -}}
helm.sh/chart: {{ include "runtime-api.chart" . }}
{{ include "runtime-api.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: openhands
{{- end }}

{{/*
Selector labels
*/}}
{{- define "runtime-api.selectorLabels" -}}
app.kubernetes.io/name: {{ include "runtime-api.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
PostgreSQL host
*/}}
{{- define "runtime-api.postgresql.host" -}}
{{- if .Values.postgresql.enabled }}
{{- printf "%s-%s" .Release.Name "postgresql" | trunc 63 | trimSuffix "-" -}}
{{- else }}
{{- .Values.database.host -}}
{{- end }}
{{- end -}}

{{/*
PostgreSQL username
*/}}
{{- define "runtime-api.postgresql.username" -}}
{{- if .Values.postgresql.enabled }}
{{- .Values.postgresql.auth.username -}}
{{- else }}
{{- .Values.database.user -}}
{{- end }}
{{- end -}}

{{/*
PostgreSQL database
*/}}
{{- define "runtime-api.postgresql.database" -}}
{{- if .Values.postgresql.enabled }}
{{- .Values.postgresql.auth.database -}}
{{- else }}
{{- .Values.database.name -}}
{{- end }}
{{- end -}}

{{/*
PostgreSQL secret name
*/}}
{{- define "runtime-api.postgresql.secretName" -}}
{{- if .Values.postgresql.enabled }}
{{- printf "%s-%s" .Release.Name "postgresql" -}}
{{- else }}
{{- include "runtime-api.fullname" . -}}
{{- end }}
{{- end -}}

{{/*
PostgreSQL secret key
*/}}
{{- define "runtime-api.postgresql.secretKey" -}}
{{- if .Values.postgresql.enabled }}
{{- printf "postgres-password" -}}
{{- else }}
{{- printf "db-password" -}}
{{- end }}
{{- end -}}

{{/*
Pod affinity. Renders the affinity map as YAML, or nothing when no affinity is
configured, so callers can wrap the include in a `with` and omit the key
entirely. A chart-level `affinity` value wins over the umbrella-wide
`global.scheduling.affinity`.
*/}}
{{- define "runtime-api.affinity" -}}
{{- $affinity := .Values.affinity | default (dig "scheduling" "affinity" (dict) (.Values.global | default (dict))) -}}
{{- with $affinity -}}
{{- toYaml . -}}
{{- end -}}
{{- end -}}

{{/*
Identity of an S3 credential source, for comparing two of them: empty when it
sets none (ambient identity). Takes an s3 values block.
*/}}
{{- define "runtime-api.s3CredentialsKey" -}}
{{- if .existingSecret -}}
secret/{{ .existingSecret }}/{{ .accessKeyIdKey | default "AWS_ACCESS_KEY_ID" }}/{{ .secretAccessKeyKey | default "AWS_SECRET_ACCESS_KEY" }}
{{- else if .accessKeyId -}}
inline/{{ .accessKeyId }}/{{ .secretAccessKey }}
{{- end -}}
{{- end }}

{{/*
Credential identity the runtimeFileArchive block puts in AWS_ACCESS_KEY_ID /
AWS_SECRET_ACCESS_KEY: empty unless the archive is enabled for s3.
*/}}
{{- define "runtime-api.archiveS3CredentialsKey" -}}
{{- if and .Values.runtimeFileArchive.enabled (eq (.Values.runtimeFileArchive.storeType | default "google_cloud") "s3") -}}
{{- include "runtime-api.s3CredentialsKey" .Values.runtimeFileArchive.s3 -}}
{{- end -}}
{{- end }}
