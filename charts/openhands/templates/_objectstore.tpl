{{/*
Bundled object-store backend selection.

Two bundled backends, in this precedence order:

  minio    the bundled MinIO subchart (minio.enabled)
  rustfs   the rustfs subchart (rustfs.enabled)

MinIO wins when both are enabled, so enabling RustFS by itself deploys it
without repointing the app — the same shape templates/_cache.tpl uses for
redis/valkey, where the incumbent keeps precedence. Deploying RustFS therefore
never moves a consumer onto an empty store; the migration switch (a later
release) is what repoints consumers, once it has copied the data across.

Neither backend is bundled when both are disabled: that install uses an
external store (S3/GCS) or none, and these helpers render nothing.

The endpoint is the only thing that changes between the two backends. Both are
reached over plain HTTP inside the cluster, and both use the same credentials,
so switching backends does not rotate anything the app holds.
*/}}

{{/* "minio", "rustfs", or "" when no bundled store is deployed. */}}
{{- define "openhands.objectStore.backend" -}}
{{- if .Values.minio.enabled -}}
minio
{{- else if .Values.rustfs.enabled -}}
rustfs
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
app's credentials are identical either way and only the endpoint moves.
Validation rejects a mismatch (see templates/validations.yaml).
*/}}
{{- define "openhands.objectStore.accessKey" -}}
{{ (index .Values.minio.svcaccts 0).accessKey }}
{{- end -}}

{{- define "openhands.objectStore.secretKey" -}}
{{ (index .Values.minio.svcaccts 0).secretKey }}
{{- end -}}
