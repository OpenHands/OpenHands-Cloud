"""Render every runtime workload with tracing enabled and disabled."""

import subprocess
from pathlib import Path

import pytest
import yaml


CHART = Path(__file__).resolve().parents[1] / "charts/openhands/charts/runtime-api"


@pytest.mark.parametrize("enabled", [False, True])
def test_runtime_workloads_honor_datadog_setting(enabled: bool) -> None:
    result = subprocess.run(
        [
            "helm", "template", "runtime", str(CHART),
            "--set", f"datadog.enabled={str(enabled).lower()}",
            "--set", "warmRuntimes.enabled=true",
            "--set", "testKeysCleanup.enabled=true",
        ],
        check=True, capture_output=True, text=True,
    )
    workloads = {}
    for doc in yaml.safe_load_all(result.stdout):
        if not doc or doc["kind"] not in {"Deployment", "CronJob"}:
            continue
        spec = doc["spec"]
        if doc["kind"] == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        for container in spec["template"]["spec"]["containers"]:
            env = {item["name"]: item.get("value") for item in container["env"]}
            workloads[container["name"]] = env
            assert env["DD_TRACE_ENABLED"] == str(enabled).lower()
            if enabled:
                assert env["DD_AGENT_HOST"] == "datadog-agent.all-hands-system.svc.cluster.local"
                assert env.get("DD_INSTRUMENTATION_TELEMETRY_ENABLED") != "false"
            else:
                assert env["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] == "false"
                assert "DD_AGENT_HOST" not in env
    assert set(workloads) >= {
        "runtime-api", "cleanup-k8s-garbage", "cleanup-reaper",
        "cleanup-retention", "cleanup-snapshotter", "db-cleanup",
        "pod-status-logger", "warm-runtimes", "test-keys-cleanup",
    }
