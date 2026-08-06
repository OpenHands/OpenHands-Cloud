{{/*
Bundled object-store backend selection.

RustFS is the only bundled backend. It is deployed when rustfs.enabled, and the
helpers render nothing when it is not: that install uses an external store
(S3/GCS) or none.

This was a two-backend selector while MinIO was still deployed alongside RustFS
as a rollback source. Both stores are gone from the chart now, so precedence and
the migration switch that flipped it are gone with them. An install still on
MinIO must reach the release that migrated its data before this one, which the
migration release being promoted as required enforces.
*/}}

{{/* "rustfs", or "" when no bundled store is deployed. */}}
{{- define "openhands.objectStore.backend" -}}
{{- if .Values.rustfs.enabled -}}
rustfs
{{- end -}}
{{- end -}}

{{/* In-cluster endpoint of the bundled store. */}}
{{- define "openhands.objectStore.endpoint" -}}
{{ printf "http://%s-rustfs-svc:9000" .Release.Name }}
{{- end -}}

{{/*
Credentials for the bundled store. These moved from minio.svcaccts[0] when MinIO
was removed. The two were seeded with the same pair and validation rejected a
mismatch, so an install carrying chart defaults sees no change and nothing the
app holds is rotated. An install that overrode the MinIO pair must set the same
values under rustfs.secret.rustfs, which the upgrade note calls out.
*/}}
{{- define "openhands.objectStore.accessKey" -}}
{{ .Values.rustfs.secret.rustfs.access_key }}
{{- end -}}

{{- define "openhands.objectStore.secretKey" -}}
{{ .Values.rustfs.secret.rustfs.secret_key }}
{{- end -}}
