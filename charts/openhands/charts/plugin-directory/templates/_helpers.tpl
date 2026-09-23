{{/*
Expand the name of the chart.
*/}}
{{- define "plugin-directory.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "plugin-directory.fullname" -}}
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
{{- define "plugin-directory.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "plugin-directory.labels" -}}
helm.sh/chart: {{ include "plugin-directory.chart" . }}
{{ include "plugin-directory.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: openhands
{{- end }}

{{/*
Selector labels
*/}}
{{- define "plugin-directory.selectorLabels" -}}
app.kubernetes.io/name: {{ include "plugin-directory.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Pod affinity. Renders the affinity map as YAML, or nothing when no affinity is
configured, so callers can wrap the include in a `with` and omit the key
entirely. A chart-level `affinity` value wins over the umbrella-wide
`global.scheduling.affinity`.
*/}}
{{- define "plugin-directory.affinity" -}}
{{- $affinity := .Values.affinity | default (dig "scheduling" "affinity" (dict) (.Values.global | default (dict))) -}}
{{- with $affinity -}}
{{- toYaml . -}}
{{- end -}}
{{- end -}}
