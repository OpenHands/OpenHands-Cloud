from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = ROOT / ".github/workflows/deploy-replicated.yml"
E2E_WORKFLOW = ROOT / ".github/workflows/e2e-replicated.yml"
TEST_WORKFLOW = ROOT / ".github/workflows/test-scripts.yml"


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
    }

    assert workflow["env"]["ARGO_SERVER"] == "https://workflows.dev.all-hands.dev"

    command = trigger["run"]
    assert "jq -n" in command
    assert "instance: $instance" in command
    assert "run_url" not in command
    assert "target-url" not in command
    assert "test_revision" not in command
    assert "all-hands-testing.dev" not in command
    assert "curl --fail-with-body" in command
    assert (
        "${ARGO_SERVER}/api/v1/events/openhands-e2e/replicated-deploy" in command
    )
    assert 'Authorization: Bearer ${ARGO_TOKEN}' in command


def test_e2e_workflow_rejects_an_instance_no_binding_serves():
    """A caller typo would otherwise dispatch, match nothing, and pass."""
    command = trigger_step()["run"]
    assert "unstable|beta|stable" in command


def test_e2e_workflow_confirms_a_run_started():
    """Argo answers 200 {} whether or not a binding matched, so the status code
    alone cannot distinguish a dispatched run from a silent no-op."""
    command = trigger_step()["run"]
    assert "${ARGO_SERVER}/api/v1/workflows/openhands-e2e" in command
    assert 'openhands-e2e-${INSTANCE}-' in command
    assert "no openhands-e2e-${INSTANCE}-* workflow was created" in command
    assert "exit 1" in command


def test_e2e_workflow_never_retries_the_dispatch():
    """A retry whose first attempt already landed starts a second suite."""
    assert "--retry" not in trigger_step()["run"]


def test_deploy_replicated_calls_e2e_only_after_a_successful_deploy():
    workflow = load_workflow(DEPLOY_WORKFLOW)
    e2e = workflow["jobs"]["e2e"]

    assert e2e == {
        "name": "E2E Replicated",
        "needs": "deploy",
        "uses": "./.github/workflows/e2e-replicated.yml",
        "with": {"instance": "${{ inputs.instance }}"},
    }


def test_workflow_contract_runs_when_either_workflow_changes():
    text = TEST_WORKFLOW.read_text(encoding="utf-8")
    assert "- '.github/workflows/deploy-replicated.yml'" in text
    assert "- '.github/workflows/e2e-replicated.yml'" in text
