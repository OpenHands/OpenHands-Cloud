"""Tests for the self-hosted Laminar API ingress contract."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LAMINAR_API_INGRESS = (
    REPO_ROOT / "charts" / "openhands" / "templates" / "ingress-laminar-api.yaml"
)
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"
REPLICATED_APPLICATION = REPO_ROOT / "replicated" / "application.yaml"


def test_laminar_api_ingress_exposes_only_v1_prefix() -> None:
    template = LAMINAR_API_INGRESS.read_text(encoding="utf-8")

    assert 'name: laminar-api-ingress' in template
    assert 'path: {{ $apiIngress.path | default "/v1" }}' in template
    assert 'pathType: {{ $apiIngress.pathType | default "Prefix" }}' in template
    assert "name: laminar-app-server-service" in template
    assert "number: 8000" in template
    assert "path: /" not in template


def test_replicated_enables_laminar_api_ingress_on_analytics_host() -> None:
    values = REPLICATED_OPENHANDS.read_text(encoding="utf-8")

    assert "apiIngress:" in values
    assert "enabled: true" in values
    assert "analytics.app.{{repl ConfigOption \"base_domain\"}}" in values
    assert "{{repl ConfigOption \"analytics_hostname\"}}" in values
    assert "appServer:\n            loadBalancer:\n              enabled: false" in values
    assert "appServer:\n            loadBalancer:\n              enabled: false\n            ingress:" not in values


def test_replicated_application_expects_laminar_api_ingress() -> None:
    application = REPLICATED_APPLICATION.read_text(encoding="utf-8")

    assert "openhands/ingress/laminar-api-ingress" in application
