#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest", "PyYAML"]
# ///
"""Runs the release gate's real shell step against fake `gh` and `curl`."""

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release-gate.yml"
UNSTABLE_WORKFLOW = ROOT / ".github/workflows/release-replicated-unstable.yml"
E2E_WORKFLOW = ROOT / ".github/workflows/e2e-replicated.yml"

DEPLOY_ID = 4242
DEPLOY_URL = "https://github.com/OpenHands/OpenHands-Cloud/actions/runs/4242"
PR_NUMBER = "1651"

FAKE_GH = r"""#!/usr/bin/env python3
import json, os, subprocess, sys

args = sys.argv[1:]
data = json.load(open(os.environ["FAKE_GH_DATA"]))

if args[:2] == ["pr", "comment"]:
    with open(os.environ["FAKE_GH_COMMENTS"], "a") as f:
        f.write(args[args.index("--body") + 1] + "\n")
    sys.exit(0)

assert args[0] == "api", args
jq = args[args.index("--jq") + 1] if "--jq" in args else None
path = next(a for a in args[1:] if not a.startswith("-") and a != jq)
for prefix, body in data.items():
    if path.startswith(prefix):
        break
else:
    print(f"unexpected gh call: {args}", file=sys.stderr)
    sys.exit(1)
if "--slurp" in args:
    body = [body]
out = json.dumps(body)
if jq:
    out = subprocess.run(["jq", "-r", jq], input=out, capture_output=True, text=True, check=True).stdout
print(out, end="" if out.endswith("\n") else "\n")
"""

FAKE_CURL = """#!/usr/bin/env bash
[ -n "${ARGO_DOWN:-}" ] && { echo "connection refused" >&2; exit 7; }
cat "$FAKE_ARGO_LISTING"
"""


def ts(minutes_ago: float) -> str:
    when = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def argo_run(name, phase, created_min_ago, finished_min_ago=None):
    status = {"phase": phase}
    if finished_min_ago is not None:
        status["finishedAt"] = ts(finished_min_ago)
    return {"metadata": {"name": name, "creationTimestamp": ts(created_min_ago)}, "status": status}


def own(attempt=1):
    return f"openhands-e2e-unstable-gh-{DEPLOY_ID}-{attempt}"


def gate_script() -> str:
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["release-gate"]["steps"]
    return next(s["run"] for s in steps if s.get("name") == "Evaluate release gate")


def gate_env() -> dict:
    return {k: str(v) for k, v in yaml.safe_load(WORKFLOW.read_text())["env"].items()}


def gnu_date_dir(bin_dir: Path):
    probe = subprocess.run(["date", "-u", "-d", "2026-01-01T00:00:00Z", "+%s"], capture_output=True)
    if probe.returncode == 0:
        return
    gdate = shutil.which("gdate")
    if not gdate:
        pytest.skip("the gate uses GNU date; install coreutils to run this test locally")
    (bin_dir / "date").symlink_to(gdate)


def run_gate(
    tmp_path,
    argo_items,
    deploy_status="completed",
    deploy_conclusion="success",
    deploy_job_conclusion="success",
    deployed_min_ago=30,
    labels=(),
    label_events=(),
    permission="write",
    existing_comments=(),
    argo_down=False,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    for name, body in (("gh", FAKE_GH), ("curl", FAKE_CURL)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    gnu_date_dir(bin_dir)

    deploy_run = {
        "id": DEPLOY_ID,
        "run_attempt": 1,
        "status": deploy_status,
        "conclusion": deploy_conclusion if deploy_status == "completed" else None,
        "head_sha": "b8b3e07aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "updated_at": ts(deployed_min_ago - 0.5),
        "html_url": DEPLOY_URL,
    }
    jobs = [
        {"name": "release", "completed_at": ts(deployed_min_ago + 3)},
        {
            "name": "deploy / Deploy to unstable",
            "conclusion": deploy_job_conclusion,
            "completed_at": ts(deployed_min_ago),
        },
        {"name": "E2E Replicated / trigger-e2e", "completed_at": ts(deployed_min_ago - 0.2)},
    ]
    gh_data = {
        "repos/OpenHands/OpenHands-Cloud/actions/workflows/release-replicated-unstable.yml/runs": {
            "workflow_runs": [deploy_run]
        },
        f"repos/OpenHands/OpenHands-Cloud/actions/runs/{DEPLOY_ID}/attempts/1/jobs": {"jobs": jobs},
        f"repos/OpenHands/OpenHands-Cloud/issues/{PR_NUMBER}/labels": [{"name": n} for n in labels],
        f"repos/OpenHands/OpenHands-Cloud/issues/{PR_NUMBER}/events": [
            {"event": "labeled", "label": {"name": "override-e2e-gate"}, "actor": {"login": who}, "created_at": when}
            for who, when in label_events
        ],
        f"repos/OpenHands/OpenHands-Cloud/issues/{PR_NUMBER}/comments": [{"body": b} for b in existing_comments],
        "repos/OpenHands/OpenHands-Cloud/collaborators/": {"permission": permission},
    }
    (tmp_path / "gh.json").write_text(json.dumps(gh_data))
    (tmp_path / "argo.json").write_text(json.dumps({"items": argo_items}))
    comments = tmp_path / "comments"
    comments.touch()
    summary = tmp_path / "summary.md"

    env = {
        **os.environ,
        **gate_env(),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "REPO": "OpenHands/OpenHands-Cloud",
        "PR_NUMBER": PR_NUMBER,
        "ARGO_GATE_TOKEN": "test",
        "GH_TOKEN": "test",
        "GITHUB_STEP_SUMMARY": str(summary),
        "FAKE_GH_DATA": str(tmp_path / "gh.json"),
        "FAKE_GH_COMMENTS": str(comments),
        "FAKE_ARGO_LISTING": str(tmp_path / "argo.json"),
    }
    if argo_down:
        env["ARGO_DOWN"] = "1"
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", gate_script()],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    result.summary = summary.read_text()
    result.comments = comments.read_text()
    return result


def test_blocks_while_the_e2e_for_the_newest_deploy_is_running(tmp_path):
    # An older green run exists, but the run for the newest build has not finished.
    result = run_gate(
        tmp_path,
        [
            argo_run("openhands-e2e-unstable-gh-4100-1", "Succeeded", 60, 50),
            argo_run(own(), "Running", 29),
        ],
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "E2E for deploy b8b3e07a is still running" in result.stdout


def test_blocks_when_the_only_green_run_predates_the_deploy(tmp_path):
    result = run_gate(
        tmp_path,
        [argo_run("openhands-e2e-unstable-scheduled-1", "Succeeded", 90, 80)],
    )
    assert result.returncode == 1
    assert "no unstable E2E has run since deploy b8b3e07a" in result.stdout


def test_blocks_while_the_deploy_is_in_progress(tmp_path):
    result = run_gate(
        tmp_path,
        [argo_run("openhands-e2e-unstable-scheduled-1", "Succeeded", 90, 80)],
        deploy_status="in_progress",
    )
    assert result.returncode == 1
    assert "unstable deploy of b8b3e07a is still in_progress" in result.stdout


def test_blocks_when_the_deploy_failed(tmp_path):
    result = run_gate(
        tmp_path,
        [argo_run("openhands-e2e-unstable-scheduled-1", "Succeeded", 90, 80)],
        deploy_conclusion="failure",
        deploy_job_conclusion="failure",
    )
    assert result.returncode == 1
    assert "unstable deploy of b8b3e07a did not succeed (run failure)" in result.stdout


def test_a_failed_e2e_trigger_does_not_hide_a_good_deploy(tmp_path):
    # The run fails when the Argo trigger fails, but the deploy job succeeded.
    result = run_gate(
        tmp_path,
        [argo_run("openhands-e2e-unstable-scheduled-2", "Succeeded", 10, 2)],
        deploy_conclusion="failure",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_passes_when_the_deploy_triggered_run_succeeded(tmp_path):
    result = run_gate(tmp_path, [argo_run(own(), "Succeeded", 29, 15)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{own()} passed on deploy b8b3e07a" in result.stdout
    assert "| E2E runs since deploy | 1 (0 running) |" in result.summary


def test_matches_the_deploy_triggered_run_by_name_despite_clock_skew(tmp_path):
    # Created 30s "before" the deploy job finished, as a skewed Argo clock could report.
    result = run_gate(tmp_path, [argo_run(own(), "Succeeded", 30.5, 15)], deployed_min_ago=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_passes_on_a_later_scheduled_run_after_the_triggered_one_failed(tmp_path):
    result = run_gate(
        tmp_path,
        [
            argo_run(own(), "Failed", 29, 20),
            argo_run("openhands-e2e-unstable-scheduled-2", "Succeeded", 10, 2),
        ],
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_blocks_when_the_latest_e2e_for_the_deploy_failed(tmp_path):
    result = run_gate(
        tmp_path,
        [
            argo_run("openhands-e2e-unstable-gh-4100-1", "Succeeded", 60, 50),
            argo_run(own(), "Failed", 29, 15),
        ],
    )
    assert result.returncode == 1
    assert "latest unstable E2E for deploy b8b3e07a finished Failed" in result.stdout


def test_ignores_other_instances(tmp_path):
    result = run_gate(tmp_path, [argo_run("openhands-e2e-beta-gh-9-1", "Succeeded", 10, 2)])
    assert result.returncode == 1
    assert "no unstable E2E has run since deploy" in result.stdout


def test_blocks_on_a_stale_result(tmp_path):
    result = run_gate(
        tmp_path,
        [argo_run(own(), "Succeeded", 22 * 60 - 1, 21 * 60)],
        deployed_min_ago=22 * 60,
    )
    assert result.returncode == 1
    assert "is stale (21h old, max 20h)" in result.stdout


def test_a_result_is_fresh_until_it_is_max_age_hours_old(tmp_path):
    # The age is whole hours, so a result 20h01m old counts as 20h and is still fresh.
    fresh = run_gate(
        tmp_path / "fresh",
        [argo_run(own(), "Succeeded", 21 * 60 - 1, 20 * 60 + 1)],
        deployed_min_ago=21 * 60,
    )
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    stale = run_gate(
        tmp_path / "stale",
        [argo_run(own(), "Succeeded", 22 * 60 - 1, 21 * 60 + 1)],
        deployed_min_ago=22 * 60,
    )
    assert stale.returncode == 1
    assert "is stale (21h old, max 20h)" in stale.stdout


def test_blocks_when_argo_is_unreachable(tmp_path):
    result = run_gate(tmp_path, [], argo_down=True)
    assert result.returncode == 1
    assert "could not reach Argo" in result.stdout


def test_override_applied_after_the_signal_passes_and_is_recorded(tmp_path):
    result = run_gate(
        tmp_path,
        [argo_run(own(), "Failed", 29, 15)],
        labels=["override-e2e-gate"],
        label_events=[("maintainer", ts(5))],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "E2E release gate overridden" in result.summary
    assert "by `maintainer` (`write`)" in result.comments


def test_override_applied_before_the_signal_is_rejected(tmp_path):
    result = run_gate(
        tmp_path,
        [argo_run(own(), "Failed", 29, 15)],
        labels=["override-e2e-gate"],
        label_events=[("maintainer", ts(20))],
    )
    assert result.returncode == 1
    assert "predates the signal" in result.stdout


def test_override_by_someone_without_write_access_is_rejected(tmp_path):
    result = run_gate(
        tmp_path,
        [argo_run(own(), "Failed", 29, 15)],
        labels=["override-e2e-gate"],
        label_events=[("outsider", ts(5))],
        permission="read",
    )
    assert result.returncode == 1
    assert "who lacks write permission (read)" in result.stdout


def test_override_applied_in_the_same_second_as_the_signal_is_rejected(tmp_path):
    failed = argo_run(own(), "Failed", 29, 15)
    result = run_gate(
        tmp_path,
        [failed],
        labels=["override-e2e-gate"],
        label_events=[("maintainer", failed["status"]["finishedAt"])],
    )
    assert result.returncode == 1
    assert "predates the signal" in result.stdout


def test_an_override_is_recorded_on_the_pr_only_once(tmp_path):
    override_when = ts(5)
    result = run_gate(
        tmp_path,
        [argo_run(own(), "Failed", 29, 15)],
        labels=["override-e2e-gate"],
        label_events=[("maintainer", override_when)],
        existing_comments=[f"<!-- release-gate-override:{override_when} -->"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.comments == ""


def test_override_cannot_carry_over_to_a_newer_deploy(tmp_path):
    # Applied while an older build was red; a newer deploy has landed since.
    result = run_gate(
        tmp_path,
        [argo_run(own(), "Running", 9)],
        deployed_min_ago=10,
        labels=["override-e2e-gate"],
        label_events=[("maintainer", ts(15))],
    )
    assert result.returncode == 1
    assert "predates the signal" in result.stdout


def test_gate_targets_the_workflow_that_deploys_unstable():
    gate = yaml.safe_load(WORKFLOW.read_text())
    unstable = yaml.safe_load(UNSTABLE_WORKFLOW.read_text())
    assert gate["env"]["DEPLOY_WORKFLOW"] == UNSTABLE_WORKFLOW.name
    # PyYAML reads the `on:` key as True.
    assert gate[True]["workflow_run"]["workflows"] == [unstable["name"]]
    # The gate finds the deploy job by its display prefix "deploy /".
    assert "deploy" in unstable["jobs"] and "name" not in unstable["jobs"]["deploy"]
    assert unstable["jobs"]["deploy"]["with"]["instance"] == gate["env"]["E2E_INSTANCE"]


def test_gate_matches_the_argo_run_name_the_deploy_submits():
    trigger = yaml.safe_load(E2E_WORKFLOW.read_text())["jobs"]["trigger-e2e"]["steps"][0]["run"]
    assert 'workflow="openhands-e2e-${INSTANCE}-gh-${RUN_ID}-${RUN_ATTEMPT}"' in trigger
    assert '"openhands-e2e-${E2E_INSTANCE}-gh-${deploy_id}-"' in gate_script()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
