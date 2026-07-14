# Helm chart testing with KinD

The OpenHands chart's native Helm test can be reproduced locally with the same
runner used by GitHub Actions. The runner creates a dedicated KinD cluster,
performs a fresh `helm install`, verifies the selected storage profile, and runs
the filtered `helm test` hook twice.

The values and secrets under this directory are deterministic CI fixtures. They
are not suitable for a production installation.

## Prerequisites

- Docker Desktop, OrbStack, or Docker Engine with enough memory and disk for the
  OpenHands dependencies (8 GB or more is recommended)
- Helm `v3.21.3`
- KinD `v0.32.0`
- kubectl `v1.36.1`

Those are the versions pinned in CI. The runner prints the detected versions and
stops on a mismatch. To investigate an intentional version difference, pass
`--allow-version-skew`; results from that run are not an exact CI reproduction.

CI runs on Linux/amd64. The pinned KinD node image is multi-architecture, but
container image architecture can still be relevant when debugging on Apple
Silicon.

## Run the CI flow locally

Run the ephemeral profile:

```bash
./ci/run-kind-helm-tests.sh run ephemeral
```

Run the persistent profile, including StorageClass and PVC checks:

```bash
./ci/run-kind-helm-tests.sh run persistent
```

The cluster is deliberately preserved after both successful and failed runs.
The runner prints copy-and-paste commands for inspecting the release and stores
logs under `build/kind-tests/<cluster>/`, which is ignored by Git.

It also writes a dedicated kubeconfig there and exports it only within the
runner process. It never changes or uses your active kubectl context for Helm or
Kubernetes operations.

## Debug an existing run

Rerun only the native Helm test:

```bash
./ci/run-kind-helm-tests.sh test ephemeral
```

Collect a fresh diagnostic bundle:

```bash
./ci/run-kind-helm-tests.sh diagnostics ephemeral
```

Repeat the complete flow against the retained cluster. This deletes and
recreates only the dedicated `openhands` namespace before performing another
fresh `helm install`:

```bash
./ci/run-kind-helm-tests.sh run ephemeral --reuse-cluster
```

Delete the cluster explicitly when finished:

```bash
./ci/run-kind-helm-tests.sh delete ephemeral
```

## Commands and overrides

The first argument can be `run`, `create`, `install`, `test`, `diagnostics`, or
`delete`. The second argument selects `ephemeral` or `persistent`.

The local defaults are intentionally isolated:

- cluster: `openhands-local-<profile>`
- release: `openhands`
- namespace: `openhands`
- artifacts and kubeconfig: `build/kind-tests/<cluster>/`

The CI-compatible environment variables `KIND_CLUSTER`, `KIND_NODE_IMAGE`,
`RELEASE`, `NAMESPACE`, `CHART`, `ARTIFACT_DIR`, `KUBECONFIG_PATH`, and
`HELM_TEST_RUNS` can be overridden when a debugging scenario requires it.
`--cluster NAME` overrides the cluster name for one invocation.

Run `./ci/run-kind-helm-tests.sh --help` for the complete command summary.
