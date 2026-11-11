"""Missing host commands must be reported before Sysbox is configured."""

import subprocess
from pathlib import Path

import pytest
import yaml


CHECK = Path(__file__).resolve().parents[1] / "charts/infra/files/check-sysbox-host.sh"
TOOLS = ("rsync", "modprobe", "iptables", "fusermount3")


@pytest.mark.parametrize("missing", TOOLS)
def test_missing_host_command_is_actionable(tmp_path, missing):
    for tool in TOOLS:
        if tool != missing:
            binary = tmp_path / tool
            binary.write_text("#!/bin/sh\nexit 0\n")
            binary.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", str(CHECK)], env={"PATH": str(tmp_path)},
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert f"Missing host commands: {missing}." in result.stdout
    assert "SYSBOX_TOOLS=FAIL" in result.stdout
    assert "SYSBOX_TOOLS=OK" not in result.stdout
    assert "SYSBOX_CHECK_COMPLETE=OK" in result.stdout


def test_all_missing_commands_are_reported_together(tmp_path):
    result = subprocess.run(
        ["/bin/bash", str(CHECK)], env={"PATH": str(tmp_path)},
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "Missing host commands: rsync modprobe iptables fusermount3." in result.stdout
    assert "install rsync, kmod, iptables and fuse3" in result.stdout


def test_kots_supplies_credentials_before_application_secrets_exist():
    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((root / "replicated/infra-sysbox.yaml").read_text())
    sysbox = manifest["spec"]["values"]["sysbox"]
    assert sysbox["preflightPullSecretData"] == "{{repl LocalRegistryImagePullSecret }}"
