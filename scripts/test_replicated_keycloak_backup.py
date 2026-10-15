"""Replicated exposes the Keycloak pre-upgrade backup, on by default, and wires it to the chart."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def toggle() -> dict:
    config = yaml.safe_load((ROOT / "replicated/config.yaml").read_text())
    group = next(g for g in config["spec"]["groups"] if g["name"] == "upgrade_checks")
    return next(i for i in group["items"] if i["name"] == "keycloak_pre_upgrade_backup")


def test_toggle_is_on_by_default_under_upgrade_checks() -> None:
    item = toggle()

    assert item["type"] == "bool"
    assert item["default"] == "1"


def test_backup_failure_message_names_the_toggle_as_the_admin_console_shows_it() -> None:
    job = (ROOT / "charts/openhands/templates/keycloak-backup-job.yaml").read_text()

    assert f'turn off \\"{toggle()["title"]}\\"' in job


def test_airgap_backup_image_is_the_tag_the_online_install_runs() -> None:
    spec = yaml.safe_load((ROOT / "replicated/openhands.yaml").read_text())["spec"]
    online = spec["values"]["keycloak"]["waitForDb"]["image"]
    airgap = next(
        b for b in spec["optionalValues"] if "HasLocalRegistry" in b["when"]
    )["values"]["keycloak"]["waitForDb"]["image"]

    assert airgap.rsplit("/", 1)[1] == online.rsplit("/", 1)[1]


def test_toggle_drives_the_chart_value() -> None:
    values = yaml.safe_load((ROOT / "replicated/openhands.yaml").read_text())["spec"]["values"]

    assert values["keycloak"]["preUpgradeBackup"]["enabled"] == (
        'repl{{ ConfigOptionEquals "keycloak_pre_upgrade_backup" "1" }}'
    )
