{{/*
Expand the name of the chart.
*/}}
{{- define "openhands.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "openhands.fullname" -}}
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
{{- define "openhands.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "openhands.labels" -}}
helm.sh/chart: {{ include "openhands.chart" . }}
{{ include "openhands.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: openhands
{{- end }}

{{/*
Selector labels
*/}}
{{- define "openhands.selectorLabels" -}}
app.kubernetes.io/name: {{ include "openhands.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Pod affinity. Renders the affinity map as YAML, or nothing when no affinity is
configured, so callers can wrap the include in a `with` and omit the key
entirely. A chart-level `affinity` value wins over the umbrella-wide
`global.scheduling.affinity`.
*/}}
{{- define "openhands.affinity" -}}
{{- $affinity := .Values.affinity | default (dig "scheduling" "affinity" (dict) (.Values.global | default (dict))) -}}
{{- with $affinity -}}
{{- toYaml . -}}
{{- end -}}
{{- end -}}

{{/*
Namespace runtime-api creates sandbox pods in. Mirrors the K8S_NAMESPACE
resolution in the runtime-api subchart: its env.K8S_NAMESPACE override, then
sandbox_namespace, then the release namespace.
*/}}
{{- define "openhands.sandboxNamespace" -}}
{{- $runtimeApi := index .Values "runtime-api" | default dict -}}
{{- $env := $runtimeApi.env | default dict -}}
{{- if hasKey $env "K8S_NAMESPACE" -}}
{{- $env.K8S_NAMESPACE -}}
{{- else -}}
{{- $runtimeApi.sandbox_namespace | default .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{/*
One sandboxNetworkPolicy egress rule (dns or ingressController). Takes a dict
with `name` (for error messages) and `rule`. An empty `to` would allow every
destination on the rule's ports, so it fails the render instead.
*/}}
{{- define "openhands.sandboxNetworkPolicy.rule" -}}
{{- if not .rule.to -}}
{{- fail (printf "sandboxNetworkPolicy.%s.to is empty, which would allow every destination. List the destinations, or set sandboxNetworkPolicy.%s.enabled to false." .name .name) -}}
{{- end -}}
- to:
    {{- toYaml .rule.to | nindent 4 }}
  {{- with .rule.ports }}
  ports:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end -}}
