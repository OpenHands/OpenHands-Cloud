"""Tests for the ECR credential provider contract.

The installer is a host-level change driven from three places that have to agree:
the KOTS config option, the Replicated HelmChart release that reads it, and the
chart values the release turns on. A drift between them fails silently — nodes
just keep failing ECR pulls — so it is asserted here rather than left to review.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
INFRA_CHART = REPO_ROOT / "charts" / "infra"
INFRA_VALUES = INFRA_CHART / "values.yaml"
INSTALL_SCRIPT = INFRA_CHART / "files" / "install-ecr-credential-provider.sh"
ECR_RELEASE = REPO_ROOT / "replicated" / "infra-ecr-credential-provider.yaml"
OPENHANDS_RELEASE = REPO_ROOT / "replicated" / "openhands.yaml"
CONFIG = REPO_ROOT / "replicated" / "config.yaml"
SUPPORT_BUNDLE = (
    REPO_ROOT / "charts" / "openhands" / "templates" / "troubleshoot" / "support-bundle.yaml"
)

CONFIG_OPTION = "ecr_credential_provider_enabled"


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


@pytest.fixture(scope="module")
def infra_values() -> dict:
    return load_yaml(INFRA_VALUES)["ecrCredentialProvider"]


@pytest.fixture(scope="module")
def ecr_release() -> dict:
    return load_yaml(ECR_RELEASE)


@pytest.fixture(scope="module")
def config_items() -> dict:
    config = load_yaml(CONFIG)
    return {
        item["name"]: item
        for group in config["spec"]["groups"]
        for item in group.get("items", [])
    }


def test_config_option_exists_and_defaults_off(config_items: dict) -> None:
    """The node-level change must never be on unless an operator asked for it."""
    item = config_items[CONFIG_OPTION]
    assert item["type"] == "bool"
    assert item["default"] == "0"


def test_chart_default_is_off(infra_values: dict) -> None:
    """A chart consumer that never heard of this feature must not get it."""
    assert infra_values["enabled"] is False


def test_release_enables_the_chart_from_the_config_option(ecr_release: dict) -> None:
    enabling = [
        entry
        for entry in ecr_release["spec"]["optionalValues"]
        if entry["values"].get("ecrCredentialProvider", {}).get("enabled") is True
    ]
    assert len(enabling) == 1, "exactly one optionalValues block should turn the installer on"
    assert CONFIG_OPTION in enabling[0]["when"]


def test_release_carries_only_the_installer(ecr_release: dict) -> None:
    """cert-manager and trust-manager have their own weighted releases."""
    values = ecr_release["spec"]["values"]
    assert values["cert-manager"]["enabled"] is False
    assert values["trust-manager"]["enabled"] is False
    assert values["ecrCredentialProvider"]["enabled"] is False


def test_release_is_weighted_after_every_other_release(ecr_release: dict) -> None:
    """The first deploy restarts k0s, so it has to land after KOTS applies the rest."""
    weights = []
    for manifest in sorted((REPO_ROOT / "replicated").glob("*.yaml")):
        doc = load_yaml(manifest)
        if doc.get("kind") == "HelmChart" and manifest != ECR_RELEASE:
            weights.append(doc["spec"]["weight"])
    assert ecr_release["spec"]["weight"] > max(weights)


def test_release_does_not_wait(ecr_release: dict) -> None:
    """Helm must not be blocking on this DaemonSet while the API server bounces."""
    assert "helmUpgradeFlags" not in ecr_release["spec"]


def test_self_test_is_scoped_to_ecr_repositories(ecr_release: dict) -> None:
    """Pointing the self-test at a non-ECR registry would report a bogus failure."""
    entries = [
        entry
        for entry in ecr_release["spec"]["optionalValues"]
        if "selfTestImage" in entry["values"].get("ecrCredentialProvider", {})
    ]
    assert len(entries) == 1
    when = entries[0]["when"]
    assert CONFIG_OPTION in when
    assert ".dkr.ecr." in when


def test_proxy_block_always_keeps_imds_off_the_proxy(ecr_release: dict) -> None:
    """A proxied IMDS request is the quiet way instance-profile lookups break."""
    entries = [
        entry
        for entry in ecr_release["spec"]["optionalValues"]
        if "providerEnv" in entry["values"].get("ecrCredentialProvider", {})
    ]
    assert len(entries) == 1
    assert "169.254.169.254" in entries[0]["when"]
    assert set(entries[0]["values"]["ecrCredentialProvider"]["providerEnv"]) == {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    }


def test_match_images_cover_the_aws_partitions(infra_values: dict) -> None:
    globs = infra_values["matchImages"]
    assert "*.dkr.ecr.*.amazonaws.com" in globs
    assert "public.ecr.aws" in globs
    for glob in globs:
        # Each kubelet glob matches a single subdomain segment, so a wildcard
        # spanning a dot would silently never match.
        assert "*." in glob or "*" not in glob, glob


def test_checksums_are_sha256_and_both_arches_present(infra_values: dict) -> None:
    assert set(infra_values["checksums"]) == {"amd64", "arm64"}
    for digest in infra_values["checksums"].values():
        assert re.fullmatch(r"[0-9a-f]{64}", digest), digest


def test_release_url_and_version_agree_with_the_installer(infra_values: dict) -> None:
    """The installer builds the download URL, so the values must fit its layout."""
    assert infra_values["version"].startswith("v")
    assert infra_values["releaseBaseUrl"].endswith("/releases/download")
    script = INSTALL_SCRIPT.read_text()
    assert (
        '$ECR_CP_RELEASE_BASE_URL/$ECR_CP_VERSION/ecr-credential-provider-linux-$ARCH' in script
    )


def test_installer_refuses_an_unverified_binary() -> None:
    """The plugin comes from a third-party rebuild, so the pin is the trust boundary."""
    script = INSTALL_SCRIPT.read_text()
    assert 'err "checksum mismatch' in script


def test_pull_secret_env_still_falls_back_when_no_registry_credentials() -> None:
    """With node IAM there is no ECR pull secret, so the sandbox env must not demand one."""
    text = OPENHANDS_RELEASE.read_text()
    line = next(l for l in text.splitlines() if "RUNTIME_IMAGE_PULL_SECRETS" in l)
    assert "ImagePullSecretName" in line


def test_support_bundle_collects_the_installer_logs() -> None:
    assert "ecr-credential-provider-installer" in SUPPORT_BUNDLE.read_text()


def test_daemonset_renders_only_when_enabled() -> None:
    def render(enabled: bool) -> str:
        return subprocess.run(
            [
                "helm",
                "template",
                "infra",
                str(INFRA_CHART),
                "--set",
                "cert-manager.enabled=false",
                "--set",
                "trust-manager.enabled=false",
                "--set",
                f"ecrCredentialProvider.enabled={'true' if enabled else 'false'}",
            ],
            check=True,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        ).stdout

    assert "ecr-credential-provider-installer" not in render(False)
    assert "ecr-credential-provider-installer" in render(True)


def test_rendered_install_script_is_byte_identical_to_the_source() -> None:
    """The host runs whatever the pod spec carries, so the inlining must not mangle it."""
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "infra",
            str(INFRA_CHART),
            "--set",
            "cert-manager.enabled=false",
            "--set",
            "trust-manager.enabled=false",
            "--set",
            "ecrCredentialProvider.enabled=true",
            "--show-only",
            "templates/ecr-credential-provider-installer.yaml",
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout
    daemonset = yaml.safe_load(rendered)
    env = {
        entry["name"]: entry.get("value", "")
        for entry in daemonset["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["INSTALL_SCRIPT"] == INSTALL_SCRIPT.read_text()


def test_no_when_conditional_survives_yaml_loading_with_a_newline() -> None:
    """A block-scalar `when:` breaks every install of the release, not just this feature.

    KOTS runs strconv.ParseBool over the rendered `when`, and it evaluates every
    HelmChart's conditionals at app-pull time regardless of config. A `when: |`
    keeps its newlines, so the value arrives as "\\n\\nfalse\\n" and ParseBool
    rejects it, failing the install even with the feature switched off. Folded
    (`>-`) and single-line scalars both load without newlines and are fine.
    """
    offenders = []
    for manifest in sorted((REPO_ROOT / "replicated").glob("*.yaml")):
        doc = load_yaml(manifest)
        if not isinstance(doc, dict) or doc.get("kind") != "HelmChart":
            continue
        for index, entry in enumerate(doc.get("spec", {}).get("optionalValues") or []):
            when = entry.get("when", "")
            if "\n" in when:
                offenders.append(f"{manifest.name}[{index}]: {when!r}")
    assert not offenders, "block-scalar `when:` found (must load as a single line): " + "; ".join(
        offenders
    )
