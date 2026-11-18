#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest", "PyYAML"]
# ///
"""Runs the deploy gate's real shell step against a fake `gh` that lists no Beta run for the first N queries."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(".github/workflows/deploy-gate.yml")
STEP_NAME = "Assert both lanes deployed their newest trigger"
WAIT_ATTEMPTS = 4

FAKE_GH = r"""#!/usr/bin/env bash
url=$2
case "$url" in
  */contents/*) echo "on: {}" ;;
  */commits\?*)
    echo '[{"commit":{"committer":{"date":"2026-01-01T00:00:00Z"}},"sha":"abcdef0123456789"}]' | jq -r "$4" ;;
  */releases\?*)
    echo '[{"tag_name":"openhands/0.111.0"}]' | jq -r "$4" ;;
  */release-replicated-beta.yml/runs\?branch=openhands/0.111.0)
    echo x >> "$BETA_CALLS"
    if [ "$(wc -l < "$BETA_CALLS")" -le "$BETA_EMPTY_QUERIES" ]; then
      echo '{"total_count":0,"workflow_runs":[]}'
    else
      echo '{"total_count":1,"workflow_runs":[{"id":1,"status":"completed","conclusion":"success","html_url":"https://example/run/1"}]}'
    fi ;;
  */release-replicated-unstable.yml/runs\?*)
    echo '{"total_count":1,"workflow_runs":[{"id":2,"status":"completed","conclusion":"success","html_url":"https://example/run/2"}]}' ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
"""

FAKE_YQ = "#!/usr/bin/env bash\necho replicated\n"


def _gate_script() -> str:
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["last-deploy"]["steps"]
    return next(s["run"] for s in steps if s.get("name") == STEP_NAME)


def _run_gate(tmp_path: Path, beta_empty_queries: int):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("gh", FAKE_GH), ("yq", FAKE_YQ)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    beta_calls = tmp_path / "beta_calls"
    beta_calls.touch()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "REPO": "OpenHands/OpenHands-Cloud",
        "PR": "1",
        "OVERRIDE_LABEL": "deploy-gate-override",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "BETA_CALLS": str(beta_calls),
        "BETA_EMPTY_QUERIES": str(beta_empty_queries),
        "WAIT_ATTEMPTS": str(WAIT_ATTEMPTS),
        "WAIT_INTERVAL": "0",
    }
    result = subprocess.run(
        ["bash", "-e", "-c", _gate_script()],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, len(beta_calls.read_text().splitlines())


def test_gate_passes_when_beta_run_appears_after_a_delay(tmp_path):
    result, beta_queries = _run_gate(tmp_path, beta_empty_queries=2)

    assert result.returncode == 0, result.stdout + result.stderr
    assert beta_queries == 3
    assert "beta: ok" in result.stdout


def test_gate_does_not_wait_when_beta_run_is_already_listed(tmp_path):
    result, beta_queries = _run_gate(tmp_path, beta_empty_queries=0)

    assert result.returncode == 0, result.stdout + result.stderr
    assert beta_queries == 1


def test_gate_fails_after_bounded_wait_when_beta_run_never_appears(tmp_path):
    result, beta_queries = _run_gate(tmp_path, beta_empty_queries=10**6)

    assert result.returncode == 1
    assert beta_queries == WAIT_ATTEMPTS
    assert (
        "::error::beta: no run of release-replicated-beta.yml appeared "
        f"for tag `openhands/0.111.0` after {WAIT_ATTEMPTS} queries"
    ) in result.stdout.splitlines()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
