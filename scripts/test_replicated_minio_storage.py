from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_replicated_minio_uses_openebs_hostpath_storage() -> None:
    chart = yaml.safe_load(
        (REPO_ROOT / "replicated" / "openhands.yaml").read_text(encoding="utf-8")
    )
    persistence = chart["spec"]["values"]["minio"]["persistence"]

    assert persistence["enabled"] is True
    assert persistence["storageClass"] == "openebs-hostpath"
