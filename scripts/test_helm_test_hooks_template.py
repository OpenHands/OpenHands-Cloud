"""Behavioral checks for the native OpenHands Helm smoke test."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
OPENHANDS_CHART = REPO_ROOT / "charts" / "openhands"
KIND_VALUES = REPO_ROOT / "ci" / "kind-values.yaml"
KIND_PROFILE_VALUES = {
    "ephemeral": REPO_ROOT / "ci" / "kind-profiles" / "ephemeral.yaml",
    "persistent": REPO_ROOT / "ci" / "kind-profiles" / "persistent.yaml",
}
KIND_SECRETS_SCRIPT = REPO_ROOT / "ci" / "create-kind-secrets.sh"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"
HELM_TEST_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "helm-chart-tests.yml"
SCRIPT_TESTS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test-scripts.yml"


def run_chart_render(
    *,
    release: str = "openhands",
    set_values: tuple[str, ...] = (),
    values_files: tuple[Path, ...] = (),
) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", release, str(OPENHANDS_CHART)]
    for values_file in values_files:
        command.extend(["--values", str(values_file)])
    for value in set_values:
        command.extend(["--set", value])

    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def render_chart(
    *,
    release: str = "openhands",
    set_values: tuple[str, ...] = (),
    values_files: tuple[Path, ...] = (),
) -> list[dict[str, Any]]:
    result = run_chart_render(
        release=release,
        set_values=set_values,
        values_files=values_files,
    )
    result.check_returncode()
    return [
        document
        for document in yaml.safe_load_all(result.stdout)
        if isinstance(document, dict)
    ]


def parent_test_pods(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        document
        for document in documents
        if document.get("kind") == "Pod"
        and document.get("metadata", {}).get("labels", {}).get("app")
        == "openhands-tests"
        and document.get("metadata", {}).get("annotations", {}).get("helm.sh/hook")
        == "test"
    ]


def render_kind_profile(profile: str) -> list[dict[str, Any]]:
    return render_chart(
        release="openhands",
        values_files=(KIND_VALUES, KIND_PROFILE_VALUES[profile]),
    )


def resource_identities(
    documents: list[dict[str, Any]],
) -> set[tuple[str, str, str]]:
    return {
        (
            document.get("apiVersion", ""),
            document.get("kind", ""),
            document.get("metadata", {}).get("name", ""),
        )
        for document in documents
    }


def test_default_render_has_one_basic_app_health_smoke_test() -> None:
    pods = parent_test_pods(render_chart())

    assert [pod["metadata"]["name"] for pod in pods] == [
        "openhands-test-connection"
    ]
    pod = pods[0]
    assert pod["metadata"]["annotations"] == {
        "helm.sh/hook": "test",
        "helm.sh/hook-delete-policy": "before-hook-creation",
    }

    spec = pod["spec"]
    assert spec["restartPolicy"] == "Never"
    assert spec["automountServiceAccountToken"] is False
    assert spec["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 65534,
        "runAsGroup": 65534,
        "seccompProfile": {"type": "RuntimeDefault"},
    }

    container = spec["containers"][0]
    assert container["command"] == ["wget"]
    assert container["args"] == [
        "-q",
        "-T",
        "10",
        "-O",
        "/dev/null",
        "http://openhands-service:3000/health",
    ]
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }


@pytest.mark.parametrize("disabled_value", ("tests.enabled=false", "enabled=false"))
def test_smoke_test_respects_its_enable_gates(disabled_value: str) -> None:
    assert parent_test_pods(render_chart(set_values=(disabled_value,))) == []


def test_disabling_helm_tests_removes_only_the_parent_test_hook() -> None:
    enabled = resource_identities(render_chart())
    disabled = resource_identities(
        render_chart(set_values=("tests.enabled=false",))
    )

    assert enabled - disabled == {("v1", "Pod", "openhands-test-connection")}
    assert disabled - enabled == set()


def test_smoke_test_supports_a_pinned_image_and_registry_secret() -> None:
    digest = "sha256:" + "a" * 64
    pod = parent_test_pods(
        render_chart(
            set_values=(
                f"tests.image.digest={digest}",
                "imagePullSecrets[0].name=registry-creds",
            )
        )
    )[0]

    assert pod["spec"]["containers"][0]["image"] == f"busybox@{digest}"
    assert pod["spec"]["imagePullSecrets"] == [{"name": "registry-creds"}]


def test_kind_ci_fixtures_live_at_repository_root() -> None:
    root_ci = REPO_ROOT / "ci"
    secret_bootstrap = root_ci / "create-kind-secrets.sh"

    assert (root_ci / "kind-values.yaml").is_file()
    assert set(KIND_PROFILE_VALUES) == {"ephemeral", "persistent"}
    assert all(path.is_file() for path in KIND_PROFILE_VALUES.values())
    assert secret_bootstrap.is_file()
    assert os.access(secret_bootstrap, os.X_OK)
    assert not (OPENHANDS_CHART / "ci" / "kind-values.yaml").exists()
    assert not (OPENHANDS_CHART / "ci" / "create-kind-secrets.sh").exists()


@pytest.mark.parametrize("profile", KIND_PROFILE_VALUES)
def test_kind_profiles_render_the_smoke_hook(profile: str) -> None:
    documents = render_kind_profile(profile)
    pods = parent_test_pods(documents)

    assert [pod["metadata"]["name"] for pod in pods] == ["openhands-test-connection"]
    assert pods[0]["spec"]["containers"][0]["image"] == (
        "busybox@sha256:"
        "73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662"
    )


def test_kind_secret_bootstrap_is_idempotent_and_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "kubectl-calls.txt"
    fake_kubectl = fake_bin / "kubectl"
    fake_kubectl.write_text(
        """#!/bin/sh
printf '%s\n' "$*" >> "$KUBECTL_LOG"
case " $* " in
  *" create secret generic "*)
    printf '%s\n' 'apiVersion: v1' 'kind: Secret' 'metadata:' '  name: fake'
    ;;
  *" apply -f - "*)
    cat >/dev/null
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_kubectl.chmod(0o755)
    monkeypatch.setenv("KUBECTL_LOG", str(calls))
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")

    subprocess.run(
        ["bash", str(KIND_SECRETS_SCRIPT), "ci-namespace"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    invocations = calls.read_text(encoding="utf-8").splitlines()

    create_calls = [line for line in invocations if " create secret generic " in line]
    apply_calls = [line for line in invocations if " apply -f -" in line]
    assert len(create_calls) == len(apply_calls) == 10
    for secret in (
        "jwt-secret",
        "keycloak-realm",
        "keycloak-admin",
        "postgres-password",
        "redis",
        "lite-llm-api-key",
        "admin-password",
        "default-api-key",
        "sandbox-api-key",
        "litellm-env-secrets",
    ):
        create_call = next(
            line for line in create_calls if f"secret generic {secret}" in line
        )
        assert "-n ci-namespace" in create_call
        assert "--dry-run=client" in create_call


def test_replicated_relocates_the_smoke_test_image() -> None:
    values = REPLICATED_OPENHANDS.read_text(encoding="utf-8")

    assert (
        "    tests:\n"
        "      image:\n"
        "        repository: 'images.r9.all-hands.dev/proxy/"
        "{{repl LicenseFieldValue \"appSlug\"}}/docker.io/library/busybox'"
    ) in values


def test_kind_workflow_runs_only_the_smoke_test_and_reports_failures() -> None:
    workflow = HELM_TEST_WORKFLOW.read_text(encoding="utf-8")
    workflow_definition = yaml.load(workflow, Loader=yaml.BaseLoader)
    trigger_block = workflow.split("jobs:", 1)[0]

    assert "pull_request:" in trigger_block
    assert "merge_group:" in trigger_block
    assert "workflow_dispatch:" in trigger_block
    assert "paths:" not in trigger_block
    assert workflow_definition["on"]["push"]["branches"] == ["main"]
    assert set(workflow_definition["jobs"]) == {"kind-tests"}
    kind_job = workflow_definition["jobs"]["kind-tests"]
    assert "needs" not in kind_job
    assert "if" not in kind_job
    assert kind_job["name"] == "KinD install and helm test (${{ matrix.profile }})"
    assert kind_job["strategy"] == {
        "fail-fast": "false",
        "max-parallel": "2",
        "matrix": {
            "include": [
                {
                    "profile": "ephemeral",
                    "values": "ci/kind-profiles/ephemeral.yaml",
                },
                {
                    "profile": "persistent",
                    "values": "ci/kind-profiles/persistent.yaml",
                },
            ]
        },
    }
    assert kind_job["env"] == {
        "PROFILE": "${{ matrix.profile }}",
        "PROFILE_VALUES": "${{ matrix.values }}",
        "RELEASE": "openhands",
        "NAMESPACE": "openhands",
        "CHART": "charts/openhands",
        "KIND_CLUSTER": "openhands-ci",
    }
    assert workflow_definition["permissions"] == {"contents": "read"}
    assert "dorny/paths-filter@" not in workflow

    action_refs = re.findall(r"^\s*- uses: ([^\s]+)", workflow, flags=re.MULTILINE)
    assert action_refs
    assert all(re.search(r"@[0-9a-f]{40}$", ref) for ref in action_refs)

    assert "version: v3.21.3" in workflow
    assert "version: v0.32.0" in workflow
    assert "kubectl_version: v1.36.1" in workflow
    assert 'bash ci/create-kind-secrets.sh "$NAMESPACE"' in workflow
    assert "--values ci/kind-values.yaml" in workflow
    assert '--values "$PROFILE_VALUES"' in workflow
    assert "cluster_name: openhands-ci" in workflow
    assert "charts/openhands/ci" not in workflow
    dependency_build_index = workflow.index('helm dependency build "$CHART"')
    for repository_command in (
        "helm repo add lmnr https://lmnr-ai.github.io/lmnr-helm",
        "helm repo add minio https://charts.min.io/",
        "helm repo add bitnami https://charts.bitnami.com/bitnami",
    ):
        assert repository_command in workflow
        assert workflow.index(repository_command) < dependency_build_index
    assert "helm dependency build \"$CHART\"" in workflow
    assert "helm install \"$RELEASE\" \"$CHART\"" in workflow
    assert "helm test \"$RELEASE\"" in workflow
    assert "set -euo pipefail" in workflow
    assert "for test_run in 1 2; do" in workflow
    assert '--filter "name=${RELEASE}-test-connection"' in workflow
    assert "--filter '!name=" not in workflow
    assert "--logs" in workflow
    assert "helm get hooks" in workflow
    assert "kind export logs" in workflow
    assert "actions/upload-artifact" in workflow
    assert (
        "name: helm-chart-test-diagnostics-${{ matrix.profile }}-attempt-"
        "${{ github.run_attempt }}"
    ) in workflow
    assert "kubectl get storageclass standard" in workflow
    assert "kubectl wait" in workflow
    for pvc in (
        "openhands-minio",
        "data-openhands-postgresql-0",
        "redis-data-openhands-redis-master-0",
    ):
        assert f'"{pvc}"' in workflow
    assert '"pvc/$pvc"' in workflow
    assert 'kubectl get pvc -n "$NAMESPACE" -o wide' in workflow
    assert "kubectl get pv -o wide" in workflow
    assert "kubectl get storageclass -o wide" in workflow
    assert 'kubectl describe pvc -n "$NAMESPACE"' in workflow
    assert "if: failure()\n        run: |\n          mkdir -p artifacts" in workflow


def test_script_workflow_installs_yaml_dependency() -> None:
    script_tests = SCRIPT_TESTS_WORKFLOW.read_text(encoding="utf-8")
    assert "--with PyYAML==6.0.3" in script_tests
