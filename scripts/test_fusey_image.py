#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyYAML", "pytest"]
# ///
"""Behavior tests for the fusey image release check.

The registry is a real HTTP server on localhost speaking the small part of the
OCI distribution API the check uses, so the request and response handling runs
unmodified.
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))

import fusey_image
from fusey_image import FuseyImageError

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "openhands/agent-server-fusey"
TAG = "1.60.1-python-fusey0.0.14"


def platform(architecture, os="linux"):
    return {"platform": {"architecture": architecture, "os": os}}


# A multi-arch index as buildx publishes it: one manifest per architecture plus
# an attestation manifest for each, which carries no real platform.
FULL_INDEX = {
    "manifests": [
        platform("amd64"),
        platform("unknown", os="unknown"),
        platform("arm64"),
        platform("unknown", os="unknown"),
    ]
}


class Registry:
    """A registry holding `tags` (tag -> index document) for one package."""

    def __init__(self):
        self.tags: dict[str, dict] = {}
        self.package_exists = True
        self.requests: list[str] = []
        registry = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                registry.requests.append(self.path)
                if self.path.startswith("/token"):
                    status, body = (200, {"token": "anonymous"}) if registry.package_exists else (404, {})
                else:
                    tag = self.path.rsplit("/", 1)[-1]
                    authorized = self.headers.get("Authorization") == "Bearer anonymous"
                    if not authorized:
                        status, body = 401, {}
                    elif tag in registry.tags:
                        status, body = 200, registry.tags[tag]
                    else:
                        status, body = 404, {}
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"127.0.0.1:{self.server.server_port}"
        self.url = f"http://{self.host}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def registry():
    instance = Registry()
    yield instance
    instance.close()


def write_values(root: Path, repository, agent_server_tag="1.60.1-python", version="0.0.14", **overrides):
    """Lay down both values files, each with a decoy image block at another path."""
    for path in fusey_image.VALUES_FILES:
        values = {
            "image": {"tag": "0.0.1-decoy"},
            "global": {
                "agentServerImage": {"repository": "ghcr.io/openhands/agent-server", "tag": agent_server_tag},
                "fuseyImage": {"repository": repository, "version": version},
            },
        }
        for key, value in overrides.get(path.parent.name, {}).items():
            values["global"][key].update(value)
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(yaml.safe_dump(values))
    return root


def test_the_image_tag_is_the_agent_server_tag_plus_the_fusey_version(tmp_path):
    write_values(tmp_path, f"ghcr.io/{PACKAGE}", agent_server_tag="1.70.0-python", version="0.1.2")

    image = fusey_image.resolve(tmp_path)

    assert image.tag == "1.70.0-python-fusey0.1.2"
    assert image.reference == f"ghcr.io/{PACKAGE}:1.70.0-python-fusey0.1.2"


def test_the_real_charts_resolve_to_an_image_on_ghcr():
    image = fusey_image.resolve(REPO_ROOT)

    assert image.repository == f"ghcr.io/{PACKAGE}"
    assert image.tag == f"{image.agent_server_tag}-fusey{image.fusey_version}"


@pytest.mark.parametrize(
    "key, value, label",
    [
        ("agentServerImage", {"tag": "1.59.0-python"}, "agent-server image tags"),
        ("fuseyImage", {"version": "0.0.13"}, "fusey versions"),
        ("fuseyImage", {"repository": "ghcr.io/other/fusey"}, "fusey image repositories"),
    ],
)
def test_the_umbrella_and_subchart_pins_must_agree(tmp_path, key, value, label):
    write_values(tmp_path, f"ghcr.io/{PACKAGE}", **{"runtime-api": {key: value}})

    with pytest.raises(FuseyImageError, match=f"{label} disagree with each other") as error:
        fusey_image.resolve(tmp_path)

    # Both files are named, so the fix is actionable.
    for path in fusey_image.VALUES_FILES:
        assert str(path) in str(error.value)


def test_an_unquoted_version_is_rejected_instead_of_read_as_a_float(tmp_path):
    write_values(tmp_path, f"ghcr.io/{PACKAGE}")
    for path in fusey_image.VALUES_FILES:
        text = (tmp_path / path).read_text().replace("version: 0.0.14", "version: 0.1")
        (tmp_path / path).write_text(text.replace("version: '0.0.14'", "version: 0.1"))

    with pytest.raises(FuseyImageError, match="expected a quoted string"):
        fusey_image.resolve(tmp_path)


def test_a_missing_pin_names_the_file_and_key(tmp_path):
    write_values(tmp_path, f"ghcr.io/{PACKAGE}")
    path = fusey_image.VALUES_FILES[1]
    values = yaml.safe_load((tmp_path / path).read_text())
    del values["global"]["fuseyImage"]
    (tmp_path / path).write_text(yaml.safe_dump(values))

    with pytest.raises(FuseyImageError, match=r"no value at \.global\.fuseyImage"):
        fusey_image.resolve(tmp_path)


def test_check_passes_when_both_architectures_are_published(tmp_path, registry):
    write_values(tmp_path, f"{registry.host}/{PACKAGE}")
    registry.tags[TAG] = FULL_INDEX

    report = fusey_image.check(tmp_path, registry.url)

    assert TAG in report
    assert "amd64" in report and "arm64" in report


def test_check_asks_the_registry_for_the_derived_tag_only(tmp_path, registry):
    write_values(tmp_path, f"{registry.host}/{PACKAGE}", agent_server_tag="1.70.0-python")
    registry.tags[TAG] = FULL_INDEX  # the old tag exists, the new one does not

    with pytest.raises(FuseyImageError, match="1.70.0-python-fusey0.0.14 is not published"):
        fusey_image.check(tmp_path, registry.url)

    assert any(request.endswith("/manifests/1.70.0-python-fusey0.0.14") for request in registry.requests)


def test_a_missing_tag_says_how_to_publish_it_with_the_exact_pins(tmp_path, registry):
    write_values(tmp_path, f"{registry.host}/{PACKAGE}", agent_server_tag="1.70.0-python", version="0.1.2")

    with pytest.raises(FuseyImageError) as error:
        fusey_image.check(tmp_path, registry.url)

    message = str(error.value)
    assert f"{registry.host}/{PACKAGE}:1.70.0-python-fusey0.1.2 is not published" in message
    assert "set these in fusey-image.env in OpenHands/runtime-api" in message
    assert "AGENT_SERVER_TAG=1.70.0-python\n  FUSEY_VERSION=0.1.2" in message
    # runtime-api's workflow takes the version with its leading v.
    assert (
        "gh workflow run fusey-image.yaml --repo OpenHands/runtime-api "
        "-f agent_server_tag=1.70.0-python -f fusey_version=v0.1.2"
    ) in message


def test_check_fails_when_the_package_does_not_exist_yet(tmp_path, registry):
    write_values(tmp_path, f"{registry.host}/{PACKAGE}")
    registry.package_exists = False

    with pytest.raises(FuseyImageError, match="is not published"):
        fusey_image.check(tmp_path, registry.url)


@pytest.mark.parametrize("present, missing", [("amd64", "arm64"), ("arm64", "amd64")])
def test_check_fails_when_an_architecture_is_missing(tmp_path, registry, present, missing):
    write_values(tmp_path, f"{registry.host}/{PACKAGE}")
    registry.tags[TAG] = {"manifests": [platform(present), platform("unknown", os="unknown")]}

    with pytest.raises(FuseyImageError) as error:
        fusey_image.check(tmp_path, registry.url)

    message = str(error.value)
    assert f"without {missing}" in message
    assert "gh workflow run fusey-image.yaml" in message


def test_attestation_manifests_do_not_count_as_architectures(tmp_path, registry):
    write_values(tmp_path, f"{registry.host}/{PACKAGE}")
    registry.tags[TAG] = {"manifests": [platform("amd64"), platform("arm64", os="unknown")]}

    with pytest.raises(FuseyImageError, match="without arm64"):
        fusey_image.check(tmp_path, registry.url)


def test_check_refuses_a_repository_on_another_registry(tmp_path, registry):
    write_values(tmp_path, f"quay.io/{PACKAGE}")

    with pytest.raises(FuseyImageError, match="is not on"):
        fusey_image.check(tmp_path, registry.url)


def test_the_command_exits_nonzero_with_the_error_on_stderr(tmp_path, registry, capsys):
    write_values(tmp_path, f"{registry.host}/{PACKAGE}")

    status = fusey_image.main(["--repo-root", str(tmp_path), "--registry-url", registry.url])

    assert status == 1
    assert "is not published" in capsys.readouterr().err


def test_the_command_exits_zero_when_the_image_is_published(tmp_path, registry, capsys):
    write_values(tmp_path, f"{registry.host}/{PACKAGE}")
    registry.tags[TAG] = FULL_INDEX

    status = fusey_image.main(["--repo-root", str(tmp_path), "--registry-url", registry.url])

    assert status == 0
    assert TAG in capsys.readouterr().out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
