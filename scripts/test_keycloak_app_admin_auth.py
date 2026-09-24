"""Application admin calls retain access after rotating the console password."""

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_replicated_application_containers_use_provisioner():
    replicated = yaml.safe_load((ROOT / "replicated/openhands.yaml").read_text())
    client_id = replicated["spec"]["values"]["env"]["KEYCLOAK_ADMIN_CLIENT_ID"]
    rendered = subprocess.run(
        ["helm", "template", "openhands", str(ROOT / "charts/openhands"), "--values", "-"],
        input=yaml.safe_dump({"env": {"KEYCLOAK_ADMIN_CLIENT_ID": client_id}}),
        capture_output=True, text=True, check=True,
    )
    count = 0
    for doc in yaml.safe_load_all(rendered.stdout):
        if not doc or doc["kind"] != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            if "enterprise-server:" not in container["image"]:
                continue
            env = {item["name"]: item for item in container["env"]}
            assert env["KEYCLOAK_ADMIN_CLIENT_ID"]["value"] == "openhands-provisioner"
            assert env["KEYCLOAK_ADMIN_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
                "name": "keycloak-admin", "key": "admin-password"
            }
            count += 1
    assert count == 3
