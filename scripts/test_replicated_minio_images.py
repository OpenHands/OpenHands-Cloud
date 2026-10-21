"""MinIO server and setup jobs must use the same mirrored release in every mode."""

import copy
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {
    "image": (
        "ohe-minio",
        "RELEASE.2023-05-18T00-05-36Z",
        "52c9c477179216d0418c95e8aad047db6d406fa475b7d624b5ba990fe7099279",
    ),
    "mcImage": (
        "ohe-minio-mc",
        "RELEASE.2023-05-18T16-59-00Z",
        "9e46d9ed12fa66361f6482b0081d65c2d68e01cbbdede8b964950f76e5a35701",
    ),
}


def config():
    return yaml.safe_load((ROOT / "replicated/openhands.yaml").read_text())["spec"]


@pytest.mark.parametrize("mode", ["online", "airgap", "builder"])
def test_minio_server_and_jobs_use_verified_images(mode, tmp_path):
    spec = config()
    values = copy.deepcopy(spec["values"]["minio"])
    if mode == "builder":
        values.update(spec["builder"]["minio"])
    elif mode == "airgap":
        local = next(
            block
            for block in spec["optionalValues"]
            if "HasLocalRegistry" in block["when"]
        )
        for key in IMAGES:
            values.setdefault(key, {}).update(local["values"]["minio"][key])
    replacements = {
        '{{repl LicenseFieldValue "appSlug"}}': "test-app",
        "{{repl ImagePullSecretName }}": "test-registry",
        "{{repl LocalRegistryHost }}": "registry.test",
        "{{repl LocalRegistryNamespace }}": "test-app",
    }
    text = yaml.safe_dump(values)
    for source, target in replacements.items():
        text = text.replace(source, target)
    values = yaml.safe_load(text)
    for key, (name, tag, digest) in IMAGES.items():
        expected_repo = {
            "online": f"images.r9.all-hands.dev/proxy/test-app/ghcr.io/openhands/{name}",
            "airgap": f"registry.test/test-app/{name}",
            "builder": f"ghcr.io/openhands/{name}",
        }[mode]
        assert values[key]["repository"] == expected_repo
        assert values[key]["tag"] == f"{tag}-amd64@sha256:{digest}"
    # Render the actual pinned dependency, including its post-install job.
    values.update(
        mode="standalone",
        replicas=1,
        rootUser="test-root",
        rootPassword="test-password",
    )
    values["persistence"] = {"enabled": True, "size": "500Gi"}
    values["buckets"] = [{"name": "test-sessions", "policy": "none", "purge": False}]
    values["svcaccts"] = [
        {"accessKey": "test-access", "secretKey": "test-secret", "user": "root"}
    ]
    values_path = tmp_path / "values.yaml"
    values_path.write_text(yaml.safe_dump(values))
    dependency = next((ROOT / "charts/openhands/charts").glob("minio-*.tgz"))
    rendered = subprocess.run(
        ["helm", "template", "openhands", str(dependency), "-f", str(values_path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    docs = [d for d in yaml.safe_load_all(rendered) if d]
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    job = next(d for d in docs if d["kind"] == "Job")
    server = values["image"]
    client = values["mcImage"]
    assert (
        deployment["spec"]["template"]["spec"]["containers"][0]["image"]
        == f"{server['repository']}:{server['tag']}"
    )
    job_pod = job["spec"]["template"]["spec"]
    containers = job_pod.get("initContainers", []) + job_pod["containers"]
    assert len(containers) >= 2
    assert all(
        c["image"] == f"{client['repository']}:{client['tag']}" for c in containers
    )
    assert job_pod["imagePullSecrets"] == [{"name": "test-registry"}]
