import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

SCRIPT = Path(__file__).parents[1] / "charts/openhands/files/budget_upgrade.py"
spec = importlib.util.spec_from_file_location("budget_upgrade", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
IMAGE = "ghcr.io/openhands/enterprise-server:sha-candidate"
OLD = "ghcr.io/openhands/enterprise-server:1.56.0"


def pod_spec(image=IMAGE, init_image=IMAGE):
    return NS(
        containers=[NS(name="app", image=image)],
        init_containers=[NS(name="migrate-db", image=init_image)],
    )


def cron(name, suspended):
    return NS(
        metadata=NS(name=name),
        spec=NS(
            suspend=suspended, job_template=NS(spec=NS(template=NS(spec=pod_spec())))
        ),
    )


@pytest.fixture
def upgrade():
    core, apps, batch = MagicMock(), MagicMock(), MagicMock()
    core.list_namespaced_pod.return_value.items = []
    apps.list_namespaced_deployment.return_value.items = []
    batch.list_namespaced_job.return_value.items = []
    batch.list_namespaced_cron_job.return_value.items = [
        cron("openhands-budget-maintenance", True),
        cron("openhands-maintenance-tasks", True),
    ]
    state = {
        "phase": "quiesced",
        "deployments": {},
        "cronjobs": {
            "openhands-budget-maintenance": True,
            "openhands-maintenance-tasks": True,
        },
    }
    core.read_namespaced_config_map.return_value.data = {"state": json.dumps(state)}
    return module.Upgrade(core, apps, batch, "openhands", "openhands", IMAGE, "")


def test_old_init_container_prevents_all_unsuspension(upgrade):
    upgrade.apps.list_namespaced_deployment.return_value.items = [
        NS(
            metadata=NS(name="openhands"),
            spec=NS(template=NS(spec=pod_spec(init_image=OLD))),
        )
    ]
    with pytest.raises(RuntimeError, match="migrate-db"):
        upgrade.resume()
    upgrade.batch.patch_namespaced_cron_job.assert_not_called()
    upgrade.core.patch_namespaced_config_map.assert_not_called()


def test_resumes_budget_but_preserves_other_operator_suspension(upgrade):
    upgrade.resume()
    calls = upgrade.batch.patch_namespaced_cron_job.call_args_list
    assert [(c.args[0], c.args[2]["spec"]["suspend"]) for c in calls] == [
        ("openhands-budget-maintenance", False),
        ("openhands-maintenance-tasks", True),
    ]


def test_failed_resume_keeps_original_checkpoint_for_retry(upgrade):
    upgrade.batch.patch_namespaced_cron_job.side_effect = [
        None,
        RuntimeError("API unavailable"),
    ]
    with pytest.raises(RuntimeError, match="API unavailable"):
        upgrade.resume()
    upgrade.core.patch_namespaced_config_map.assert_not_called()
    upgrade.batch.patch_namespaced_cron_job.side_effect = None
    upgrade.resume()
    saved = json.loads(
        upgrade.core.patch_namespaced_config_map.call_args.args[2]["data"]["state"]
    )
    assert saved["phase"] == "complete"
    assert saved["cronjobs"]["openhands-maintenance-tasks"] is True


def test_quiescence_retry_does_not_replace_original_suspension(upgrade):
    upgrade.quiesce()
    upgrade.core.create_namespaced_config_map.assert_not_called()
    saved = json.loads(
        upgrade.core.patch_namespaced_config_map.call_args.args[2]["data"]["state"]
    )
    assert saved["phase"] == "quiesced"
    assert saved["cronjobs"]["openhands-maintenance-tasks"] is True


def test_old_running_job_pod_prevents_resume(upgrade):
    upgrade.core.list_namespaced_pod.return_value.items = [
        NS(
            metadata=NS(name="legacy-worker"),
            status=NS(phase="Running"),
            spec=pod_spec(OLD),
        )
    ]
    with pytest.raises(RuntimeError, match="legacy-worker"):
        upgrade.resume()
    upgrade.batch.patch_namespaced_cron_job.assert_not_called()
