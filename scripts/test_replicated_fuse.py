"""The Replicated install must give fuse workspaces a bucket, credentials and a device.

Fuse sandboxes keep /workspace in the bundled MinIO. Each test resolves the
Replicated templating for a combination of the two config options and checks
that the values handed to the chart agree with what the chart creates.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FUSE_TEST = 'ConfigOptionEquals "computed_sandbox_fuse_enabled" "true"'
KVM_TEST = 'ConfigOptionEquals "sandbox_kvm_enabled" "1"'


def resolve(fuse: bool, kvm: bool) -> dict:
    text = (ROOT / "replicated/openhands.yaml").read_text()
    text = text.replace(FUSE_TEST, str(fuse).lower()).replace(KVM_TEST, str(kvm).lower())
    text = re.sub(
        r"repl\{\{ or \((true|false)\) \((true|false)\) \}\}",
        lambda m: str("true" in m.groups()).lower(),
        text,
    )
    text = re.sub(r"repl\{\{ (true|false) \}\}", r"\1", text)
    return yaml.safe_load(text)["spec"]


def chart_values() -> dict:
    return yaml.safe_load((ROOT / "charts/openhands/values.yaml").read_text())


def config_items() -> dict:
    groups = yaml.safe_load((ROOT / "replicated/config.yaml").read_text())["spec"]["groups"]
    return {item["name"]: item for group in groups for item in group["items"]}


@pytest.mark.parametrize("kvm", [False, True])
def test_the_values_point_the_fuse_mount_at_a_bucket_the_chart_creates(kvm):
    spec = resolve(fuse=True, kvm=kvm)
    mount = spec["values"]["runtime-api"]["fuseMount"]
    minio = chart_values()["minio"]

    assert mount["enabled"] is True
    assert mount["storageProvider"] == "s3"
    bucket = next(b for b in minio["buckets"] if b["name"] == mount["bucket"])
    assert bucket["purge"] is False
    assert mount["s3"]["endpoint"] == f"http://{spec['releaseName']}-minio:9000"
    account = minio["svcaccts"][0]
    assert mount["s3"]["accessKeyId"] == account["accessKey"]
    assert mount["s3"]["secretAccessKey"] == account["secretKey"]
    assert mount["s3"]["region"]


def test_the_broker_url_the_sandboxes_call_is_already_set():
    env = resolve(fuse=True, kvm=False)["values"]["runtime-api"]["env"]
    assert "RUNTIME_API_BASE_URL" in env


@pytest.mark.parametrize("fuse", [False, True])
@pytest.mark.parametrize("kvm", [False, True])
def test_one_toggle_drives_the_mount_the_pool_and_the_device(fuse, kvm):
    values = resolve(fuse=fuse, kvm=kvm)["values"]
    runtime_api = values["runtime-api"]
    plugin = values["device-plugin"]

    assert runtime_api["fuseMount"]["enabled"] is fuse
    assert runtime_api["warmRuntimes"]["fuse"]["enabled"] is fuse
    assert plugin["enabled"] is (fuse or kvm)
    assert plugin["devices"]["fuse"]["enabled"] is fuse
    assert plugin["devices"]["kvm"]["enabled"] is kvm


def test_sandboxes_request_the_device_the_plugin_advertises():
    values = resolve(fuse=True, kvm=False)["values"]
    resource = values["runtime-api"]["env"]["FUSE_DEVICE_RESOURCE"]
    assert resource == f"{values['device-plugin']['domain']}/fuse"


def test_the_fusey_image_follows_the_agent_server_image_in_every_mode():
    spec = resolve(fuse=True, kvm=False)
    global_values = spec["values"]["global"]
    local = next(b for b in spec["optionalValues"] if "HasLocalRegistry" in b["when"])
    local_global = local["values"]["global"]

    for values in (global_values, local_global):
        agent_server = values["agentServerImage"]["repository"]
        assert values["fuseyImage"]["repository"] == f"{agent_server}-fusey"


def test_the_air_gap_builder_renders_the_fuse_pool_so_its_image_is_bundled():
    runtime_api = resolve(fuse=True, kvm=False)["builder"]["runtime-api"]
    assert runtime_api["fuseMount"]["enabled"] is True
    assert runtime_api["warmRuntimes"]["fuse"]["enabled"] is True


def test_the_option_is_unavailable_with_a_custom_sandbox_image():
    items = config_items()
    assert "custom_sandbox_image_enabled" in items["sandbox_fuse_enabled"]["when"]
    computed = items["computed_sandbox_fuse_enabled"]
    assert computed["hidden"] is True
    assert '"sandbox_fuse_enabled" "1"' in computed["default"]
    assert '"custom_sandbox_image_enabled" "0"' in computed["default"]
