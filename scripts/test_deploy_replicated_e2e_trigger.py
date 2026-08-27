from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = ROOT / ".github/workflows/deploy-replicated.yml"
E2E_WORKFLOW = ROOT / ".github/workflows/e2e-replicated.yml"
RELEASE_GATE_WORKFLOW = ROOT / ".github/workflows/release-gate.yml"
TEST_WORKFLOW = ROOT / ".github/workflows/test-scripts.yml"
RELEASE_WORKFLOWS = {
    "unstable": ROOT / ".github/workflows/release-replicated-unstable.yml",
    "beta": ROOT / ".github/workflows/release-replicated-beta.yml",
}


def load_workflow(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def trigger_step():
    job = load_workflow(E2E_WORKFLOW)["jobs"]["trigger-e2e"]
    return next(
        step for step in job["steps"] if step.get("name") == "Trigger Replicated E2E"
    )


def test_e2e_workflow_can_only_be_called_by_another_workflow():
    workflow = load_workflow(E2E_WORKFLOW)
    triggers = workflow[True]

    assert set(triggers) == {"workflow_call"}
    assert triggers["workflow_call"] == {
        "inputs": {
            "instance": {"required": True, "type": "string"},
        }
    }


def test_e2e_workflow_uses_its_environment_token_and_argo_owned_target():
    workflow = load_workflow(E2E_WORKFLOW)
    job = workflow["jobs"]["trigger-e2e"]
    assert job["environment"] == "e2e-replicated"

    trigger = next(
        step for step in job["steps"] if step.get("name") == "Trigger Replicated E2E"
    )
    assert trigger["env"] == {
        "ARGO_TOKEN": "${{ secrets.ARGO_WORKFLOWS_E2E_TOKEN }}",
        "INSTANCE": "${{ inputs.instance }}",
        "RUN_ATTEMPT": "${{ github.run_attempt }}",
        "RUN_ID": "${{ github.run_id }}",
    }

    command = trigger["run"]
    assert "jq -n" in command
    assert "instance: $instance" in command
    assert "run_id: $run_id" in command
    assert "run_attempt: $run_attempt" in command
    assert "target-url" not in command
    assert "test_revision" not in command
    assert "all-hands-testing.dev" not in command
    assert "curl --fail-with-body" in command
    assert (
        "https://workflows.dev.all-hands.dev/api/v1/events/"
        "openhands-e2e/replicated-deploy" in command
    )
    assert 'Authorization: Bearer ${ARGO_TOKEN}' in command


def test_e2e_workflow_rejects_an_instance_no_binding_serves():
    """A caller typo would otherwise dispatch, match nothing, and pass."""
    command = trigger_step()["run"]
    assert "unstable|beta|stable" in command


def test_e2e_workflow_never_retries_the_dispatch():
    """A retry whose first attempt already landed starts a second suite."""
    assert "--retry" not in trigger_step()["run"]


def test_e2e_workflow_polls_only_its_exact_argo_workflow():
    workflow = load_workflow(E2E_WORKFLOW)
    job = workflow["jobs"]["trigger-e2e"]
    assert job["timeout-minutes"] >= 50

    wait = next(
        step for step in job["steps"] if step.get("name") == "Wait for Replicated E2E"
    )
    command = wait["run"]
    assert "openhands-e2e-${INSTANCE}-gh-${RUN_ID}-${RUN_ATTEMPT}" in command
    assert "/api/v1/workflows/openhands-e2e/${WORKFLOW_NAME}" in command
    assert "/api/v1/workflows/openhands-e2e?" not in command
    for phase in ("Succeeded", "Failed", "Error"):
        assert f'"$phase" == "{phase}"' in command


def test_incident_io_receives_only_unstable_real_results():
    workflow = load_workflow(E2E_WORKFLOW)
    job = workflow["jobs"]["trigger-e2e"]
    report = next(
        step for step in job["steps"] if step.get("name") == "Report to incident.io"
    )

    assert "always()" in report["if"]
    assert "inputs.instance == 'unstable'" in report["if"]
    assert report["env"] == {
        "INCIDENT_IO_SOURCE_ID": (
            "${{ vars.INCIDENT_IO_UNSTABLE_E2E_SOURCE_ID }}"
        ),
        "INCIDENT_IO_TOKEN": (
            "${{ secrets.INCIDENT_IO_UNSTABLE_E2E_TOKEN }}"
        ),
        "E2E_PHASE": "${{ steps.wait-for-e2e.outputs.phase }}",
        "WORKFLOW_NAME": "${{ steps.trigger-e2e.outputs.workflow-name }}",
    }
    command = report["run"]
    assert 'status="resolved"' in command
    assert 'status="firing"' in command
    assert 'deduplication_key: "openhands-cloud:replicated-e2e:unstable"' in command
    assert "api.incident.io/v2/alert_events/http/${INCIDENT_IO_SOURCE_ID}" in command
    assert "Authorization: Bearer ${INCIDENT_IO_TOKEN}" in command


def test_real_argo_result_controls_the_reusable_workflow_conclusion():
    workflow = load_workflow(E2E_WORKFLOW)
    steps = workflow["jobs"]["trigger-e2e"]["steps"]
    enforce = next(
        step for step in steps if step.get("name") == "Enforce Replicated E2E result"
    )

    assert "always()" in enforce["if"]
    assert enforce["env"] == {
        "E2E_PHASE": "${{ steps.wait-for-e2e.outputs.phase }}"
    }
    assert '"$E2E_PHASE" = "Succeeded"' in enforce["run"]


@pytest.mark.parametrize("instance", sorted(RELEASE_WORKFLOWS))
def test_each_release_calls_e2e_only_after_a_successful_deploy(instance):
    """Called directly by the release workflow, not nested under the deploy.

    Secrets reach only the workflow a call site names, so a job nested one
    level further down saw an empty token however the environment was set up.
    """
    workflow = load_workflow(RELEASE_WORKFLOWS[instance])

    assert workflow["jobs"]["e2e"] == {
        "name": "E2E Replicated",
        "needs": "deploy",
        "uses": "./.github/workflows/e2e-replicated.yml",
        "with": {"instance": instance},
        "secrets": "inherit",
    }


def test_deploy_replicated_does_not_call_e2e():
    assert "e2e" not in load_workflow(DEPLOY_WORKFLOW)["jobs"]


def release_gate_command():
    workflow = load_workflow(RELEASE_GATE_WORKFLOW)
    return workflow["jobs"]["release-gate"]["steps"][0]["run"]


def test_release_gate_scopes_the_release_please_branch():
    workflow = load_workflow(RELEASE_GATE_WORKFLOW)
    job = workflow["jobs"]["release-gate"]

    assert "github.event.pull_request.head.ref" in str(job)
    assert "github.event.pull_request.base.ref" in str(job)
    assert "github.event.pull_request.head.repo.full_name" in str(job)
    assert "autorelease: openhands pending" not in release_gate_command()


def test_release_gate_reads_the_reusable_e2e_job_completion():
    command = release_gate_command()

    assert 'startswith($name + " /")' in command
    assert ".completed_at" in command
    assert 'select(.name=="$E2E_JOB_NAME")' not in command


def test_release_gate_refreshes_after_unstable_e2e_completes():
    workflow = load_workflow(RELEASE_GATE_WORKFLOW)
    triggers = workflow[True]

    assert triggers["workflow_run"] == {
        "workflows": ["Release Replicated to Unstable"],
        "types": ["completed"],
    }
    refresh = workflow["jobs"]["refresh-release-gate"]
    assert refresh["permissions"] == {
        "actions": "write",
        "contents": "read",
        "pull-requests": "read",
    }
    assert "/rerun" in refresh["steps"][0]["run"]


def test_release_gate_override_must_postdate_the_e2e_signal():
    command = release_gate_command()

    assert "override_epoch" in command
    assert "signal_epoch" in command
    assert 'override_epoch" -le "$signal_epoch' in command



def test_workflow_contract_runs_when_related_workflows_change():
    text = TEST_WORKFLOW.read_text(encoding="utf-8")
    assert "- '.github/workflows/deploy-replicated.yml'" in text
    assert "- '.github/workflows/e2e-replicated.yml'" in text
    assert "- '.github/workflows/release-gate.yml'" in text
