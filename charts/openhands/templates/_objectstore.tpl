{{/*
Bundled object-store backend selection.

Two bundled backends, in this precedence order:

  rustfs   the rustfs subchart (rustfs.enabled)
  minio    the bundled MinIO subchart (minio.enabled, defaulting to
           filestore.ephemeral)

RustFS wins over MinIO when both are enabled, which is the state a migration
runs in: MinIO stays deployed so its objects survive for rollback while every
consumer is already pointed at RustFS.

Neither backend is bundled when filestore.ephemeral is false — that install
uses an external store (S3/GCS) or none, and these helpers render nothing.

The endpoint is the only thing that changes between the two backends. Both are
reached over plain HTTP inside the cluster, and both use the same credentials,
so a migration does not rotate anything the app holds.
*/}}

{{/* "rustfs", "minio", or "" when no bundled store is deployed. */}}
{{- define "openhands.objectStore.backend" -}}
{{- if not .Values.filestore.ephemeral -}}
{{- else if .Values.rustfs.enabled -}}
rustfs
{{- else -}}
minio
{{- end -}}
{{- end -}}

{{/* In-cluster endpoint of the active bundled store. */}}
{{- define "openhands.objectStore.endpoint" -}}
{{- if eq (include "openhands.objectStore.backend" .) "rustfs" -}}
{{ printf "http://%s-rustfs-svc:9000" .Release.Name }}
{{- else -}}
{{ printf "http://%s-minio:9000" .Release.Name }}
{{- end -}}
{{- end -}}

{{/*
Credentials for the bundled store. Sourced from minio.svcaccts[0] regardless of
which backend is active: the RustFS values are seeded with the same pair, so the
app's credentials are identical before and after a migration and only the
endpoint moves. Validation rejects a mismatch.
*/}}
{{- define "openhands.objectStore.accessKey" -}}
{{ (index .Values.minio.svcaccts 0).accessKey }}
{{- end -}}

{{- define "openhands.objectStore.secretKey" -}}
{{ (index .Values.minio.svcaccts 0).secretKey }}
{{- end -}}

{{/* True while both backends are deployed, i.e. a migration is in progress. */}}
{{- define "openhands.objectStore.migrating" -}}
{{- if and .Values.filestore.ephemeral .Values.rustfs.enabled (include "openhands.objectStore.minioEnabled" .) -}}
true
{{- end -}}
{{- end -}}

{{/*
Whether the MinIO subchart renders. Mirrors its Chart.yaml condition
("minio.enabled,filestore.ephemeral"): Helm uses the first path that exists, and
minio.enabled is deliberately absent from values.yaml so existing installs keep
deploying MinIO from filestore.ephemeral alone.
*/}}
{{- define "openhands.objectStore.minioEnabled" -}}
{{- if hasKey .Values.minio "enabled" -}}
{{- if .Values.minio.enabled }}true{{ end -}}
{{- else if .Values.filestore.ephemeral -}}
true
{{- end -}}
{{- end -}}
