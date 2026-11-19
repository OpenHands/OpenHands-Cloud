import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/openhands/charts/runtime-api"
SETTING = "RUNTIME_PDB_MAX_UNAVAILABLE"


@pytest.mark.parametrize("configured", [None, 0, 1, "0", "3"])
@pytest.mark.parametrize("umbrella", [False, True], ids=["subchart", "umbrella"])
def test_budget_setting_reaches_api_and_runtime_jobs_once(configured, umbrella) -> None:
    chart = ROOT / "charts/openhands" if umbrella else CHART
    prefix = "runtime-api." if umbrella else ""
    args = [
        "helm", "template", "runtime", str(chart),
        "--set", f"{prefix}warmRuntimes.enabled=true",
        "--set", f"{prefix}testKeysCleanup.enabled=true",
    ]
    if umbrella:
        args += [
            "-f", str(chart / "tests/fixtures/legacy-policy.yaml"),
            "--set", "runtime-api.enabled=true",
        ]
    if configured is not None:
        args += ["--set", f"{prefix}env.{SETTING}={configured}"]
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    workloads = set()
    for doc in yaml.safe_load_all(result.stdout):
        if not doc or doc["kind"] not in {"Deployment", "CronJob", "Job"}:
            continue
        if doc["metadata"].get("labels", {}).get("app.kubernetes.io/name") != "runtime-api":
            continue
        spec = doc["spec"]
        if doc["kind"] == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        for container in spec["template"]["spec"]["containers"]:
            entries = [item for item in container.get("env", []) if item["name"] == SETTING]
            assert entries == [{"name": SETTING, "value": str(configured if configured is not None else 0)}]
            workloads.add(container["name"])
    assert workloads >= {"runtime-api", "warm-runtimes", "cleanup-reaper", "db-cleanup"}


