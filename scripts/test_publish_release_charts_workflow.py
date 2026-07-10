"""Workflow contract tests for stable chart publication."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ruamel.yaml import YAML


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-release-charts.yml"
DOCS = REPO_ROOT / "docs" / "staging-chart-bumps.md"
DEVELOPMENT_DOCS = REPO_ROOT / "docs" / "development-chart-bumps.md"
README = REPO_ROOT / "README.md"
APP_DOCS = REPO_ROOT / "scripts" / "create_chart_bump_dispatcher" / "README.md"
DEV_JOB = "dispatch-development-chart-bump"
TOKEN_ACTION = (
    "actions/create-github-app-token@"
    "bcd2ba49218906704ab6c1aa796996da409d3eb1"
)
CHECKOUT_ACTION = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
HELM_ACTION = "azure/setup-helm@5119fcb9089d432beecbf79bb2c7915207344b78"
PUBLISH_ACTION = (
    "appany/helm-oci-chart-releaser@"
    "dd0551c15abe174eb57824ecde62e976091094da"
)


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def docs_text() -> str:
    return DOCS.read_text(encoding="utf-8")


def load_workflow() -> dict:
    return YAML(typ="safe").load(WORKFLOW.read_text(encoding="utf-8"))


def step_by_id(job: dict, step_id: str) -> dict:
    return next(step for step in job["steps"] if step.get("id") == step_id)


def step_by_name(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step["name"] == name)


def test_openhands_release_dispatches_staging_bump_after_publish() -> None:
    text = workflow_text()

    assert "component: ${{ steps.parse.outputs.component }}" in text
    assert "version: ${{ steps.parse.outputs.version }}" in text
    assert "source-sha: ${{ steps.source.outputs.sha }}" in text

    assert "needs: publish" in text
    assert "if: ${{ needs.publish.outputs.component == 'openhands' }}" in text
    assert "environment: staging-chart-bump-dispatcher" in text
    assert "staging-chart-dispatch" not in text
    assert "STAGING_CHART_BUMP_DISPATCHER_APP_ID" in text
    assert "STAGING_CHART_BUMP_DISPATCHER_APP_PRIVATE_KEY" in text
    assert "STAGING_CHART_DISPATCHER_APP_ID" not in text
    assert "STAGING_CHART_DISPATCHER_APP_PRIVATE_KEY" not in text
    assert (
        "uses: actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
        in text
    )
    assert "uses: actions/create-github-app-token@v3" not in text
    assert "permission-contents: write" in text

    assert "/repos/OpenHands/saas-deploy/dispatches" in text
    assert "event_type=bump-chart-to-staging" in text
    assert "client_payload[chart]=${COMPONENT}" in text
    assert "client_payload[version]=${VERSION}" in text
    assert "client_payload[environment]=staging" in text
    assert "client_payload[source-repo]=${SOURCE_REPO}" in text
    assert "client_payload[source-sha]=${SOURCE_SHA}" in text


def test_staging_chart_bump_docs_use_environment_secret_names() -> None:
    text = docs_text()

    assert "STAGING_CHART_BUMP_DISPATCHER_APP_ID" in text
    assert "STAGING_CHART_BUMP_DISPATCHER_APP_PRIVATE_KEY" in text
    assert "STAGING_CHART_DISPATCHER_APP_ID" not in text
    assert "STAGING_CHART_DISPATCHER_APP_PRIVATE_KEY" not in text


def test_development_dispatch_runs_only_after_openhands_publish() -> None:
    job = load_workflow()["jobs"][DEV_JOB]

    assert job["needs"] == "publish"
    condition = job["if"]
    assert "needs.publish.result == 'success'" in condition
    assert "needs.publish.outputs.component == 'openhands'" in condition
    assert "github.actor == 'openhands-release-bot[bot]'" in condition
    assert "github.actor_id == '290150379'" in condition
    assert job["environment"] == "dev-chart-bump-dispatcher"
    assert job["permissions"] == {"contents": "read"}
    assert job["timeout-minutes"] == 5


def test_development_dispatcher_token_is_environment_scoped_and_minimal() -> None:
    job = load_workflow()["jobs"][DEV_JOB]
    token = step_by_id(job, "dispatcher-token")

    assert token["uses"] == TOKEN_ACTION
    assert token["with"] == {
        "app-id": "${{ secrets.CHART_BUMP_DISPATCHER_APP_ID }}",
        "private-key": "${{ secrets.CHART_BUMP_DISPATCHER_APP_PRIVATE_KEY }}",
        "owner": "OpenHands",
        "repositories": "saas-deploy",
        "permission-contents": "write",
    }
    assert "STAGING_CHART_BUMP_DISPATCHER" not in str(job)
    assert len(job["steps"]) == 2
    assert "actions/checkout" not in str(job)
    assert "continue-on-error" not in job


def test_development_dispatch_payload_matches_receiver_contract(tmp_path: Path) -> None:
    job = load_workflow()["jobs"][DEV_JOB]
    dispatch = step_by_name(job, "Dispatch bump-chart-to-development")

    assert dispatch["shell"] == "bash"
    assert dispatch["env"] == {
        "GH_TOKEN": "${{ steps.dispatcher-token.outputs.token }}",
        "COMPONENT": "${{ needs.publish.outputs.component }}",
        "VERSION": "${{ needs.publish.outputs.version }}",
        "SOURCE_REPO": "${{ github.repository }}",
        "SOURCE_SHA": "${{ needs.publish.outputs.source-sha }}",
    }

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "gh-args"
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$@" > "$GH_CAPTURE"\n',
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{environment['PATH']}",
            "GH_CAPTURE": str(capture),
            "GH_TOKEN": "fake-local-token",
            "COMPONENT": "openhands",
            "VERSION": "0.20.0",
            "SOURCE_REPO": "OpenHands/OpenHands-Cloud",
            "SOURCE_SHA": "a" * 40,
        }
    )

    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-e",
            "-o",
            "pipefail",
            "-c",
            dispatch["run"],
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        "api",
        "--method",
        "POST",
        "/repos/OpenHands/saas-deploy/dispatches",
        "-f",
        "event_type=bump-chart-to-development",
        "-f",
        "client_payload[chart]=openhands",
        "-f",
        "client_payload[version]=0.20.0",
        "-f",
        "client_payload[environment]=development",
        "-f",
        "client_payload[source-repo]=OpenHands/OpenHands-Cloud",
        "-f",
        f"client_payload[source-sha]={'a' * 40}",
    ]


def test_development_and_staging_dispatches_are_independent_siblings() -> None:
    jobs = load_workflow()["jobs"]
    development = jobs[DEV_JOB]
    staging = jobs["dispatch-staging-chart-bump"]

    assert development["needs"] == staging["needs"] == "publish"
    assert development["environment"] == "dev-chart-bump-dispatcher"
    assert staging["environment"] == "staging-chart-bump-dispatcher"


def test_chart_publish_supply_chain_uses_immutable_action_revisions() -> None:
    publish = load_workflow()["jobs"]["publish"]
    actions = {step["uses"] for step in publish["steps"] if "uses" in step}

    assert CHECKOUT_ACTION in actions
    assert HELM_ACTION in actions
    assert PUBLISH_ACTION in actions
    assert all("@v" not in action for action in actions)


def test_development_sender_docs_match_live_app_and_environment() -> None:
    assert DEVELOPMENT_DOCS.is_file(), "development sender docs are missing"
    text = DEVELOPMENT_DOCS.read_text(encoding="utf-8")

    assert "dev-chart-bump-dispatcher" in text
    assert "CHART_BUMP_DISPATCHER_APP_ID" in text
    assert "CHART_BUMP_DISPATCHER_APP_PRIVATE_KEY" in text
    assert "bump-chart-to-development" in text
    assert "environment=development" in text
    assert "accepted the dispatch" in text
    assert "does not mean" in text
    assert "without starting a receiver run" in text
    assert "tag ruleset" in text.lower()
    assert "openhands-release-bot[bot]" in text
    assert "290150379" in text
    assert "docs/development-chart-bumps.md" in README.read_text(encoding="utf-8")

    app_docs = APP_DOCS.read_text(encoding="utf-8")
    assert "--app-name dev-chart-bump-dispatcher" in app_docs
    assert "saas-deploy-dev-chart-dispatcher-openhands" not in app_docs
