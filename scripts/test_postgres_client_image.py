"""Every psql/pg_isready container must use the pinned global.postgresClientImage, mirrored in each mode."""

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/openhands"
APP, HOST, NS = "test-app", "registry.test", "test-ns"
# Turn on every site that runs the client, including the non-default branches.
SITE_VALUES = [
    {
        "databaseMigrations": {"createDatabases": True},
        "keycloak": {"enabled": True},
        "runtime-api": {
            "databaseMigrations": {"createDatabases": True},
            "database": {"create": True, "host": "db", "user": "postgres", "name": "rt", "new_user": "rt_user"},
        },
        "plugin-directory": {"enabled": True, "databaseMigrations": {"createDatabases": True}},
        "automation": {"enabled": True, "database": {"createDatabaseUser": True}},
        "integrations-hub": {"enabled": True, "database": {"host": "db", "createDatabaseUser": True}},
    },
    {
        "automation": {"enabled": True, "database": {"createDatabaseUser": False}, "postgresql": {"enabled": True}},
        "integrations-hub": {"enabled": True, "database": {"host": "db", "createDatabaseUser": False}, "postgresql": {"enabled": True}},
    },
]


def chart_default():
    return yaml.safe_load((CHART / "values.yaml").read_text())["global"]["postgresClientImage"]


def replicated_repository(mode):
    spec = yaml.safe_load((ROOT / "replicated/openhands.yaml").read_text())["spec"]
    if mode == "online":
        values = spec["values"]
    else:
        values = next(b for b in spec["optionalValues"] if "HasLocalRegistry" in b["when"])["values"]
    repo = values["global"]["postgresClientImage"]["repository"]
    for source, target in {
        '{{repl LicenseFieldValue "appSlug"}}': APP,
        "{{repl LocalRegistryHost }}": HOST,
        "{{repl LocalRegistryNamespace }}": NS,
    }.items():
        repo = repo.replace(source, target)
    return repo


def client_images(rendered):
    images = []
    for doc in yaml.safe_load_all(rendered):
        if not doc:
            continue
        spec = doc.get("spec", {})
        pod = spec.get("template", {}).get("spec") or spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec")
        for c in (pod or {}).get("initContainers", []) + (pod or {}).get("containers", []):
            script = " ".join((c.get("command") or []) + (c.get("args") or []))
            if "psql" in script or "pg_isready" in script:
                images.append((doc["metadata"]["name"], c["name"], c["image"]))
    return images


def test_chart_default_is_digest_pinned():
    assert "@sha256:" in chart_default()["tag"]


@pytest.mark.parametrize("mode", ["online", "airgap"])
@pytest.mark.parametrize("sites", range(len(SITE_VALUES)))
def test_every_client_container_uses_the_mirrored_image(mode, sites, tmp_path):
    default = chart_default()
    expected_repo = {
        "online": f"images.r9.all-hands.dev/proxy/{APP}/{default['repository']}",
        "airgap": f"{HOST}/{NS}/{default['repository'].rsplit('/', 1)[-1]}",
    }[mode]
    assert replicated_repository(mode) == expected_repo
    values = dict(SITE_VALUES[sites], **{"global": {"postgresClientImage": {"repository": expected_repo}}})
    values_path = tmp_path / "values.yaml"
    values_path.write_text(yaml.safe_dump(values))
    rendered = subprocess.run(
        ["helm", "template", "openhands", str(CHART), "-f", str(values_path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    images = client_images(rendered)
    assert len(images) >= 5
    assert all(image == f"{expected_repo}:{default['tag']}" for _, _, image in images), images
