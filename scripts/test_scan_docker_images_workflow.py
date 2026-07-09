#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest"]
# ///
"""Behavior checks for the Docker image scan workflow."""

from pathlib import Path

import pytest


WORKFLOW = Path(".github/workflows/scan-docker-images.yml")


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
