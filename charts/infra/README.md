# `infra` chart

Cluster-wide infrastructure (cert-manager, trust-manager) that the OpenHands
application chart depends on at runtime.

> **This chart exists solely to support Replicated installations.** It is
> not published to OCI and is not intended for standalone use. Operators
> running OpenHands outside Replicated should install cert-manager and
> trust-manager directly, using the upstream charts and whatever
> configuration is appropriate for their cluster.

## Why it exists

Replicated installs ship cert-manager and trust-manager as part of the
OpenHands bundle. Both components are pulled in here so that the Replicated
release can declare them as separately-weighted KOTS HelmChart resources
(`replicated/infra-cert-manager.yaml`, `replicated/infra-trust-manager.yaml`)
and have KOTS install them in dependency order before the openhands
application chart runs.

## Layout

- `Chart.yaml` declares cert-manager + trust-manager as subchart deps and
  pulls in the `crd-check` library chart for the pre-install CRD wait hook.
- `values.yaml` defines the default values for both subcharts plus the
  shared `crdCheck` block.
- `templates/crd-check-hook.yaml` is a one-line `include` of the
  `crd-check.hook` named template; it renders only when
  `crdCheck.enabled: true` and is used by the trust-manager release to wait
  for cert-manager CRDs to reach the `Established` condition before
  applying trust-manager's webhook resources.
- `templates/sysbox-installer.yaml` renders, only when `sysbox.enabled: true`,
  a privileged DaemonSet plus a `sysbox` RuntimeClass. The DaemonSet runs
  `files/install-sysbox.sh` on each host (via `nsenter --target 1`) to install
  Sysbox and register the `sysbox` containerd runtime through a k0s
  containerd drop-in. It targets Embedded Cluster (k0s) and discovers the
  containerd config path at runtime, so it works regardless of the EC
  `--data-dir`. Requires a Debian/Ubuntu host with internet access.

## Releases

Three Replicated HelmChart manifests reference this chart with different
toggles:

| Manifest                              | `cert-manager.enabled` | `trust-manager.enabled` | `crdCheck.enabled` | `sysbox.enabled`              | KOTS weight |
|---------------------------------------|------------------------|-------------------------|--------------------|-------------------------------|-------------|
| `replicated/infra-cert-manager.yaml`  | `true`                 | `false`                 | `false`            | `false`                       | 5           |
| `replicated/infra-trust-manager.yaml` | `false`                | `true`                  | `true`             | `false`                       | 6           |
| `replicated/infra-sysbox.yaml`        | `false`                | `false`                 | `false`            | `true` when Sandbox Isolation = Sysbox | 7           |

The trust-manager release runs the CRD check before applying its own
resources, because `helm install --wait` only waits for pods to become
ready — not for CRD apiserver registration — so a fast follow-on apply
after cert-manager finishes can race.
