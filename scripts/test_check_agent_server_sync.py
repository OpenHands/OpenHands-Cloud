#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyYAML", "pytest"]
# ///
"""Behavior tests for the agent-server / software-agent-sdk pin sync check."""

import json
import sys
import urllib.error
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))

import check_agent_server_sync as sync
from check_agent_server_sync import AGENT_SERVER_PINS, ENTERPRISE_SERVER_PIN, CheckError

REPO = "OpenHands/enterprise"
WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/check-agent-server-sync.yml"

# Trimmed to the shape the check depends on: the PEP 621 pin list it reads, and
# the mirrored poetry block it must ignore.
PYPROJECT = """\
[project]
name = "openhands"
dependencies = [
  "litellm==1.94.0",
  "openhands-agent-server=={agent_server}",
  "openhands-sdk=={sdk}",
  "openhands-tools=={tools}",
  "pathspec>=0.12.1",
]

[tool.poetry.dependencies]
openhands-sdk = "=={poetry}"
openhands-agent-server = "=={poetry}"
openhands-tools = "=={poetry}"
"""


def pyproject(agent_server="1.43.1", sdk=None, tools=None, poetry="9.9.9"):
    return PYPROJECT.format(
        agent_server=agent_server,
        sdk=sdk or agent_server,
        tools=tools or agent_server,
        poetry=poetry,
    )


def write_charts(root: Path, enterprise_tag: str, agent_server_tag: str):
    """Lay down the real pin paths, each in its own file, with real siblings.

    The siblings matter: a check that walked to the wrong key would still find
    a plausible tag, so every file carries a decoy `tag:` at another path.
    """
    documents: dict[Path, dict] = {}
    for pin in (ENTERPRISE_SERVER_PIN, *AGENT_SERVER_PINS):
        tag = enterprise_tag if pin == ENTERPRISE_SERVER_PIN else agent_server_tag
        node = documents.setdefault(pin.path, {"decoy": {"image": {"tag": "0.0.1-decoy"}}})
        for key in pin.keys[:-1]:
            node = node.setdefault(key, {})
        node[pin.keys[-1]] = tag

    for path, document in documents.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(yaml.safe_dump(document))
    return root


@pytest.fixture
def fake_remote(monkeypatch):
    """Stand in for the enterprise remote. Returns a mutable route table."""
    routes = {
        "git/ref/tags/1.56.0": {"object": {"type": "commit", "sha": "a" * 40}},
        f"contents/pyproject.toml?ref={'a' * 40}": pyproject(),
    }
    calls = []

    def http_get(url, token, accept, missing=None):
        assert url.startswith(f"{sync.API_ROOT}/repos/{REPO}/")
        route = url[len(f"{sync.API_ROOT}/repos/{REPO}/") :]
        calls.append(route)
        if route not in routes:
            if missing:
                raise CheckError(missing)
            raise CheckError(f"GET {url} failed: HTTP 404 Not Found")
        value = routes[route]
        return json.dumps(value).encode() if isinstance(value, dict) else value.encode()

    monkeypatch.setattr(sync, "http_get", http_get)
    routes["calls"] = calls  # exposed for assertions on which endpoints were hit
    return routes


def run_check(root, repo=REPO):
    return sync.check(root, repo, token=None)


def test_matching_pins_pass(tmp_path, fake_remote):
    write_charts(tmp_path, "1.56.0", "1.43.1-python")

    report = "\n".join(run_check(tmp_path))

    assert "1.56.0" in report
    assert "1.43.1" in report


def test_mismatched_pins_fail_and_name_both_versions(tmp_path, fake_remote):
    write_charts(tmp_path, "1.56.0", "1.41.0-python")

    with pytest.raises(CheckError) as error:
        run_check(tmp_path)

    message = str(error.value)
    assert "1.41.0" in message
    assert "1.43.1" in message
    # The fix has to be actionable: name the tag to set and every file holding one.
    assert "1.43.1-python" in message
    for pin in AGENT_SERVER_PINS:
        assert str(pin.path) in message


def test_variant_suffix_is_not_compared(tmp_path, fake_remote):
    """The image is published per language runtime off one SDK release."""
    write_charts(tmp_path, "1.56.0", "1.43.1-golang")

    assert run_check(tmp_path)  # does not raise


def test_bare_agent_server_tag_without_a_variant_passes(tmp_path, fake_remote):
    write_charts(tmp_path, "1.56.0", "1.43.1")

    assert run_check(tmp_path)


def test_agent_server_pins_must_agree_with_each_other(tmp_path, fake_remote):
    write_charts(tmp_path, "1.56.0", "1.43.1-python")
    drifted = AGENT_SERVER_PINS[-1]
    document = yaml.safe_load((tmp_path / drifted.path).read_text())
    document["image"]["tag"] = "1.42.0-python"
    (tmp_path / drifted.path).write_text(yaml.safe_dump(document))

    with pytest.raises(CheckError, match="disagree with each other"):
        run_check(tmp_path)


def test_enterprise_sdk_pins_must_agree_with_each_other(tmp_path, fake_remote):
    fake_remote[f"contents/pyproject.toml?ref={'a' * 40}"] = pyproject(
        agent_server="1.43.1", sdk="1.42.0"
    )
    write_charts(tmp_path, "1.56.0", "1.43.1-python")

    with pytest.raises(CheckError, match="disagree with each other"):
        run_check(tmp_path)


def test_poetry_dependency_block_is_ignored(tmp_path, fake_remote):
    """[project].dependencies is the pin uv installs from; poetry's is a mirror."""
    fake_remote[f"contents/pyproject.toml?ref={'a' * 40}"] = pyproject(poetry="1.20.0")
    write_charts(tmp_path, "1.56.0", "1.43.1-python")

    assert run_check(tmp_path)


def test_sha_prefixed_enterprise_pin_resolves_the_commit(tmp_path, fake_remote):
    fake_remote["commits/2809208"] = {"sha": "b" * 40}
    fake_remote[f"contents/pyproject.toml?ref={'b' * 40}"] = pyproject()
    write_charts(tmp_path, "sha-2809208", "1.43.1-python")

    report = "\n".join(run_check(tmp_path))

    assert "commits/2809208" in fake_remote["calls"]
    assert "commit 2809208" in report


def test_annotated_enterprise_tag_is_dereferenced(tmp_path, fake_remote):
    fake_remote["git/ref/tags/1.56.0"] = {"object": {"type": "tag", "sha": "c" * 40}}
    fake_remote[f"git/tags/{'c' * 40}"] = {"object": {"sha": "d" * 40}}
    fake_remote[f"contents/pyproject.toml?ref={'d' * 40}"] = pyproject()
    write_charts(tmp_path, "1.56.0", "1.43.1-python")

    assert run_check(tmp_path)


def test_missing_enterprise_tag_names_the_pin_that_is_wrong(tmp_path, fake_remote):
    write_charts(tmp_path, "9.9.9", "1.43.1-python")

    with pytest.raises(CheckError) as error:
        run_check(tmp_path)

    message = str(error.value)
    assert "no tag 9.9.9" in message
    assert str(ENTERPRISE_SERVER_PIN.path) in message


def test_enterprise_pin_without_an_exact_sdk_pin_fails(tmp_path, fake_remote):
    fake_remote[f"contents/pyproject.toml?ref={'a' * 40}"] = (
        '[project]\ndependencies = ["openhands-sdk>=1.43.1"]\n'
    )
    write_charts(tmp_path, "1.56.0", "1.43.1-python")

    with pytest.raises(CheckError, match="no exact version"):
        run_check(tmp_path)


def test_a_missing_chart_pin_fails_loudly(tmp_path, fake_remote):
    write_charts(tmp_path, "1.56.0", "1.43.1-python")
    dropped = AGENT_SERVER_PINS[0]
    document = yaml.safe_load((tmp_path / dropped.path).read_text())
    del document[dropped.keys[0]]
    (tmp_path / dropped.path).write_text(yaml.safe_dump(document))

    with pytest.raises(CheckError, match="no value at"):
        run_check(tmp_path)


def test_an_unquoted_yaml_number_is_rejected(tmp_path, fake_remote):
    """`tag: 1.10` parses as the float 1.1, which would compare as 1.1."""
    write_charts(tmp_path, "1.56.0", "1.43.1-python")
    # image-loader's pin is the one that owns its whole file.
    (tmp_path / "charts/image-loader/values.yaml").write_text("image:\n  tag: 1.10\n")

    with pytest.raises(CheckError, match="expected a quoted string"):
        run_check(tmp_path)


def test_http_errors_are_reported_not_swallowed(monkeypatch):
    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 500, "Server Error", {}, None)

    monkeypatch.setattr(sync.urllib.request, "urlopen", urlopen)

    with pytest.raises(CheckError, match="HTTP 500"):
        sync.http_get("https://example.invalid/x", None, "application/json")


def test_pins_still_resolve_against_the_real_charts():
    """Guards the pin table against a values.yaml restructure. No network."""
    repo_root = Path(__file__).resolve().parents[1]

    for pin in (ENTERPRISE_SERVER_PIN, *AGENT_SERVER_PINS):
        assert sync.read_pin(repo_root, pin), f"{pin} resolved to an empty value"


def test_workflow_runs_on_every_release_please_pr():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    # PyYAML parses a bare `on:` key as the boolean True.
    triggers = workflow[True]

    # A path filter would skip release PRs for charts outside charts/openhands,
    # and would leave the check pending forever if it were made required.
    assert "paths" not in triggers["pull_request"]
    assert "paths-ignore" not in triggers["pull_request"]

    condition = workflow["jobs"]["check-agent-server-sync"]["if"]
    assert "startsWith(github.head_ref, 'release-please--')" in condition


def test_workflow_fails_the_job_on_a_mismatch():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    job = workflow["jobs"]["check-agent-server-sync"]
    step = job["steps"][-1]

    assert job["runs-on"] == "ubuntu-24.04"
    assert "scripts/check_agent_server_sync.py" in step["run"]
    # The report is captured so it can reach the step summary; the script's exit
    # status still has to decide the job.
    assert 'exit "$status"' in step["run"]
    assert "continue-on-error" not in step


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
