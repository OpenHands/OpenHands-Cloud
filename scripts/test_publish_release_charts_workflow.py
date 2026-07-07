"""Workflow contract tests for stable chart publication."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-release-charts.yml"


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_openhands_release_dispatches_staging_bump_after_publish() -> None:
    text = workflow_text()

    assert "component: ${{ steps.parse.outputs.component }}" in text
    assert "version: ${{ steps.parse.outputs.version }}" in text
    assert "source-sha: ${{ steps.source.outputs.sha }}" in text

    assert "needs: publish" in text
    assert "if: ${{ needs.publish.outputs.component == 'openhands' }}" in text
    assert "environment: staging-chart-bump-dispatcher" in text
    assert "staging-chart-dispatch" not in text
    assert "STAGING_CHART_DISPATCHER_APP_ID" in text
    assert "STAGING_CHART_DISPATCHER_APP_PRIVATE_KEY" in text
    assert "permission-contents: write" in text

    assert "/repos/OpenHands/saas-deploy/dispatches" in text
    assert "event_type=bump-chart-to-staging" in text
    assert "client_payload[chart]=${COMPONENT}" in text
    assert "client_payload[version]=${VERSION}" in text
    assert "client_payload[environment]=staging" in text
    assert "client_payload[source-repo]=${SOURCE_REPO}" in text
    assert "client_payload[source-sha]=${SOURCE_SHA}" in text
