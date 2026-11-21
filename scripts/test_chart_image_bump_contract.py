#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest"]
# ///
"""Behavior checks for the reusable chart image-bump workflow."""

from pathlib import Path

import pytest


WORKFLOW = Path(".github/workflows/bump-image-tag.yml")


def _section(text: str, start: str, end: str) -> str:
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    return text[start_index:end_index]


def test_callers_can_opt_into_updating_a_metadata_scalar_with_the_image_tag():
    workflow = WORKFLOW.read_text()
    inputs = _section(workflow, "    inputs:\n", "    secrets:\n")

    assert "      metadata_file:\n" in inputs
    assert "        default: ''\n" in inputs
    assert "      metadata_path:\n" in inputs
    assert "        default: '.appVersion'\n" in inputs

    metadata_step = _section(
        workflow,
        "      - name: Bump related metadata\n",
        "      - name: Create or update pull request\n",
    )
    assert "if: ${{ inputs.metadata_file != '' }}" in metadata_step
    assert "METADATA_FILE: ${{ inputs.metadata_file }}" in metadata_step
    assert "METADATA_PATH: ${{ inputs.metadata_path }}" in metadata_step
    assert "NEW_TAG: ${{ inputs.tag }}" in metadata_step
    assert '--file "$METADATA_FILE"' in metadata_step
    assert '--path "$METADATA_PATH"' in metadata_step
    assert '--tag "$NEW_TAG"' in metadata_step


def test_metadata_updates_are_staged_with_the_primary_image_tag_update():
    workflow = WORKFLOW.read_text()
    create_pr_step = _section(
        workflow,
        "      - name: Create or update pull request\n",
        "      - name: Summary\n",
    )

    assert "add-paths: |" in create_pr_step
    assert "${{ inputs.chart_file }}" in create_pr_step
    assert "${{ inputs.metadata_file }}" in create_pr_step


def test_callers_can_sync_the_fusey_version_into_both_values_files():
    workflow = WORKFLOW.read_text()
    inputs = _section(workflow, "    inputs:\n", "    secrets:\n")
    fusey_input = _section(inputs, "      fusey_version:\n", "      base_branch:\n")
    assert "        default: ''\n" in fusey_input

    step = _section(
        workflow,
        "      - name: Bump fusey version\n",
        "      - name: Create or update pull request\n",
    )
    assert "if: ${{ inputs.fusey_version != '' }}" in step
    assert "FUSEY_VERSION: ${{ inputs.fusey_version }}" in step
    assert "charts/openhands/values.yaml" in step
    assert "charts/openhands/charts/runtime-api/values.yaml" in step
    assert "--path .global.fuseyImage.version" in step
    assert '--tag "$FUSEY_VERSION"' in step

    create_pr_step = _section(
        workflow, "      - name: Create or update pull request\n", "      - name: Summary\n"
    )
    assert "            charts/openhands/values.yaml\n" in create_pr_step
    assert "            charts/openhands/charts/runtime-api/values.yaml\n" in create_pr_step


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
