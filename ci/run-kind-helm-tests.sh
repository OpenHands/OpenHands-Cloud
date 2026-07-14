#!/usr/bin/env bash

# Shared KinD + Helm test runner for GitHub Actions and local debugging.
set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

ci_helm_version="v3.21.3"
ci_kind_version="v0.32.0"
ci_kubectl_version="v1.36.1"
default_node_image="kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5"

usage() {
  cat <<'EOF'
Usage:
  ci/run-kind-helm-tests.sh [run|create|install|test|diagnostics|delete] [ephemeral|persistent] [options]

Commands:
  run          Create a fresh release and run the native Helm test twice (default).
  create       Create only the dedicated KinD cluster.
  install      Create the cluster and perform a fresh Helm install without testing.
  test         Rerun only the native Helm test against an existing release.
  diagnostics  Collect Helm, Kubernetes, pod, storage, and KinD diagnostics.
  delete       Delete the dedicated KinD cluster.

Options:
  --cluster NAME        Override the dedicated cluster name.
  --reuse-cluster       Allow run/install to reset the namespace in an existing cluster.
  --allow-version-skew  Warn instead of failing when local tools differ from CI pins.
  -h, --help            Show this help.

The cluster is never deleted automatically. This keeps state available for debugging.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 2
}

warn() {
  echo "WARNING: $*" >&2
}

action="run"
profile="ephemeral"
reuse_cluster="false"
allow_version_skew="${ALLOW_VERSION_SKEW:-0}"
cluster_override=""

if [[ $# -gt 0 ]]; then
  case "$1" in
    run|create|install|test|diagnostics|delete)
      action="$1"
      shift
      ;;
    ephemeral|persistent)
      profile="$1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown command '$1'"
      ;;
  esac
fi

if [[ $# -gt 0 && "$1" != --* ]]; then
  profile="$1"
  shift
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cluster)
      [[ $# -ge 2 ]] || die "--cluster requires a name"
      cluster_override="$2"
      shift 2
      ;;
    --reuse-cluster)
      reuse_cluster="true"
      shift
      ;;
    --allow-version-skew)
      allow_version_skew="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option '$1'"
      ;;
  esac
done

case "$profile" in
  ephemeral|persistent) ;;
  *) die "profile must be 'ephemeral' or 'persistent' (got '$profile')" ;;
esac

release="${RELEASE:-openhands}"
namespace="${NAMESPACE:-openhands}"
chart="${CHART:-$repo_root/charts/openhands}"
kind_cluster="${cluster_override:-${KIND_CLUSTER:-openhands-local-$profile}}"
kind_node_image="${KIND_NODE_IMAGE:-$default_node_image}"
helm_test_runs="${HELM_TEST_RUNS:-2}"

case "$chart" in
  /*) ;;
  *) chart="$repo_root/$chart" ;;
esac

case "$helm_test_runs" in
  ''|*[!0-9]*|0) die "HELM_TEST_RUNS must be a positive integer" ;;
esac

if [[ -n "${ARTIFACT_DIR:-}" ]]; then
  artifact_dir="$ARTIFACT_DIR"
  case "$artifact_dir" in
    /*) ;;
    *) artifact_dir="$repo_root/$artifact_dir" ;;
  esac
else
  artifact_dir="$repo_root/build/kind-tests/$kind_cluster"
fi

mkdir -p "$artifact_dir"
if [[ -n "${KUBECONFIG_PATH:-}" ]]; then
  kubeconfig="$KUBECONFIG_PATH"
elif [[ "${GITHUB_ACTIONS:-}" == "true" && -n "${RUNNER_TEMP:-}" ]]; then
  kubeconfig="$RUNNER_TEMP/$kind_cluster-kubeconfig"
else
  kubeconfig="$artifact_dir/kubeconfig"
fi
case "$kubeconfig" in
  /*) ;;
  *) kubeconfig="$repo_root/$kubeconfig" ;;
esac
mkdir -p "$(dirname "$kubeconfig")"
export KUBECONFIG="$kubeconfig"

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

check_version() {
  local tool="$1"
  local actual="$2"
  local expected="$3"

  if [[ "$actual" == *"$expected"* ]]; then
    return
  fi
  if [[ "$allow_version_skew" == "1" ]]; then
    warn "$tool differs from CI (expected $expected; found: $actual)"
    return
  fi
  die "$tool must match CI version $expected (found: $actual). Re-run with --allow-version-skew only if the difference is intentional."
}

preflight() {
  local helm_version
  local kind_version
  local kubectl_version

  for tool in docker helm kind kubectl; do
    require_command "$tool"
  done

  docker --version
  docker info >/dev/null
  helm_version="$(helm version --short)"
  kind_version="$(kind version)"
  kubectl_version="$(kubectl version --client --output=yaml)"
  echo "$helm_version"
  echo "$kind_version"
  echo "$kubectl_version"

  check_version "Helm" "$helm_version" "$ci_helm_version"
  check_version "KinD" "$kind_version" "$ci_kind_version"
  check_version "kubectl" "$kubectl_version" "$ci_kubectl_version"
}

cluster_exists() {
  local cluster
  while IFS= read -r cluster; do
    [[ "$cluster" == "$kind_cluster" ]] && return 0
  done < <(kind get clusters)
  return 1
}

export_cluster_kubeconfig() {
  kind export kubeconfig --name "$kind_cluster" --kubeconfig "$kubeconfig"
}

create_or_reuse_cluster() {
  if cluster_exists; then
    if [[ "$reuse_cluster" != "true" && "$action" != "test" && "$action" != "diagnostics" ]]; then
      die "KinD cluster '$kind_cluster' already exists. Use --reuse-cluster to reset its '$namespace' namespace, or run '$0 delete $profile'."
    fi
    echo "Reusing KinD cluster $kind_cluster"
  else
    if [[ "$action" == "test" || "$action" == "diagnostics" ]]; then
      die "KinD cluster '$kind_cluster' does not exist; run '$0 run $profile' first"
    fi
    kind create cluster \
      --name "$kind_cluster" \
      --image "$kind_node_image" \
      --wait 120s \
      --kubeconfig "$kubeconfig"
  fi
  export_cluster_kubeconfig
}

prepare_dependencies() {
  helm repo add lmnr https://lmnr-ai.github.io/lmnr-helm
  helm repo add minio https://charts.min.io/
  helm repo add bitnami https://charts.bitnami.com/bitnami
  helm dependency build "$chart"
}

reset_namespace() {
  kubectl delete namespace "$namespace" \
    --ignore-not-found \
    --wait=true \
    --timeout=5m
  kubectl create namespace "$namespace" --dry-run=client -o yaml |
    kubectl apply -f -
  bash "$script_dir/create-kind-secrets.sh" "$namespace"
}

install_release() {
  local profile_values="$script_dir/kind-profiles/$profile.yaml"

  : >"$artifact_dir/helm-install.log"
  helm install "$release" "$chart" \
    --namespace "$namespace" \
    --values "$script_dir/kind-values.yaml" \
    --values "$profile_values" \
    --wait \
    --wait-for-jobs \
    --timeout 25m \
    2>&1 | tee "$artifact_dir/helm-install.log"
}

verify_persistent_storage() {
  local pvc

  if [[ "$profile" != "persistent" ]]; then
    return 0
  fi
  kubectl get storageclass standard
  for pvc in \
    "openhands-minio" \
    "data-openhands-postgresql-0" \
    "redis-data-openhands-redis-master-0"
  do
    kubectl wait \
      --namespace "$namespace" \
      --for=jsonpath='{.status.phase}'=Bound \
      "pvc/$pvc" \
      --timeout=5m
  done
}

run_native_tests() {
  local test_run

  touch "$artifact_dir/helm-test.log"
  for ((test_run = 1; test_run <= helm_test_runs; test_run++)); do
    echo "Helm test run ${test_run}/${helm_test_runs} for profile ${profile}"
    helm test "$release" \
      --namespace "$namespace" \
      --filter "name=${release}-test-connection" \
      --logs \
      --timeout 10m \
      2>&1 | tee -a "$artifact_dir/helm-test.log"
  done
}

collect_diagnostics() {
  local pod
  local name

  mkdir -p "$artifact_dir"
  helm status "$release" --namespace "$namespace" >"$artifact_dir/helm-status.txt" 2>&1 || true
  helm get hooks "$release" --namespace "$namespace" >"$artifact_dir/helm-hooks.yaml" 2>&1 || true
  helm get manifest "$release" --namespace "$namespace" >"$artifact_dir/helm-manifest.yaml" 2>&1 || true
  helm get values "$release" --namespace "$namespace" --all >"$artifact_dir/helm-values.yaml" 2>&1 || true
  kubectl get nodes -o wide >"$artifact_dir/nodes.txt" 2>&1 || true
  kubectl get pods,deployments,statefulsets,jobs -n "$namespace" -o wide >"$artifact_dir/workloads.txt" 2>&1 || true
  kubectl get pvc -n "$namespace" -o wide >"$artifact_dir/pvcs.txt" 2>&1 || true
  kubectl get pv -o wide >"$artifact_dir/pvs.txt" 2>&1 || true
  kubectl get storageclass -o wide >"$artifact_dir/storageclasses.txt" 2>&1 || true
  kubectl get events -n "$namespace" --sort-by=.lastTimestamp >"$artifact_dir/events.txt" 2>&1 || true
  kubectl describe pods -n "$namespace" >"$artifact_dir/pod-descriptions.txt" 2>&1 || true
  kubectl describe pvc -n "$namespace" >"$artifact_dir/pvc-descriptions.txt" 2>&1 || true

  while IFS= read -r pod; do
    [[ -n "$pod" ]] || continue
    name="${pod#pod/}"
    kubectl logs -n "$namespace" "$pod" --all-containers --prefix >"$artifact_dir/${name}.log" 2>&1 || true
    kubectl logs -n "$namespace" "$pod" --all-containers --prefix --previous >"$artifact_dir/${name}-previous.log" 2>&1 || true
  done < <(kubectl get pods -n "$namespace" -o name 2>/dev/null || true)

  kind export logs "$artifact_dir/kind" --name "$kind_cluster" || true
  df -h >"$artifact_dir/disk.txt" 2>&1 || true
  docker system df >"$artifact_dir/docker-disk.txt" 2>&1 || true
}

print_debug_commands() {
  local stream="${1:-1}"
  local version_skew_flag=""
  if [[ "$allow_version_skew" == "1" ]]; then
    version_skew_flag=" --allow-version-skew"
  fi
  {
    echo
    if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
      echo "Cluster remains available until the GitHub Actions job finishes: $kind_cluster"
    else
      echo "Cluster preserved for debugging: $kind_cluster"
    fi
    printf 'export KUBECONFIG=%q\n' "$kubeconfig"
    printf 'kubectl get pods -n %q\n' "$namespace"
    printf 'helm status %q -n %q\n' "$release" "$namespace"
    printf '%q test %q --cluster %q%s\n' "$0" "$profile" "$kind_cluster" "$version_skew_flag"
    printf '%q diagnostics %q --cluster %q%s\n' "$0" "$profile" "$kind_cluster" "$version_skew_flag"
    printf 'kind delete cluster --name %q\n' "$kind_cluster"
    echo "Diagnostics: $artifact_dir"
  } >&"$stream"
}

diagnostics_ready="false"
on_error() {
  local status=$?
  trap - ERR
  set +e
  if [[ "$diagnostics_ready" == "true" ]]; then
    echo "Collecting failure diagnostics in $artifact_dir" >&2
    collect_diagnostics
    print_debug_commands 2
  fi
  exit "$status"
}
trap on_error ERR

if [[ "$action" == "delete" ]]; then
  require_command kind
  if cluster_exists; then
    kind delete cluster --name "$kind_cluster"
  else
    echo "KinD cluster $kind_cluster does not exist"
  fi
  exit 0
fi

preflight

create_or_reuse_cluster
diagnostics_ready="true"

if [[ "$action" == "run" || "$action" == "install" ]]; then
  prepare_dependencies
fi

case "$action" in
  create)
    ;;
  run|install)
    reset_namespace
    install_release
    verify_persistent_storage
    if [[ "$action" == "run" ]]; then
      run_native_tests
    fi
    ;;
  test)
    run_native_tests
    ;;
  diagnostics)
    collect_diagnostics
    ;;
esac

print_debug_commands
