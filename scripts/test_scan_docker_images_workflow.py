#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest", "ruamel.yaml"]
# ///
"""Behavior checks for the Docker image scan workflow."""

import json
import os
import shlex
import stat
import subprocess
import warnings
from datetime import date, timedelta
from pathlib import Path

import pytest
from ruamel.yaml import YAML


WORKFLOW = Path(".github/workflows/scan-docker-images.yml")
GATE_WORKFLOW = Path(".github/workflows/image-scan-gate.yml")
TRIVY_IGNORE = Path(".trivyignore.yaml")
RELEASE_BRANCH = "release-please--branches--main--components--openhands"


def _load(path: Path) -> dict:
    return YAML(typ="safe").load(path.read_text())


def _trivy_steps() -> list[dict]:
    steps = _load(WORKFLOW)["jobs"]["scan_docker_images"]["steps"]
    return [s for s in steps if s.get("uses", "").startswith("aquasecurity/trivy-action")]


def _line_number_containing(lines: list[str], text: str) -> int:
    for index, line in enumerate(lines):
        if text in line:
            return index
    raise AssertionError(f"{text!r} not found in {WORKFLOW}")


def test_scan_waits_for_the_matrix_image_manifest_before_running_trivy():
    lines = WORKFLOW.read_text().splitlines()
    checkout_index = _line_number_containing(lines, "- name: Checkout repository")
    wait_index = _line_number_containing(lines, "- name: Wait for image manifest")
    trivy_index = _line_number_containing(
        lines, "- name: Run Trivy vulnerability scanner"
    )

    assert checkout_index < wait_index < trivy_index
    wait_step = "\n".join(lines[wait_index:trivy_index])
    assert "IMAGE_REF: ${{ env.REGISTRY }}/${{ matrix.IMAGE }}" in wait_step
    assert 'python3 scripts/wait_for_image_manifest.py "$IMAGE_REF"' in wait_step


def test_there_is_one_trivy_step_and_it_is_pinned_to_a_commit():
    (step,) = _trivy_steps()
    ref = step["uses"].split("@", 1)[1]
    assert len(ref) == 40 and set(ref) <= set("0123456789abcdef"), step["uses"]


def test_scan_fails_the_job_only_when_enforced():
    (step,) = _trivy_steps()
    assert "if" not in step
    assert step["with"]["exit-code"] == "${{ inputs.enforce && '1' || '0' }}"
    assert step["with"]["severity"] == "${{ inputs.severity || 'CRITICAL,HIGH' }}"
    assert step["with"]["ignore-unfixed"] is True
    assert step["with"]["trivyignores"] == str(TRIVY_IGNORE)


def test_scan_results_are_written_to_the_job_summary_even_when_the_scan_fails():
    steps = _load(WORKFLOW)["jobs"]["scan_docker_images"]["steps"]
    names = [s["name"] for s in steps]
    scan, publish = steps[names.index("Run Trivy vulnerability scanner") :][:2]
    assert scan["with"]["output"] == "trivy-results.txt"
    assert publish["name"] == "Publish scan results"
    assert "!cancelled()" in publish["if"]
    assert '"$GITHUB_STEP_SUMMARY"' in publish["run"]


def test_one_failing_image_does_not_cancel_the_other_scans():
    job = _load(WORKFLOW)["jobs"]["scan_docker_images"]
    assert job["strategy"]["fail-fast"] is False


def test_workflow_is_callable_with_enforce_and_severity_inputs():
    inputs = _load(WORKFLOW)["on"]["workflow_call"]["inputs"]
    assert inputs["enforce"]["type"] == "boolean"
    assert inputs["enforce"]["default"] is False
    assert inputs["severity"]["type"] == "string"


def test_scan_prints_a_table():
    (step,) = _trivy_steps()
    assert step["with"]["format"] == "table"


def _publish(table_bytes: int, tmp_path: Path) -> tuple[str, str]:
    """Run the real publish script; return (job log output, job summary)."""
    steps = _load(WORKFLOW)["jobs"]["scan_docker_images"]["steps"]
    publish = next(s for s in steps if s["name"] == "Publish scan results")
    (tmp_path / "trivy-results.txt").write_text("x" * (table_bytes - 1) + "\n")
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["bash", "-c", publish["run"]],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env={"IMAGE": "img", "GITHUB_STEP_SUMMARY": str(summary), "PATH": os.environ["PATH"]},
    )
    return result.stdout, summary.read_text()


@pytest.mark.parametrize("table_bytes", [1_000, 3_000_000])
def test_job_summary_stays_under_the_github_size_limit(table_bytes, tmp_path):
    log, summary = _publish(table_bytes, tmp_path)
    assert len(log) == table_bytes
    assert len(summary.encode()) < 1024 * 1024


@pytest.mark.parametrize("table_bytes", [900_000, 900_001])
def test_summary_is_cut_only_above_the_limit(table_bytes, tmp_path):
    _, summary = _publish(table_bytes, tmp_path)
    notice = any("job log has the full table" in ln for ln in summary.splitlines())
    assert notice == (table_bytes > 900_000)


@pytest.mark.parametrize("table_bytes", [1_000, 3_000_000])
def test_summary_is_a_heading_and_a_closed_code_fence(table_bytes, tmp_path):
    _, summary = _publish(table_bytes, tmp_path)
    lines = summary.splitlines()
    assert lines[:2] == ["### img", "```"]
    assert lines.count("```") == 2


WARN_DAYS = 14


def _check_ignore_entries(entries: list[dict], today: date) -> None:
    for entry in entries:
        assert entry["id"] and entry["statement"] and entry["expired_at"], entry
        expired_at = entry["expired_at"]
        assert isinstance(expired_at, date), (
            f"{entry['id']}: expired_at must be an unquoted YYYY-MM-DD date"
        )
        assert expired_at > today, (
            f"{entry['id']} expired on {expired_at}; renew or remove it"
        )
        if expired_at <= today + timedelta(days=WARN_DAYS):
            warnings.warn(
                f"{entry['id']} in {TRIVY_IGNORE} expires on {expired_at}; "
                "renew or remove it",
                stacklevel=2,
            )


def test_trivy_ignore_entries_are_justified_and_expire():
    _check_ignore_entries(_load(TRIVY_IGNORE)["vulnerabilities"], date.today())


TODAY = date(2026, 6, 1)


def _entry(expired_at) -> dict:
    return {"id": "CVE-2026-0001", "statement": "Not reachable.", "expired_at": expired_at}


def test_ignore_entry_far_from_expiry_neither_warns_nor_fails():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _check_ignore_entries([_entry(TODAY + timedelta(days=365))], TODAY)


def test_ignore_entry_expiring_soon_warns_with_id_and_date_but_does_not_fail():
    expiry = TODAY + timedelta(days=7)
    with pytest.warns(UserWarning, match=rf"CVE-2026-0001.*{expiry.isoformat()}"):
        _check_ignore_entries([_entry(expiry)], TODAY)


@pytest.mark.parametrize(
    ("expired_at", "message"),
    [
        (TODAY - timedelta(days=1), "renew or remove it"),
        (TODAY, "renew or remove it"),
        ("2026-12-31", "unquoted YYYY-MM-DD date"),
    ],
)
def test_ignore_entry_that_has_expired_or_is_not_a_date_fails(expired_at, message):
    with pytest.raises(AssertionError, match=message):
        _check_ignore_entries([_entry(expired_at)], TODAY)


def test_gate_workflow_has_no_path_filter_and_scopes_jobs_to_the_release_pr():
    workflow = _load(GATE_WORKFLOW)
    assert "paths" not in (workflow["on"]["pull_request"] or {})
    assert workflow["jobs"]["scan"]["with"]["enforce"] is True
    for name in ("scan", "image-scan-gate"):
        condition = workflow["jobs"][name]["if"]
        assert f"head.ref == '{RELEASE_BRANCH}'" in condition
        assert "head.repo.full_name == github.repository" in condition


def test_release_branch_matches_the_release_please_config():
    config = json.loads(Path("release-please-config.openhands.json").read_text())
    component = config["packages"]["."]["component"]
    assert RELEASE_BRANCH == f"release-please--branches--main--components--{component}"
    for path in (GATE_WORKFLOW, Path(".github/workflows/release-gate.yml")):
        assert _load(path)["env"]["RELEASE_PR_BRANCH"] == RELEASE_BRANCH
        assert f"head.ref == '{RELEASE_BRANCH}'" in path.read_text()


def test_gate_job_has_the_required_context_name_and_waits_for_the_scan():
    job = _load(GATE_WORKFLOW)["jobs"]["image-scan-gate"]
    assert job["name"] == "image-scan-gate"
    assert job["needs"] == "scan"
    assert "always()" in job["if"]


TEST_SCRIPTS_WORKFLOW = Path(".github/workflows/test-scripts.yml")
OVERRIDE_LABEL = "override-image-scan-gate"
HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
HEAD_DATE = "2026-01-01T00:00:00Z"
LATER = "2026-01-02T00:00:00Z"
MARKER = f"<!-- image-scan-gate-override:{LATER} -->"

FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$FIXTURES/calls.log"
jq_filter=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  [ "${args[i]}" = "--jq" ] && jq_filter="${args[i+1]}"
done
emit() { if [ -n "$jq_filter" ]; then jq -r "$jq_filter" "$1"; else cat "$1"; fi; }
case "$*" in
  "pr comment"*) ;;
  *"--method POST"*"/rerun") exit "$(cat "$FIXTURES/rerun_exit")" ;;
  *"/issues/"*"/labels"*) emit "$FIXTURES/labels.json" ;;
  *"/issues/"*"/events"*) cat "$FIXTURES/events.json" ;;
  *"/issues/"*"/comments"*) cat "$FIXTURES/comments.json" ;;
  *"/commits/"*) emit "$FIXTURES/commit.json" ;;
  *"/collaborators/"*"/permission"*)
    user=$(sed -E 's#.*/collaborators/([^/]+)/permission.*#\\1#' <<<"$*")
    [ -e "$FIXTURES/permission_exit" ] && exit "$(cat "$FIXTURES/permission_exit")"
    emit "$FIXTURES/permission.$user.json" 2>/dev/null || emit "$FIXTURES/permission.json" ;;
  "api repos/OpenHands/OpenHands-Cloud/pulls?state=open&base=main&head=OpenHands:@BRANCH@")
    cat "$FIXTURES/pulls.json" ;;
  "api repos/OpenHands/OpenHands-Cloud/actions/workflows/image-scan-gate.yml/runs?event=pull_request&head_sha=@SHA@&per_page=1")
    cat "$FIXTURES/runs.json" ;;
  *) echo "unexpected gh call: $*" >&2; exit 99 ;;
esac
""".replace("@BRANCH@", RELEASE_BRANCH).replace("@SHA@", HEAD_SHA)


def _run_step(tmp_path, job, step_name, env, fixtures):
    workflow = _load(GATE_WORKFLOW)
    step = next(s for s in workflow["jobs"][job]["steps"] if s["name"] == step_name)
    script = tmp_path / "step.sh"
    script.write_text(step["run"])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    defaults = {"rerun_exit": "0", "labels": "", "events.json": "[]",
                "comments.json": "[]", "head_date": HEAD_DATE, "permission": "write",
                "pulls.json": "[]", "runs.json": '{"workflow_runs": []}'}
    for name, value in {**defaults, **fixtures}.items():
        if name == "labels":
            text = json.dumps([{"name": label} for label in value.split()])
            name = "labels.json"
        elif name == "head_date":
            text = json.dumps({"commit": {"committer": {"date": value}}})
            name = "commit.json"
        elif name.startswith("permission") and name != "permission_exit":
            text = json.dumps({"permission": value})
            name += ".json"
        else:
            text = str(value)
        (fixture_dir / name).write_text(text)
    summary = tmp_path / "summary.md"
    summary.touch()
    shell = workflow["defaults"]["run"]["shell"].format(script)
    result = subprocess.run(
        shlex.split(shell),
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FIXTURES": str(fixture_dir),
            "GITHUB_STEP_SUMMARY": str(summary),
            "OVERRIDE_LABEL": workflow["env"]["OVERRIDE_LABEL"],
            "RELEASE_PR_BRANCH": workflow["env"]["RELEASE_PR_BRANCH"],
            "REPO": "OpenHands/OpenHands-Cloud",
            **env,
        },
    )
    calls = fixture_dir / "calls.log"
    return result, calls.read_text() if calls.exists() else "", summary.read_text()


def _labeled(actor, when):
    return {"event": "labeled", "label": {"name": OVERRIDE_LABEL},
            "actor": {"login": actor}, "created_at": when}


def _gate(tmp_path, scan_result="failure", **fixtures):
    return _run_step(
        tmp_path, "image-scan-gate", "Evaluate image scan gate",
        {"GH_TOKEN": "x", "PR_NUMBER": "7", "HEAD_SHA": HEAD_SHA,
         "SCAN_RESULT": scan_result, "SEVERITY": "CRITICAL,HIGH",
         "RUN_URL": "https://example.invalid/run"},
        fixtures,
    )


def _overridden(**fixtures):
    return {"labels": OVERRIDE_LABEL, "events.json": json.dumps([[_labeled("alice", LATER)]]),
            **fixtures}


def test_gate_passes_without_looking_at_labels_when_the_scan_succeeds(tmp_path):
    result, calls, _ = _gate(tmp_path, scan_result="success")
    assert result.returncode == 0, result.stderr
    assert calls == ""


@pytest.mark.parametrize("scan_result", ["failure", "cancelled", "skipped"])
def test_gate_blocks_a_failed_scan_that_has_no_override(tmp_path, scan_result):
    result, calls, _ = _gate(tmp_path, scan_result=scan_result)
    assert result.returncode == 1
    assert f"::error::image scan {scan_result}" in result.stdout
    assert "pr comment" not in calls


def test_gate_honours_a_fresh_override_by_a_writer_and_records_it_once(tmp_path):
    result, calls, summary = _gate(tmp_path, **_overridden())
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.count("pr comment") == 1
    assert MARKER in calls
    assert "Image scan gate overridden" in summary


def test_gate_does_not_comment_again_when_the_override_is_already_recorded(tmp_path):
    comments = json.dumps([[{"body": f"overridden\n\n{MARKER}"}]])
    result, calls, _ = _gate(tmp_path, **_overridden(**{"comments.json": comments}))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pr comment" not in calls


def test_gate_rejects_an_override_applied_before_the_head_commit(tmp_path):
    result, _, _ = _gate(tmp_path, **_overridden(head_date="2026-01-03T00:00:00Z"))
    assert result.returncode == 1
    assert "predates head commit" in result.stdout


@pytest.mark.parametrize("head_date", [None, "", "not a date"])
def test_gate_blocks_when_the_head_commit_date_is_unusable(tmp_path, head_date):
    result, calls, _ = _gate(tmp_path, **_overridden(head_date=head_date))
    assert result.returncode != 0
    assert "Override honored" not in result.stdout
    assert "pr comment" not in calls


def test_gate_blocks_when_the_permission_lookup_fails(tmp_path):
    result, calls, _ = _gate(tmp_path, **_overridden(permission_exit="1"))
    assert result.returncode == 1
    assert "lacks write permission (none)" in result.stdout
    assert "pr comment" not in calls


@pytest.mark.parametrize("permission", ["read", "triage", "none"])
def test_gate_rejects_an_override_from_someone_without_write_access(tmp_path, permission):
    result, _, _ = _gate(tmp_path, **_overridden(permission=permission))
    assert result.returncode == 1
    assert "lacks write permission" in result.stdout


def test_gate_rejects_a_label_with_no_labeled_event(tmp_path):
    result, _, _ = _gate(tmp_path, labels=OVERRIDE_LABEL)
    assert result.returncode == 1
    assert "no event records who applied it" in result.stdout


def test_gate_judges_the_most_recent_application_of_the_label(tmp_path):
    events = json.dumps([[_labeled("alice", LATER)], [_labeled("mallory", "2026-01-04T00:00:00Z")]])
    result, _, _ = _gate(
        tmp_path,
        **_overridden(**{"events.json": events, "permission.alice": "write",
                         "permission.mallory": "read"}),
    )
    assert result.returncode == 1
    assert "applied by mallory" in result.stdout


def test_gate_ignores_other_labels(tmp_path):
    result, _, _ = _gate(tmp_path, **_overridden(labels="override-something-else"))
    assert result.returncode == 1


def _refresh(tmp_path, **fixtures):
    return _run_step(
        tmp_path, "refresh-image-scan-gate", "Rerun the open release PR gate",
        {"GH_TOKEN": "x"}, fixtures,
    )


def _pr(branch=RELEASE_BRANCH):
    return json.dumps([{"base": {"ref": "main"}, "head": {
        "ref": branch, "sha": HEAD_SHA, "repo": {"full_name": "OpenHands/OpenHands-Cloud"}}}])


def _runs(status, sha=HEAD_SHA):
    return json.dumps({"workflow_runs": [{"id": 42, "head_sha": sha, "status": status}]})


def test_refresh_reruns_the_completed_gate_run_for_the_release_pr_head(tmp_path):
    result, calls, _ = _refresh(tmp_path, **{"pulls.json": _pr(), "runs.json": _runs("completed")})
    assert result.returncode == 0, result.stderr
    assert "--method POST repos/OpenHands/OpenHands-Cloud/actions/runs/42/rerun" in calls


@pytest.mark.parametrize("fixtures", [
    {"pulls.json": _pr("some-other-branch"), "runs.json": _runs("completed")},
    {"pulls.json": _pr(), "runs.json": _runs("in_progress")},
    {"pulls.json": _pr(), "runs.json": _runs("completed", sha="f" * 40)},
])
def test_refresh_leaves_everything_else_alone(tmp_path, fixtures):
    result, calls, _ = _refresh(tmp_path, **fixtures)
    assert result.returncode == 0, result.stderr
    assert "/rerun" not in calls


def test_refresh_finds_the_release_pr_and_its_run_with_exact_server_side_filters(tmp_path):
    result, calls, _ = _refresh(tmp_path, **{"pulls.json": _pr(), "runs.json": _runs("completed")})
    assert result.returncode == 0, result.stderr
    assert "unexpected gh call" not in result.stderr
    assert calls.splitlines()[:2] == [
        "api repos/OpenHands/OpenHands-Cloud/pulls?state=open&base=main"
        f"&head=OpenHands:{RELEASE_BRANCH}",
        "api repos/OpenHands/OpenHands-Cloud/actions/workflows/image-scan-gate.yml/runs"
        f"?event=pull_request&head_sha={HEAD_SHA}&per_page=1",
    ]


def test_refresh_does_not_fail_when_the_rerun_is_refused(tmp_path):
    result, _, _ = _refresh(
        tmp_path, **{"pulls.json": _pr(), "runs.json": _runs("completed"), "rerun_exit": "1"})
    assert result.returncode == 0, result.stderr
    assert "could not be rerun" in result.stdout


def test_gate_workflow_triggers_cover_label_changes_and_the_schedule():
    on = _load(GATE_WORKFLOW)["on"]
    assert {"opened", "reopened", "synchronize", "labeled", "unlabeled"} <= set(
        on["pull_request"]["types"])
    assert on["schedule"]
    assert _load(GATE_WORKFLOW)["jobs"]["refresh-image-scan-gate"]["if"] == (
        "github.event_name == 'schedule'")


GATE_SEVERITY = "${{ vars.IMAGE_SCAN_GATE_SEVERITY || 'CRITICAL,HIGH' }}"


def test_gate_fails_on_high_and_critical_findings_unless_the_variable_overrides_it():
    jobs = _load(GATE_WORKFLOW)["jobs"]
    assert jobs["scan"]["with"]["severity"] == GATE_SEVERITY
    assert jobs["image-scan-gate"]["steps"][0]["env"]["SEVERITY"] == GATE_SEVERITY


def test_gate_summary_states_the_severity_it_gates_on(tmp_path):
    env = {"GH_TOKEN": "x", "PR_NUMBER": "7", "HEAD_SHA": HEAD_SHA, "SCAN_RESULT": "success",
           "RUN_URL": "https://example.invalid/run", "SEVERITY": "CRITICAL,HIGH"}
    _, _, summary = _run_step(tmp_path, "image-scan-gate", "Evaluate image scan gate", env, {})
    assert "| Severity | `CRITICAL,HIGH` |" in summary


def test_both_gate_jobs_are_limited_to_prs_into_main():
    jobs = _load(GATE_WORKFLOW)["jobs"]
    for name in ("scan", "image-scan-gate"):
        assert "base.ref == 'main'" in jobs[name]["if"], name


def test_gate_job_permissions_cover_what_each_job_calls():
    workflow = _load(GATE_WORKFLOW)
    jobs = workflow["jobs"]
    assert workflow["permissions"] == {}
    callee = _load(WORKFLOW)["jobs"]["scan_docker_images"]["permissions"]
    assert jobs["scan"]["permissions"] == callee
    assert jobs["image-scan-gate"]["permissions"]["pull-requests"] == "write"
    assert jobs["refresh-image-scan-gate"]["permissions"]["actions"] == "write"


def test_test_scripts_workflow_runs_when_the_gate_workflow_changes():
    paths = _load(TEST_SCRIPTS_WORKFLOW)["on"]["pull_request"]["paths"]
    assert str(GATE_WORKFLOW) in paths


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
