"""Behavioral checks for the first native OpenHands Helm smoke test."""

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
INVALID_VALUES_DIR = REPO_ROOT / "ci" / "invalid-values"
KIND_SECRETS_SCRIPT = REPO_ROOT / "ci" / "create-kind-secrets.sh"
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"
HELM_TEST_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "helm-chart-tests.yml"
PR_CHECKS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
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


def rendered_resource(
    documents: list[dict[str, Any]], kind: str, name: str
) -> dict[str, Any]:
    matches = [
        document
        for document in documents
        if document.get("kind") == kind
        and document.get("metadata", {}).get("name") == name
    ]
    assert len(matches) == 1, f"expected one {kind}/{name}, got {len(matches)}"
    return matches[0]


def persistent_storage_claims(
    documents: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    claims = {
        f"PersistentVolumeClaim/{document['metadata']['name']}": document
        for document in documents
        if document.get("kind") == "PersistentVolumeClaim"
    }
    for stateful_set in (
        document
        for document in documents
        if document.get("kind") == "StatefulSet"
    ):
        for claim in stateful_set.get("spec", {}).get("volumeClaimTemplates", []):
            claims[
                f"StatefulSet/{stateful_set['metadata']['name']}/"
                f"{claim['metadata']['name']}"
            ] = claim
    return claims


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
    assert INVALID_VALUES_DIR.is_dir()
    assert secret_bootstrap.is_file()
    assert os.access(secret_bootstrap, os.X_OK)
    assert not (OPENHANDS_CHART / "ci" / "kind-values.yaml").exists()
    assert not (OPENHANDS_CHART / "ci" / "create-kind-secrets.sh").exists()


@pytest.mark.parametrize(
    ("values_files", "expected_error_fragments"),
    (
        pytest.param(
            (INVALID_VALUES_DIR / "postgresql-auth-type.yaml",),
            ("enablePostgresUser", "boolean"),
            id="postgresql-schema",
        ),
        pytest.param(
            (
                KIND_VALUES,
                KIND_PROFILE_VALUES["persistent"],
                INVALID_VALUES_DIR / "redis-persistence-type.yaml",
            ),
            ("redis", "persistence", "boolean"),
            id="persistent-redis-schema",
        ),
        pytest.param(
            (INVALID_VALUES_DIR / "sandbox-gateway-missing-hostname.yaml",),
            (
                "sandboxGateway.hostname is required when "
                "sandboxGateway.enabled is true",
            ),
            id="sandbox-gateway-required",
        ),
        pytest.param(
            (INVALID_VALUES_DIR / "laminar-aws-secrets-missing-name.yaml",),
            ("AWS Secrets Manager", "secretName", "clusterName"),
            id="laminar-fail",
        ),
    ),
)
def test_invalid_values_are_rejected_before_install(
    values_files: tuple[Path, ...], expected_error_fragments: tuple[str, ...]
) -> None:
    result = run_chart_render(values_files=values_files)
    output = result.stdout + result.stderr

    assert result.returncode != 0, (
        f"expected Helm rendering to reject values files: {values_files}"
    )
    for fragment in expected_error_fragments:
        assert fragment in output


@pytest.mark.parametrize("profile", KIND_PROFILE_VALUES)
def test_kind_profiles_render_the_smoke_hook_and_lint(profile: str) -> None:
    profile_values = KIND_PROFILE_VALUES[profile]
    documents = render_kind_profile(profile)
    pods = parent_test_pods(documents)

    assert [pod["metadata"]["name"] for pod in pods] == ["openhands-test-connection"]
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
            "--values",
            str(profile_values),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("profile", KIND_PROFILE_VALUES)
def test_kind_profiles_connect_services_to_compatible_workloads(profile: str) -> None:
    documents = render_kind_profile(profile)
    workloads = [
        document
        for document in documents
        if document.get("kind") in {"DaemonSet", "Deployment", "StatefulSet"}
    ]

    for service in (
        document for document in documents if document.get("kind") == "Service"
    ):
        selector = service.get("spec", {}).get("selector")
        if not selector:
            continue

        matching_workloads = [
            workload
            for workload in workloads
            if selector.items()
            <= workload.get("spec", {})
            .get("template", {})
            .get("metadata", {})
            .get("labels", {})
            .items()
        ]
        service_name = service["metadata"]["name"]
        assert matching_workloads, f"Service/{service_name} selects no workload"

        exposed_ports: set[str | int] = set()
        for workload in matching_workloads:
            for container in workload["spec"]["template"]["spec"].get(
                "containers", []
            ):
                for port in container.get("ports", []):
                    exposed_ports.add(port["containerPort"])
                    if port.get("name"):
                        exposed_ports.add(port["name"])

        for port in service["spec"].get("ports", []):
            target_port = port.get("targetPort", port["port"])
            assert target_port in exposed_ports, (
                f"Service/{service_name} targetPort {target_port!r} is not exposed "
                "by a selected workload"
            )


@pytest.mark.parametrize("profile", KIND_PROFILE_VALUES)
def test_kind_profiles_render_the_openhands_runtime_contract(profile: str) -> None:
    documents = render_kind_profile(profile)

    deployment = rendered_resource(documents, "Deployment", "openhands")
    service = rendered_resource(documents, "Service", "openhands-service")
    pod_labels = deployment["spec"]["template"]["metadata"]["labels"]
    assert service["spec"]["selector"].items() <= pod_labels.items()
    service_port = next(
        port for port in service["spec"]["ports"] if port["name"] == "openhands"
    )
    assert service_port["port"] == 3000
    assert service_port["targetPort"] == 3000
    assert service_port["protocol"] == "TCP"
    app_container = next(
        container
        for container in deployment["spec"]["template"]["spec"]["containers"]
        if container["name"] == "openhands"
    )
    assert any(port["containerPort"] == 3000 for port in app_container["ports"])
    assert app_container["startupProbe"]["httpGet"]["path"] == "/health"
    assert app_container["startupProbe"]["httpGet"]["port"] == 3000
    assert app_container["readinessProbe"]["httpGet"]["path"] == "/ready"
    assert app_container["readinessProbe"]["httpGet"]["port"] == 3000


@pytest.mark.parametrize("profile", KIND_PROFILE_VALUES)
def test_kind_profiles_render_expected_storage(profile: str) -> None:
    documents = render_kind_profile(profile)

    claims = persistent_storage_claims(documents)
    if profile == "ephemeral":
        assert claims == {}
    else:
        assert set(claims) == {
            "PersistentVolumeClaim/openhands-minio",
            "StatefulSet/openhands-postgresql/data",
            "StatefulSet/openhands-redis-master/redis-data",
        }
        assert {
            (
                claim["spec"]["storageClassName"],
                claim["spec"]["resources"]["requests"]["storage"],
            )
            for claim in claims.values()
        } == {("standard", "1Gi")}


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


def test_fast_workflows_keep_keycloak_and_helm_render_checks_separate() -> None:
    pr_checks = PR_CHECKS_WORKFLOW.read_text(encoding="utf-8")
    script_tests = SCRIPT_TESTS_WORKFLOW.read_text(encoding="utf-8")
    pr_trigger = pr_checks.split("jobs:", 1)[0]
    workflow_definition = yaml.load(pr_checks, Loader=yaml.BaseLoader)
    jobs = workflow_definition["jobs"]

    keycloak_job = jobs["test-keycloak-realm-template"]
    helm_render_job = jobs["test-openhands-helm-chart-render"]

    def job_commands(job: dict[str, Any]) -> str:
        return "\n".join(
            step.get("run", "") for step in job["steps"] if "run" in step
        )

    keycloak_commands = job_commands(keycloak_job)
    helm_render_commands = job_commands(helm_render_job)

    assert "'ci/**'" in pr_trigger
    for test_file in (
        "scripts/test_keycloak_realm_template.py",
        "scripts/test_openhands_secrets_template.py",
        "scripts/test_replicated_resolver_label.py",
    ):
        assert test_file in keycloak_commands
    assert "scripts/test_helm_test_hooks_template.py" not in keycloak_commands
    assert "scripts/test_helm_test_hooks_template.py" in helm_render_commands
    for commands in (keycloak_commands, helm_render_commands):
        assert "helm dependency build charts/openhands" in commands
    assert "python3 -m pip install pytest PyYAML==6.0.3" in helm_render_commands
    assert "--with PyYAML==6.0.3" in script_tests
