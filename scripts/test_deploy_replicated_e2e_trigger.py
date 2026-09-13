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
        "RUN_ID": "${{ github.run_id }}",
        "RUN_ATTEMPT": "${{ github.run_attempt }}",
    }

    command = trigger["run"]
    assert "jq -n" in command
    assert "instance: $instance" in command
    assert "run_id: $run_id" in command
    assert "run_attempt: $run_attempt" in command
    # Identifiers only. A caller still cannot name a URL, a target or a suite.
    assert "run_url" not in command
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


def test_dispatch_payload_satisfies_the_binding_selector():
    """The Argo binding selects on run_id and run_attempt being positive
    numbers. Sending them as JSON strings, or not at all, matches no binding -
    and an unmatched event is answered 200, so the dispatch fails silently."""
    command = trigger_step()["run"]
    assert "--argjson run_id" in command
    assert "--argjson run_attempt" in command
    assert "--arg run_id" not in command
    assert "--arg run_attempt" not in command


def confirm_step():
    steps = load_workflow(E2E_WORKFLOW)["jobs"]["trigger-e2e"]["steps"]
    return next(
        step for step in steps if step.get("name") == "Confirm the run was created"
    )


def test_the_dispatched_workflow_name_matches_what_argo_derives():
    """Argo builds the name from the same run_id and run_attempt it is sent."""
    command = trigger_step()["run"]
    assert 'workflow="openhands-e2e-${INSTANCE}-gh-${RUN_ID}-${RUN_ATTEMPT}"' in command
    assert "${WORKFLOW}" in confirm_step()["run"]


def test_dispatch_is_confirmed_by_reading_the_run_back():
    # The POST returns 200 for an unmatched event too, so only reading the
    # named object back proves the run exists.
    command = confirm_step()["run"]
    assert "/api/v1/workflows/openhands-e2e/${WORKFLOW}" in command
    # One exact object, never a namespace listing - the submit token has no
    # list permission.
    assert "/api/v1/workflows/openhands-e2e?" not in command


def test_confirmation_cannot_redden_the_deploy_badge():
    # This job runs inside release-replicated-unstable.yml, whose run
    # conclusion drives the deploy badge AND the `last-deploy` required check.
    # Anything fatal here lets an E2E-side blip block every open PR under a
    # message telling people to fix a deploy that is fine.
    command = confirm_step()["run"]
    assert "::warning::" in command
    assert "::error::" not in command
    assert "exit 1" not in command


def test_confirmation_cannot_outlive_the_job_timeout():
    # A wall-clock deadline, not an attempt count: attempt-bounded retries
    # against a hanging API can exceed timeout-minutes and fail the release
    # run for an E2E-side problem.
    job = load_workflow(E2E_WORKFLOW)["jobs"]["trigger-e2e"]
    command = confirm_step()["run"]
    assert "deadline=$(( $(date +%s) + 120 ))" in command
    assert job["timeout-minutes"] * 60 > 120 * 2


def test_e2e_workflow_does_not_wait_for_the_suite():
    """Holding a runner for a 45-minute suite to set a conclusion nothing
    reads. The result is read from Argo, and from ReportPortal for history."""
    job = load_workflow(E2E_WORKFLOW)["jobs"]["trigger-e2e"]
    assert job["timeout-minutes"] <= 10
    assert [step.get("name") for step in job["steps"]] == [
        "Trigger Replicated E2E",
        "Confirm the run was created",
    ]


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


def test_workflow_contract_runs_when_related_workflows_change():
    text = TEST_WORKFLOW.read_text(encoding="utf-8")
    assert "- '.github/workflows/deploy-replicated.yml'" in text
    assert "- '.github/workflows/e2e-replicated.yml'" in text
    assert "- '.github/workflows/release-gate.yml'" in text


def release_gate_command():
    workflow = load_workflow(RELEASE_GATE_WORKFLOW)
    return workflow["jobs"]["release-gate"]["steps"][0]["run"]


def test_incident_io_is_not_wired_into_ci():
    # Paging belongs to the Argo exit handler in saas-deploy: it sees scheduled
    # runs, which CI cannot, and it cannot redden the E2E verdict.
    for path in [E2E_WORKFLOW, RELEASE_GATE_WORKFLOW]:
        assert "incident.io" not in path.read_text(), path


def test_release_gate_reads_the_result_from_argo():
    # Most unstable runs are scheduled and have no GitHub run at all, so job
    # names cannot be the signal - the gate would be blind to them.
    command = release_gate_command()
    assert "/api/v1/workflows/${ARGO_NAMESPACE}" in command
    assert "openhands-e2e-${E2E_INSTANCE}-" in command
    assert ".status.finishedAt" in command
    assert "sort_by(.finished)" in command
    # No trace of the old GitHub-job-name matching.
    assert "actions/workflows/" not in command
    assert 'startswith($name + " /")' not in command


def test_release_gate_uses_a_read_only_argo_token():
    # The submit token is environment-scoped and can create runs; the gate must
    # not use it. The read-only token is repo-level because the e2e-replicated
    # environment admits only main and openhands/*, which a pull_request job
    # can never satisfy.
    workflow = load_workflow(RELEASE_GATE_WORKFLOW)
    job = workflow["jobs"]["release-gate"]
    assert "environment" not in job
    step = job["steps"][0]
    assert step["env"]["ARGO_GATE_TOKEN"] == (
        "${{ secrets.ARGO_WORKFLOWS_GATE_TOKEN }}"
    )
    assert "ARGO_WORKFLOWS_E2E_TOKEN" not in yaml.safe_dump(workflow)


def test_release_gate_staleness_window_fits_inside_argo_retention():
    # Argo has no archive database here; a finished run is deleted after its
    # 24h TTL. A window at or past that blocks on runs that merely aged out.
    workflow = load_workflow(RELEASE_GATE_WORKFLOW)
    assert int(workflow["env"]["E2E_MAX_AGE_HOURS"]) < 24


def test_release_gate_scopes_the_release_please_branch():
    workflow = load_workflow(RELEASE_GATE_WORKFLOW)
    job = workflow["jobs"]["release-gate"]

    assert "github.event.pull_request.head.ref" in str(job)
    assert "github.event.pull_request.base.ref" in str(job)
    assert "github.event.pull_request.head.repo.full_name" in str(job)
    assert "autorelease: openhands pending" not in release_gate_command()


def test_release_gate_refreshes_on_a_schedule():
    # A scheduled Argo run fires no GitHub event, so workflow_run alone would
    # leave a blocked release PR red until someone pushed to it.
    workflow = load_workflow(RELEASE_GATE_WORKFLOW)
    triggers = workflow[True]
    assert "schedule" in triggers
    assert triggers["schedule"][0]["cron"]
    refresh = workflow["jobs"]["refresh-release-gate"]
    assert "schedule" in refresh["if"]


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
