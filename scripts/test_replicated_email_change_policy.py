"""Tests for the Replicated account email change policy."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPLICATED_OPENHANDS = REPO_ROOT / "replicated" / "openhands.yaml"


def test_replicated_disables_email_changes_in_api_and_ui() -> None:
    values = REPLICATED_OPENHANDS.read_text(encoding="utf-8")

    assert "      EMAIL_CHANGE_ENABLED: false\n" in values
    assert "      OH_WEB_CLIENT_EMAIL_CHANGE_ENABLED: false\n" in values
