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
- `templates/ecr-credential-provider-installer.yaml` renders, only when
  `ecrCredentialProvider.enabled: true`, a privileged DaemonSet that runs
  `files/install-ecr-credential-provider.sh` on each host (via
  `nsenter --target 1`). The installer puts the kubelet ECR credential provider
  plugin on the node and points the kubelet at it, so pulls from Amazon ECR
  authenticate with the node's EC2 instance profile instead of a pull secret
  holding a token that expires every 12 hours. Requires EC2 nodes and host
  egress to the plugin's release host; see below for what it does to the node.

## The ECR credential provider changes the node

Everything else in this chart installs Kubernetes objects. This one edits the
host, because the two settings it needs — `--image-credential-provider-config`
and `--image-credential-provider-bin-dir` — are kubelet **command-line flags**.
They are not fields of `KubeletConfiguration`, so k0s `workerProfiles` cannot
express them, and Embedded Cluster's `unsupportedOverrides.k0s` only patches the
k0s `ClusterConfig`. Embedded Cluster hardcodes `--kubelet-extra-args` when it
runs `k0s install`, and the k0s systemd unit bakes its arguments straight into
`ExecStart=` with no `EnvironmentFile`, so there is no drop-in to hook either.

That leaves editing the unit. The installer rewrites the `--kubelet-extra-args`
value in `k0scontroller.service` (or `k0sworker.service`), keeps the original at
`<installDir>/<unit>.service.orig`, and restarts k0s. The restart is required:
k0s builds kubelet's argument list once at startup and its supervisor re-execs
kubelet from that stored list, so killing kubelet alone changes nothing.

Consequences worth knowing before enabling it:

- The node's k0s components bounce once, which on a controller briefly takes the
  API server with them. Running containers survive, because containerd's shims
  outlive containerd.
- Only the first enable pays that cost. The installer compares desired against
  actual and restarts nothing when the flags are already in place.
- A node joining later gets a fresh unit from `k0s install`, so its own
  DaemonSet pod repeats the edit and the restart on that node alone.
- Embedded Cluster upgrades are safe: they roll k0s through autopilot binary
  swaps rather than re-running `k0s install`, so the edited unit survives.
- The plugin is downloaded on the host, checksum-pinned, from
  `ecrCredentialProvider.releaseBaseUrl`. Air-gapped installs need that URL
  repointed at an internal mirror serving the same layout.
- Turning the option back off removes the DaemonSet but leaves the node as it
  is, which keeps ECR pulls working. To undo the host change, restore
  `<installDir>/<unit>.service.orig` over the unit, `systemctl daemon-reload`,
  restart k0s, and delete `<installDir>`.

## Releases

Four Replicated HelmChart manifests reference this chart with different
toggles:

| Manifest                                        | `cert-manager.enabled` | `trust-manager.enabled` | `crdCheck.enabled` | Other                                            | KOTS weight |
|-------------------------------------------------|------------------------|-------------------------|--------------------|--------------------------------------------------|-------------|
| `replicated/infra-cert-manager.yaml`            | `true`                 | `false`                 | `false`            | `sandboxUpgradeCoordinator.enabled: true`        | 5           |
| `replicated/infra-trust-manager.yaml`           | `false`                | `true`                  | `true`             | —                                                | 6           |
| `replicated/infra-sysbox.yaml`                  | `false`                | `false`                 | `false`            | `sysbox` on when Sandbox Isolation = Sysbox      | 7           |
| `replicated/infra-ecr-credential-provider.yaml` | `false`                | `false`                 | `false`            | `ecrCredentialProvider` on when ECR node IAM is enabled | 20          |

The trust-manager release runs the CRD check before applying its own
resources, because `helm install --wait` only waits for pods to become
ready — not for CRD apiserver registration — so a fast follow-on apply
after cert-manager finishes can race.

The ECR release is weighted last and deliberately does not pass `--wait`. Its
first deploy restarts k0s, so the restart has to land after KOTS has applied
every other release rather than while Helm is waiting on one. Nothing depends on
it being early: the credential provider is only consulted when a pod pulls from
ECR, and a pull that races the restart backs off and retries.
