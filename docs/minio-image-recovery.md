# MinIO image distribution

The Replicated configuration uses OpenHands-hosted copies of the existing MinIO
server and client releases. This changes image distribution, not the server
version, storage format, bucket policy, credentials, or PVC. The client image is
also required by the chart's post-install/post-upgrade bucket and service-account
job. Both images must be available before packaging a release.

These recovered artifacts contain **linux/amd64 only**, matching Embedded Cluster
v2's [x86-64 requirement](https://docs.replicated.com/vendor/environment-setup).
They are not multi-platform replacements. Direct Helm chart defaults are unchanged.
Do not roll this configuration out to ARM-based KOTS installations.

| Component | Original release | Original linux/amd64 manifest |
| --- | --- | --- |
| `ohe-minio` | `RELEASE.2023-05-18T00-05-36Z` | `sha256:52c9c477179216d0418c95e8aad047db6d406fa475b7d624b5ba990fe7099279` |
| `ohe-minio-mc` | `RELEASE.2023-05-18T16-59-00Z` | `sha256:9e46d9ed12fa66361f6482b0081d65c2d68e01cbbdede8b964950f76e5a35701` |

The source multi-platform indexes are respectively
`sha256:d03ab478cd6652a3dd7f043395b969d8fa58eac8b83ba052405869229e62f2a8` and
`sha256:9b41483ac6e28f7b9997796e6a8bfefd2e1a83e6c5335f3bbf131d8f7dcb08fd`.
The recovered manifests, configs, and compressed layers retain their original
SHA-256 digests. Do not publish the source indexes without all their platform blobs.

Original source and license information:
[server release](https://github.com/minio/minio/tree/RELEASE.2023-05-18T00-05-36Z),
[client release](https://github.com/minio/mc/tree/RELEASE.2023-05-18T16-59-00Z).
Retain the original image contents and license files when copying the artifacts.

## Publishing the recovered images

Use verified single-platform OCI layouts named `ohe-minio` and `ohe-minio-mc`.
Authenticate to GHCR with a credential authorized to write the OpenHands packages;
pass credentials through stdin or a protected auth file, not command arguments.

```sh
skopeo copy --preserve-digests \
  oci:ohe-minio \
  docker://ghcr.io/openhands/ohe-minio:RELEASE.2023-05-18T00-05-36Z-amd64
skopeo copy --preserve-digests \
  oci:ohe-minio-mc \
  docker://ghcr.io/openhands/ohe-minio-mc:RELEASE.2023-05-18T16-59-00Z-amd64
```

Verify the destination manifest digest and every referenced blob against the
source. Grant the existing Replicated GHCR integration pull access to both
packages, then verify downloads through the licensed Replicated proxy. Receiving
a registry token alone does not prove permission to push or pull the images.

## Release acceptance

- Build the air-gap image list and verify both mirrored images are included.
- Install on an empty image cache without importing the recovered archive.
  Confirm MinIO and its setup job pull successfully and KOTS reaches Ready.
- Upgrade an existing install. Verify the PVC UID and size stay unchanged and
  existing objects remain readable after the MinIO pod restarts.
- Start an authenticated OpenRouter conversation and reload its successful reply.

A successful install after manually importing cached images proves the storage
behavior only. It does not satisfy the image-download acceptance check.
