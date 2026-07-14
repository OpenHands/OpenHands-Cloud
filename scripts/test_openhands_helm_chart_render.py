"""Render-contract tests for the OpenHands Helm chart."""

from __future__ import annotations

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
PR_CHECKS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"


def run_chart_render(
    *, values_files: tuple[Path, ...] = ()
) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "openhands", str(OPENHANDS_CHART)]
    for values_file in values_files:
        command.extend(["--values", str(values_file)])

    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def render_chart(*, values_files: tuple[Path, ...]) -> list[dict[str, Any]]:
    result = run_chart_render(values_files=values_files)
    result.check_returncode()
    return [
        document
        for document in yaml.safe_load_all(result.stdout)
        if isinstance(document, dict)
    ]


def render_profile(profile: str) -> list[dict[str, Any]]:
    return render_chart(
        values_files=(KIND_VALUES, KIND_PROFILE_VALUES[profile]),
    )


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
        document for document in documents if document.get("kind") == "StatefulSet"
    ):
        for claim in stateful_set.get("spec", {}).get("volumeClaimTemplates", []):
            claims[
                f"StatefulSet/{stateful_set['metadata']['name']}/"
                f"{claim['metadata']['name']}"
            ] = claim
    return claims


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

    assert (
        result.returncode != 0
    ), f"expected Helm rendering to reject values files: {values_files}"
    for fragment in expected_error_fragments:
        assert fragment in output


@pytest.mark.parametrize("profile", KIND_PROFILE_VALUES)
def test_profiles_render_and_lint(profile: str) -> None:
    profile_values = KIND_PROFILE_VALUES[profile]
    assert render_profile(profile)

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
def test_profiles_connect_services_to_compatible_workloads(profile: str) -> None:
    documents = render_profile(profile)
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
            for container in workload["spec"]["template"]["spec"].get("containers", []):
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
def test_profiles_render_the_openhands_runtime_contract(profile: str) -> None:
    documents = render_profile(profile)

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
def test_profiles_render_expected_storage(profile: str) -> None:
    documents = render_profile(profile)

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


def test_pr_checks_run_the_render_contract_suite() -> None:
    workflow = PR_CHECKS_WORKFLOW.read_text(encoding="utf-8")
    trigger_block = workflow.split("jobs:", 1)[0]
    workflow_definition = yaml.load(workflow, Loader=yaml.BaseLoader)
    render_job = workflow_definition["jobs"]["test-openhands-helm-chart-render"]
    helm_setup = next(
        step for step in render_job["steps"] if step.get("name") == "Set up Helm"
    )
    commands = "\n".join(
        step.get("run", "") for step in render_job["steps"] if "run" in step
    )

    assert "'ci/**'" in trigger_block
    assert "'scripts/test_openhands_helm_chart_render.py'" in trigger_block
    assert helm_setup["with"]["version"] == "v3.21.3"
    assert "helm dependency build charts/openhands" in commands
    assert "python3 -m pip install pytest PyYAML==6.0.3" in commands
    assert "scripts/test_openhands_helm_chart_render.py" in commands
