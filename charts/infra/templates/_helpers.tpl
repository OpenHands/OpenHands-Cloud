{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "infra.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.

Unlike the other charts, this one does not emit app.kubernetes.io/name. Each
workload here is an independent cluster-level component rather than a copy of
the chart, and its name doubles as the DaemonSet's spec.selector, which is
immutable. So every resource sets app.kubernetes.io/name itself and this helper
supplies the rest.
*/}}
{{- define "infra.labels" -}}
helm.sh/chart: {{ include "infra.chart" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: openhands
{{- end }}
