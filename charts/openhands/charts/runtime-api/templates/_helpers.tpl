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
