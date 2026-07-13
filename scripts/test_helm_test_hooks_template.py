"""Behavioral checks for the first native OpenHands Helm smoke test."""

from __future__ import annotations

import os
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
OPENHANDS_CHART = REPO_ROOT / "charts" / "openhands"
KIND_VALUES = OPENHANDS_CHART / "ci" / "kind-values.yaml"
KIND_SECRETS_SCRIPT = OPENHANDS_CHART / "ci" / "create-kind-secrets.sh"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"
HELM_TEST_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "helm-chart-tests.yml"
PR_CHECKS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
SCRIPT_TESTS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test-scripts.yml"


def render_chart(
    *,
    release: str = "openhands",
    set_values: tuple[str, ...] = (),
    values_file: Path | None = None,
) -> list[dict[str, Any]]:
    command = ["helm", "template", release, str(OPENHANDS_CHART)]
    if values_file is not None:
        command.extend(["--values", str(values_file)])
    for value in set_values:
        command.extend(["--set", value])

    result = subprocess.run(
        command,
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
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


def test_kind_fixture_renders_the_same_smoke_test_and_lints() -> None:
    pods = parent_test_pods(render_chart(values_file=KIND_VALUES))

    assert [pod["metadata"]["name"] for pod in pods] == [
        "openhands-test-connection"
    ]
    assert pods[0]["spec"]["containers"][0]["image"] == (
        "busybox@sha256:"
        "73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662"
    )

    result = subprocess.run(
        [
            "helm",
            "lint",
            str(OPENHANDS_CHART),
            "--values",
            str(KIND_VALUES),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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


def test_packaged_chart_excludes_ci_only_files(tmp_path: Path) -> None:
    subprocess.run(
        ["helm", "package", str(OPENHANDS_CHART), "--destination", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    package = next(tmp_path.glob("openhands-*.tgz"))

    with tarfile.open(package, "r:gz") as archive:
        names = archive.getnames()

    assert not any(name.startswith("openhands/ci/") for name in names)


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
    trigger_block = workflow.split("jobs:", 1)[0]

    assert "pull_request:" in trigger_block
    assert "merge_group:" in trigger_block
    assert "workflow_dispatch:" in trigger_block
    assert "paths:" not in trigger_block
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "if: always()" in workflow

    action_refs = re.findall(r"^\s*- uses: ([^\s]+)", workflow, flags=re.MULTILINE)
    assert action_refs
    assert all(re.search(r"@[0-9a-f]{40}$", ref) for ref in action_refs)

    assert "version: v3.21.3" in workflow
    assert "version: v0.32.0" in workflow
    assert "kubectl_version: v1.36.1" in workflow
    assert "helm dependency build \"$CHART\"" in workflow
    assert "helm install \"$RELEASE\" \"$CHART\"" in workflow
    assert "helm test \"$RELEASE\"" in workflow
    assert '--filter "name=${RELEASE}-test-connection"' in workflow
    assert "--filter '!name=" not in workflow
    assert "--logs" in workflow
    assert "helm get hooks" in workflow
    assert "kind export logs" in workflow
    assert "actions/upload-artifact" in workflow
    assert "if: failure()\n        run: |\n          mkdir -p artifacts" in workflow


def test_fast_workflows_run_the_smoke_test_contract() -> None:
    pr_checks = PR_CHECKS_WORKFLOW.read_text(encoding="utf-8")
    script_tests = SCRIPT_TESTS_WORKFLOW.read_text(encoding="utf-8")

    assert "scripts/test_helm_test_hooks_template.py" in pr_checks
    assert "pytest PyYAML==6.0.3" in pr_checks
    assert "--with PyYAML==6.0.3" in script_tests
