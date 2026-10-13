#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest", "ruamel.yaml"]
# ///
"""Behavior checks for the Docker image scan workflow."""

import os
import subprocess
from datetime import date
from pathlib import Path

import pytest
from ruamel.yaml import YAML


WORKFLOW = Path(".github/workflows/scan-docker-images.yml")
TRIVY_IGNORE = Path(".trivyignore.yaml")


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
    assert ("job log has the full table" in summary) == (table_bytes > 900_000)


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


def test_trivy_ignore_entries_are_justified_and_expire():
    entries = _load(TRIVY_IGNORE)["vulnerabilities"]
    for entry in entries:
        assert entry["id"] and entry["statement"] and entry["expired_at"], entry
        assert entry["expired_at"] > date.today(), f"{entry['id']} has already expired"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
