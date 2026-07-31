#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest"]
# ///
"""Tests for Enterprise Replicated preview helper behavior."""

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import enterprise_replicated_preview as preview


def test_build_metadata_uses_stable_per_pr_names_and_preview_versions():
    metadata = preview.build_metadata(
        pr_number="92",
        sha="c23c797b11aa22bb33cc44dd55ee66ff77889900",
        chart_version="0.35.0",
        run_number=17,
        domain_suffix="replicated-preview.example.com",
    )

    assert metadata.enterprise_short_sha == "c23c797"
    assert metadata.enterprise_image_tag == "sha-c23c797"
    assert (
        metadata.enterprise_image_ref
        == "ghcr.io/openhands/enterprise-server:sha-c23c797"
    )
    assert metadata.preview_channel == "enterprise-pr-92"
    assert metadata.preview_customer == "enterprise-pr-92-c23c797"
    assert metadata.preview_release_version == "enterprise-pr-92-c23c797"
    assert metadata.preview_chart_version == "0.35.1-enterprise-pr.92.17"
    assert metadata.preview_instance_name == "oh-ent-pr-92-c23c797"
    assert metadata.preview_base_domain == "pr-92.replicated-preview.example.com"


def test_build_metadata_rejects_invalid_inputs():
    with pytest.raises(ValueError, match="positive"):
        preview.build_metadata(
            pr_number="0", sha="c23c797", chart_version="0.35.0", run_number=1
        )
    with pytest.raises(ValueError, match="hexadecimal"):
        preview.build_metadata(
            pr_number="1", sha="not-a-sha", chart_version="0.35.0", run_number=1
        )
    with pytest.raises(ValueError, match="expected semver"):
        preview.build_metadata(
            pr_number="1", sha="c23c797", chart_version="next", run_number=1
        )


def test_patch_chart_files_updates_only_chart_version_and_image_tag(tmp_path):
    chart = tmp_path / "Chart.yaml"
    values = tmp_path / "values.yaml"
    chart.write_text("apiVersion: v2\nappVersion: 1.49.0\nversion: 0.35.0\n")
    values.write_text(
        "allowedUsers: null\n\n"
        "image:\n"
        "  repository: ghcr.io/openhands/enterprise-server\n"
        "  tag: 1.49.0\n\n"
        "other:\n"
        "  tag: untouched\n"
    )

    preview.patch_chart_file(chart, "0.35.1-enterprise-pr.92.17")
    preview.patch_values_image_tag(values, "sha-c23c797")

    assert chart.read_text() == (
        "apiVersion: v2\nappVersion: 1.49.0\n"
        "version: 0.35.1-enterprise-pr.92.17\n"
    )
    assert '  tag: "sha-c23c797"' in values.read_text()
    assert "  tag: untouched" in values.read_text()


def test_render_config_values_encodes_file_fields_and_custom_llm(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("-----BEGIN CERTIFICATE-----\ncert\n-----END CERTIFICATE-----\n")
    key.write_text("-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n")

    rendered = preview.render_config_values(
        base_domain="pr-92.preview.example.com",
        tls_certificate=cert,
        tls_private_key=key,
        custom_base_url="https://llm.example.com/v1",
        custom_api_key="secret",
        custom_models="openai/gpt-preview\nopenai/gpt-backup",
        automations_enabled=True,
    )

    assert "hostname_mode:" in rendered
    assert 'value: "derive"' in rendered
    assert 'base_domain:\n      value: "pr-92.preview.example.com"' in rendered
    assert base64.b64encode(cert.read_bytes()).decode("ascii") in rendered
    assert base64.b64encode(key.read_bytes()).decode("ascii") in rendered
    assert "custom_base_url:" in rendered
    assert 'valuePlaintext: "secret"' in rendered
    assert "openai/gpt-preview" in rendered
    assert 'automations_enabled:\n      value: "1"' in rendered


def test_render_tfvars_includes_preview_tags_and_optional_network():
    rendered = preview.render_tfvars(
        instance_name="oh-ent-pr-92-c23c797",
        base_domain="pr-92.preview.example.com",
        aws_region="us-east-1",
        route53_zone_id="Z123",
        acme_email="ops@example.com",
        vpc_id="vpc-123",
        subnet_id="subnet-123",
        allowed_cidrs=["203.0.113.4/32"],
        default_tags={"EnterprisePR": "92", "PreviewKind": "enterprise-replicated"},
    )

    assert 'instance_name = "oh-ent-pr-92-c23c797"' in rendered
    assert 'base_domain = "pr-92.preview.example.com"' in rendered
    assert 'allowed_cidrs = ["203.0.113.4/32"]' in rendered
    assert 'vpc_id = "vpc-123"' in rendered
    assert '"EnterprisePR" = "92"' in rendered


def test_render_gcp_tfvars_matches_staging_preview_conventions():
    rendered = preview.render_gcp_tfvars(
        instance_name="oh-ent-pr-92-c23c797",
        base_domain="pr-92.replicated.staging.all-hands.dev",
        project_id="staging-092324",
        region="us-central1",
        zone="us-central1-a",
        network="staging-core-app",
        subnetwork="staging-core-app",
        dns_managed_zone="staging-all-hands-dot-dev",
        acme_email="ops@example.com",
        allowed_admin_cidrs=["203.0.113.4/32"],
        labels={"enterprise-pr": "92", "preview-kind": "enterprise-replicated"},
    )

    assert 'project_id = "staging-092324"' in rendered
    assert 'region = "us-central1"' in rendered
    assert 'network = "staging-core-app"' in rendered
    assert 'dns_managed_zone = "staging-all-hands-dot-dev"' in rendered
    assert 'allowed_admin_cidrs = ["203.0.113.4/32"]' in rendered
    assert '"enterprise-pr" = "92"' in rendered

