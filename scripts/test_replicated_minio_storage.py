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


def _storage_config(name="minio_storage_size") -> dict:
    config = yaml.safe_load((REPO_ROOT / "replicated/config.yaml").read_text())
    return next(
        item
        for group in config["spec"]["groups"]
        for item in group.get("items", [])
        if item["name"] == name
    )


def _render_storage_settings(tmp_path, claim_size=None, selected_size="") -> dict:
    """Run source Go templates with cluster/config lookups replaced by fixtures."""
    import subprocess

    chart = yaml.safe_load((REPO_ROOT / "replicated/openhands.yaml").read_text())
    config = _storage_config()
    expressions = {
        "size": chart["spec"]["values"]["minio"]["persistence"]["size"],
        "new_install": config["when"],
        "installed_size": _storage_config("minio_existing_storage_size")["default"],
        "existing_install": _storage_config("minio_existing_storage_size")["when"],
    }
    lookup = 'Lookup "v1" "PersistentVolumeClaim" "openhands" "openhands-minio"'
    rendered = []
    for key, expression in expressions.items():
        assert lookup in expression
        expression = expression.replace("{{repl", "{{").replace("repl{{", "{{")
        expression = expression.replace(lookup, ".Values.claim")
        expression = expression.replace(
            'ConfigOption "minio_storage_size"', ".Values.selected"
        )
        rendered.append(f"  {key}: '{expression}'")
    (tmp_path / "Chart.yaml").write_text(
        "apiVersion: v2\nname: storage-test\nversion: 0.1.0\n"
    )
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates/settings.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: storage-test\ndata:\n"
        + "\n".join(rendered)
        + "\n"
    )
    claim = (
        {"spec": {"resources": {"requests": {"storage": claim_size}}}}
        if claim_size
        else {}
    )
    result = subprocess.run(
        ["helm", "template", "storage-test", str(tmp_path), "--values", "-"],
        input=yaml.safe_dump({"claim": claim, "selected": selected_size}),
        text=True,
        capture_output=True,
        check=True,
    )
    return yaml.safe_load(result.stdout)["data"]


def test_new_install_uses_smaller_default(tmp_path) -> None:
    assert _render_storage_settings(tmp_path) == {
        "size": "100Gi",
        "new_install": "true",
        "installed_size": "",
        "existing_install": "false",
    }


def test_new_install_accepts_selected_size(tmp_path) -> None:
    assert _render_storage_settings(tmp_path, selected_size="250Gi")["size"] == "250Gi"


def test_upgrade_preserves_existing_500gi_claim(tmp_path) -> None:
    assert _render_storage_settings(tmp_path, "500Gi", "100Gi") == {
        "size": "500Gi",
        "new_install": "false",
        "installed_size": "500Gi",
        "existing_install": "true",
    }


def test_upgrade_preserves_custom_claim_without_expanding(tmp_path) -> None:
    assert _render_storage_settings(tmp_path, "50Gi", "1Ti")["size"] == "50Gi"


def test_upgrade_preserves_claim_when_new_config_is_missing(tmp_path) -> None:
    assert _render_storage_settings(tmp_path, "200Gi")["size"] == "200Gi"


def test_size_validation_rejects_empty_zero_negative_and_unitless_values() -> None:
    import re

    pattern = _storage_config()["validation"]["regex"]["pattern"]
    for value in ["100Gi", "250Gi", "1Ti", "512Mi"]:
        assert re.fullmatch(pattern, value)
    for value in ["", "0Gi", "-1Gi", "100", "1.5Gi", "1G", "unlimited"]:
        assert not re.fullmatch(pattern, value)


def test_existing_size_is_readonly_and_new_default_is_optional() -> None:
    assert _storage_config("minio_existing_storage_size")["readonly"] is True
    assert _storage_config()["default"] == "100Gi"
    assert not _storage_config().get("required", False)


def test_selected_sizes_reach_bundled_minio_claim(tmp_path) -> None:
    import subprocess

    for index, (claim, selected, expected) in enumerate(
        [(None, "", "100Gi"), (None, "250Gi", "250Gi"), ("500Gi", "100Gi", "500Gi")]
    ):
        fixture = tmp_path / str(index)
        fixture.mkdir()
        size = _render_storage_settings(fixture, claim, selected)["size"]
        rendered = subprocess.run(
            [
                "helm",
                "template",
                "openhands",
                str(REPO_ROOT / "charts/openhands"),
                "--namespace",
                "openhands",
                "--set",
                "minio.enabled=true,minio.persistence.enabled=true,"
                f"minio.persistence.storageClass=openebs-hostpath,minio.persistence.size={size}",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        claims = [
            doc
            for doc in yaml.safe_load_all(rendered.stdout)
            if doc
            and doc.get("kind") == "PersistentVolumeClaim"
            and doc["metadata"]["name"] == "openhands-minio"
        ]
        assert len(claims) == 1
        assert claims[0]["spec"]["resources"]["requests"]["storage"] == expected
        assert claims[0]["spec"]["storageClassName"] == "openebs-hostpath"
