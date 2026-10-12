#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest", "ruamel.yaml"]
# ///
"""Behavior checks for the Docker image scan workflow."""

import os
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


def test_gate_job_has_the_required_context_name_and_waits_for_the_scan():
    job = _load(GATE_WORKFLOW)["jobs"]["image-scan-gate"]
    assert job["name"] == "image-scan-gate"
    assert job["needs"] == "scan"
    assert "always()" in job["if"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
