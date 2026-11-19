import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SETTING = "RUNTIME_PDB_MAX_UNAVAILABLE"
OPTION = "sandbox_pdb_max_unavailable"


@pytest.fixture(scope="module")
def option() -> dict:
    groups = yaml.safe_load((ROOT / "replicated/config.yaml").read_text())["spec"]["groups"]
    group, item = {i["name"]: (g["name"], i) for g in groups for i in g["items"]}[OPTION]
    assert group == "sandbox_configuration"
    return item


def test_installer_preserves_the_zero_default(option: dict) -> None:
    assert option["type"] == "text"
    assert option["default"] == "0"
    assert "value" not in option
    assert "when" not in option


@pytest.mark.parametrize("value", ["0", "1", "3", "10"])
def test_installer_accepts_nonnegative_integers(option: dict, value: str) -> None:
    assert re.search(option["validation"]["regex"]["pattern"], value)


@pytest.mark.parametrize("value", ["", "-1", "1.5", "50%", "true", " 1", "1 "])
def test_installer_rejects_invalid_limits(option: dict, value: str) -> None:
    assert not re.search(option["validation"]["regex"]["pattern"], value)


def test_installer_passes_its_value_to_runtime_api() -> None:
    spec = yaml.safe_load((ROOT / "replicated/openhands.yaml").read_text())["spec"]
    assert spec["values"]["runtime-api"]["env"][SETTING] == f'repl{{{{ ConfigOption "{OPTION}" }}}}'
    for entry in spec["optionalValues"]:
        assert SETTING not in entry["values"].get("runtime-api", {}).get("env", {})
