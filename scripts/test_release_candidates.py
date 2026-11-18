"""Contract tests for openhands release candidates and their promotion to stable."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ruamel.yaml import YAML


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
NOTES_SCRIPT = REPO_ROOT / "scripts" / "promotion_notes.sh"
RC_BODY = """## {version} (2026-11-01)

## What's Changed
### Features
* feat: thing in {version} by @someone in https://github.com/OpenHands/OpenHands-Cloud/pull/1
### Bug Fixes
* fix: other thing in {version} by @someone in https://github.com/OpenHands/OpenHands-Cloud/pull/2

**Full Changelog**: https://github.com/OpenHands/OpenHands-Cloud/compare/a...b
"""


def load(name: str) -> dict:
    return YAML(typ="safe").load((WORKFLOWS / name).read_text(encoding="utf-8"))


def triggers(workflow: dict) -> dict:
    # YAML 1.1 reads a bare `on:` key as the boolean True.
    return workflow.get("on", workflow.get(True))


def step(job: dict, name: str) -> dict:
    return next(s for s in job["steps"] if s.get("name") == name)


def fake_gh(tmp_path: Path, script: str) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text("#!/usr/bin/env bash\n" + script, encoding="utf-8")
    gh.chmod(0o755)
    return os.environ | {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_release_tags_route_rcs_to_beta_and_stable_to_the_stable_channel() -> None:
    beta = triggers(load("release-replicated-beta.yml"))
    stable_workflow = load("release-replicated-stable.yml")

    assert beta["push"]["tags"] == ["openhands/*-rc.*"]
    assert triggers(stable_workflow)["push"]["tags"] == ["openhands/*", "!openhands/*-*"]
    assert stable_workflow["env"]["STABLE_CHANNEL"] in {"StableTest", "Stable"}


def test_openhands_release_please_waits_for_promotion() -> None:
    jobs = load("release.yml")["jobs"]

    assert jobs["openhands"]["needs"] == "promote"
    assert "openhands" not in jobs["publish-alpha"]["needs"]
    charts = [c["name"] for c in jobs["publish-alpha"]["strategy"]["matrix"]["chart"]]
    assert "openhands" not in charts


def test_openhands_config_cuts_release_candidates() -> None:
    config = json.loads((REPO_ROOT / "release-please-config.openhands.json").read_text())
    package = config["packages"]["."]

    assert package["versioning"] == "prerelease"
    assert package["prerelease"] is True
    assert package["prerelease-type"] == "rc.1"


def test_promotion_notes_cover_rcs_since_the_previous_stable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base")
    git(repo, "tag", "openhands/0.93.0")
    for rc in ["0.94.0-rc.1", "0.94.0-rc.2", "0.94.0-rc.3", "0.94.0-rc.4"]:
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", rc)
        git(repo, "tag", f"openhands/{rc}")
    git(repo, "tag", "openhands-secrets/0.1.30")

    bodies = tmp_path / "bodies"
    bodies.mkdir()
    for rc in ["0.94.0-rc.1", "0.94.0-rc.2", "0.94.0-rc.3", "0.94.0-rc.4"]:
        (bodies / rc).write_text(RC_BODY.format(version=rc), encoding="utf-8")
    env = fake_gh(
        tmp_path,
        # gh release view openhands/<version> --json body --jq .body
        'cat "$BODIES/${3#openhands/}"\n',
    ) | {"BODIES": str(bodies), "GH_REPO": "OpenHands/OpenHands-Cloud"}

    result = subprocess.run(
        [str(NOTES_SCRIPT), "openhands/0.94.0-rc.3"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    notes = result.stdout
    assert notes.startswith("## 0.94.0 (")
    assert "Changes since openhands/0.93.0" in notes
    headings = [line for line in notes.splitlines() if line.startswith("### ")]
    assert headings == ["### 0.94.0-rc.3", "### 0.94.0-rc.2", "### 0.94.0-rc.1"]
    assert "#### Features" in notes
    assert "What's Changed" not in notes
    assert "rc.4" not in notes
    assert notes.rstrip().endswith(
        "**Full Changelog**: https://github.com/OpenHands/OpenHands-Cloud/compare/"
        "openhands/0.93.0...openhands/0.94.0"
    )


def run_find_promotion_pr(tmp_path: Path, pulls: list[dict]) -> tuple[dict, str]:
    find = step(load("release.yml")["jobs"]["promote"], "Find the promotion PR this push merged")
    pulls_file = tmp_path / "pulls.json"
    pulls_file.write_text(json.dumps(pulls), encoding="utf-8")
    output = tmp_path / "output"
    output.write_text("", encoding="utf-8")
    env = fake_gh(tmp_path, 'cat "$PULLS"\n') | {
        "PULLS": str(pulls_file),
        "GITHUB_OUTPUT": str(output),
        "RUNNER_TEMP": str(tmp_path),
        "REPO": "OpenHands/OpenHands-Cloud",
        "SHA": "a" * 40,
        "BRANCH": "main",
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", find["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    notes_file = tmp_path / "notes.md"
    return outputs, notes_file.read_text() if notes_file.exists() else ""


def promotion_pr(**overrides: object) -> dict:
    pr = {
        "number": 1600,
        "merged_at": "2026-11-01T00:00:00Z",
        "merge_commit_sha": "a" * 40,
        "base": {"ref": "main"},
        "head": {"ref": "release-promote/openhands/0.94.0-rc.3"},
        "user": {"login": "openhands-release-bot[bot]"},
        "labels": [{"name": "autorelease: openhands promote"}],
        "body": "Intro\n\n<!-- notes:start -->\n## 0.94.0 (2026-11-01)\n\nnotes\n<!-- notes:end -->\n",
    }
    return pr | overrides


def test_promote_job_reads_the_rc_and_notes_from_a_promotion_merge(tmp_path: Path) -> None:
    outputs, notes = run_find_promotion_pr(tmp_path, [promotion_pr()])

    assert outputs == {
        "found": "true",
        "number": "1600",
        "rc_tag": "openhands/0.94.0-rc.3",
        "version": "0.94.0",
        "stable_tag": "openhands/0.94.0",
    }
    assert notes == "## 0.94.0 (2026-11-01)\n\nnotes\n"


def test_promote_job_ignores_prs_that_are_not_promotions(tmp_path: Path) -> None:
    not_promotions = [
        promotion_pr(user={"login": "someone"}),
        promotion_pr(labels=[]),
        promotion_pr(merged_at=None),
        promotion_pr(base={"ref": "release/0.93"}),
        promotion_pr(head={"ref": "jl/release-promote/openhands/0.94.0-rc.3"}),
    ]
    for index, pr in enumerate(not_promotions):
        case = tmp_path / str(index)
        case.mkdir()
        outputs, _ = run_find_promotion_pr(case, [pr])
        assert outputs == {"found": "false"}, pr
