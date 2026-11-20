"""Behavior of the sandbox-upgrade-coordinator control loop.

The real coordinator.sh is rendered from the chart and run under /bin/sh. Only
the cluster boundary is faked: `kubectl` reads and writes one file per PDB, and
`sleep` ends the loop after a fixed number of ticks.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/infra"
NS = "openhands"

FAKE_KUBECTL = """#!/bin/sh
state="$FAKE_STATE"
case "$*" in
  *"get installations"*)
    [ "$(cat "$state/installation")" = UNREADABLE ] && exit 1
    cat "$state/installation" ;;
  *"get pdb -n $FAKE_NS -l "*)
    for f in "$state"/pdb/*; do [ -e "$f" ] && echo "$(basename "$f")"; done ;;
  *"get pdb -n $FAKE_NS "*"-o jsonpath"*)
    cat "$state/pdb/$5" ;;
  *"patch pdb -n $FAKE_NS "*)
    name="$5"
    while [ "$#" -gt 0 ]; do [ "$1" = "-p" ] && body="$2"; shift; done
    value=$(printf '%s' "$body" | sed 's/.*"maxUnavailable":"\\{0,1\\}\\([^"}]*\\)"\\{0,1\\}}.*/\\1/')
    printf '%s' "$value" > "$state/pdb/$name"
    echo "$name $value" >> "$state/patches" ;;
  *) echo "unexpected kubectl call: $*" >&2; exit 1 ;;
esac
"""

FAKE_SLEEP = """#!/bin/sh
n=$(cat "$FAKE_STATE/ticks_left")
if [ "$n" -le 0 ]; then kill -TERM "$PPID"; exit 0; fi
echo $((n - 1)) > "$FAKE_STATE/ticks_left"
next="$FAKE_STATE/installation.$n"
[ -e "$next" ] && mv "$next" "$FAKE_STATE/installation"
exit 0
"""


def render_script(workdir: Path) -> str:
    """Render the ConfigMap from a copy of the chart with its remote dependencies removed.

    The coordinator template needs none of them, and CI does not build them for charts/infra.
    """
    chart = workdir / "infra"
    shutil.copytree(CHART, chart)
    meta = chart / "Chart.yaml"
    meta.write_text(meta.read_text().split("dependencies:")[0])
    (chart / "Chart.lock").unlink(missing_ok=True)
    for template in (chart / "templates").iterdir():
        if template.name != "sandbox-upgrade-coordinator.yaml" and not template.name.startswith("_"):
            template.unlink()
    args = ["helm", "template", "infra", str(chart), "--set", "sandboxUpgradeCoordinator.enabled=true"]
    docs = list(yaml.safe_load_all(subprocess.run(args, check=True, capture_output=True, text=True).stdout))
    return next(d for d in docs if d["kind"] == "ConfigMap")["data"]["coordinator.sh"]


def run_coordinator(tmp_path: Path, pdbs: dict[str, str], installation_states: list[str],
                    idle: str | None) -> tuple[dict[str, str], list[str]]:
    """Run one coordinator tick per entry of installation_states."""
    state = tmp_path / "state"
    (state / "pdb").mkdir(parents=True)
    for name, value in pdbs.items():
        (state / "pdb" / name).write_text(value)
    (state / "installation").write_text(installation_states[0])
    for index, later in enumerate(installation_states[1:]):
        (state / f"installation.{len(installation_states) - 1 - index}").write_text(later)
    (state / "ticks_left").write_text(str(len(installation_states) - 1))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("kubectl", FAKE_KUBECTL), ("sleep", FAKE_SLEEP)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    coordinator = tmp_path / "coordinator.sh"
    coordinator.write_text(render_script(tmp_path))
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_STATE": str(state), "FAKE_NS": NS}
    if idle is not None:
        env["PDB_IDLE_MAX_UNAVAILABLE"] = idle
    result = subprocess.run(["/bin/sh", str(coordinator)], env=env, capture_output=True, text=True, timeout=30)
    assert "unexpected kubectl call" not in result.stderr, result.stderr
    final = {p.name: p.read_text() for p in (state / "pdb").iterdir()}
    patches = (state / "patches").read_text().splitlines() if (state / "patches").exists() else []
    return final, patches


def test_idle_coordinator_keeps_a_configured_budget_of_one(tmp_path: Path) -> None:
    final, patches = run_coordinator(tmp_path, {"runtime-a": "1", "runtime-b": "1"}, ["Installed"] * 3, idle="1")
    assert final == {"runtime-a": "1", "runtime-b": "1"}
    assert patches == []


def test_idle_coordinator_raises_stricter_budgets_to_the_configured_value(tmp_path: Path) -> None:
    final, patches = run_coordinator(tmp_path, {"runtime-a": "0", "runtime-b": "1"}, ["Installed"] * 2, idle="1")
    assert final == {"runtime-a": "1", "runtime-b": "1"}
    assert patches == ["runtime-a 1"]


def test_idle_coordinator_restores_zero_by_default(tmp_path: Path) -> None:
    final, _ = run_coordinator(tmp_path, {"runtime-a": "1", "runtime-b": "0"}, ["Installed"] * 2, idle="0")
    assert final == {"runtime-a": "0", "runtime-b": "0"}


@pytest.mark.parametrize("idle", ["0", "1", "3"])
def test_drain_relaxes_budgets_then_restores_the_configured_value(tmp_path: Path, idle: str) -> None:
    states = ["Installed", "Installing", "Installing", "Installed", "Installed"]
    final, patches = run_coordinator(tmp_path, {"runtime-a": idle}, states, idle=idle)
    assert final == {"runtime-a": idle}
    assert patches == ["runtime-a 100%", f"runtime-a {idle}"]


def test_drain_state_enqueued_also_relaxes_budgets(tmp_path: Path) -> None:
    final, patches = run_coordinator(tmp_path, {"runtime-a": "1"}, ["Enqueued", "Enqueued"], idle="1")
    assert final == {"runtime-a": "100%"}
    assert patches == ["runtime-a 100%"]


def test_unreadable_installation_leaves_budgets_untouched(tmp_path: Path) -> None:
    final, patches = run_coordinator(tmp_path, {"runtime-a": "7"}, ["UNREADABLE"] * 3, idle="1")
    assert final == {"runtime-a": "7"}
    assert patches == []


def test_coordinator_refuses_to_start_without_an_idle_budget(tmp_path: Path) -> None:
    final, patches = run_coordinator(tmp_path, {"runtime-a": "1"}, ["Installed"] * 2, idle=None)
    assert final == {"runtime-a": "1"}
    assert patches == []


def test_installer_gives_the_coordinator_and_runtime_api_the_same_option() -> None:
    def option_name(template: str) -> str:
        return re.fullmatch(r'(?:repl\{\{|\{\{repl)\s*ConfigOption "([a-z_]+)"\s*\}\}', template).group(1)

    coordinator = yaml.safe_load((ROOT / "replicated/infra-cert-manager.yaml").read_text())["spec"]["values"]
    runtime_api = yaml.safe_load((ROOT / "replicated/openhands.yaml").read_text())["spec"]["values"]
    assert option_name(coordinator["sandboxUpgradeCoordinator"]["idleMaxUnavailable"]) == option_name(
        runtime_api["runtime-api"]["env"]["RUNTIME_PDB_MAX_UNAVAILABLE"]
    )
