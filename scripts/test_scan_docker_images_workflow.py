#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest", "pyyaml"]
# ///
"""Behavior checks for the Docker image scan workflow."""

from pathlib import Path

import pytest
import yaml


WORKFLOW = Path(".github/workflows/scan-docker-images.yml")


def _scan_steps():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    return workflow["jobs"]["scan_docker_images"]["steps"]


def test_scan_waits_for_the_matrix_image_manifest_before_running_trivy():
    steps = _scan_steps()
    names = [step["name"] for step in steps]

    checkout_index = names.index("Checkout repository")
    wait_index = names.index("Wait for image manifest")
    trivy_index = names.index("Run Trivy vulnerability scanner")

    assert checkout_index < wait_index
    assert wait_index < trivy_index
    wait_step = steps[wait_index]
    assert wait_step["env"]["IMAGE_REF"] == "${{ env.REGISTRY }}/${{ matrix.IMAGE }}"
    assert "python3 scripts/wait_for_image_manifest.py \"$IMAGE_REF\"" in wait_step["run"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
