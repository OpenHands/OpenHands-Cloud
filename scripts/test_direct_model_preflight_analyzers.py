"""Admin Console results for the direct-model preflight, replayed from sample logs.

The runPod runs ``python -m run_direct_model_preflight`` from the enterprise
image and troubleshoot analyzes its log. The sample logs mirror that script's
output (OpenHands/enterprise ``server/services/direct_model_preflight.py``).

Two properties matter. Every analyzer must match one of its outcomes, because an
unmatched ``textAnalyze`` still returns an empty result row. And nothing may
read as a pass unless the scan printed its own all-clear line, so an image
without the scan (a traceback) or a scan that errored out never looks clean.
"""

from __future__ import annotations

import re

import pytest
import yaml

from test_troubleshoot_secrets_template import render_troubleshoot_secrets

LOG_FILE = "direct-model-preflight/direct-model-preflight.log"
ALL_CLEAR = "all-clear"
MODELS = "models"

ARTIFACT = '{"artifact_type": "direct_model_preflight", "error": null}\n'
LOGS = {
    "clean": (
        "Direct-model preflight: no LLM settings depend on sandbox provider credentials.\n"
        + ARTIFACT
    ),
    "azure": (
        "WARNING: 1 LLM setting(s) use a provider-direct model with no API key of their own.\n"
        "  azure/gpt-4o  ->  openhands/azure-gpt-4o\n" + ARTIFACT
    ),
    "bedrock": "WARNING: 1 LLM setting(s) ...\n  bedrock/anthropic.claude  ->  openhands/anthropic.claude\n" + ARTIFACT,
    "vertex": "WARNING: 1 LLM setting(s) ...\n  vertex_ai/gemini-2.5-pro  ->  openhands/gemini-2.5-pro\n" + ARTIFACT,
    "partial": (
        "Not scanned (see skipped_sources in the JSON artifact): org_member\n" + ARTIFACT
    ),
    "error": (
        "Direct-model preflight could not complete (OperationalError: boom). "
        "The upgrade is not blocked.\n"
        '{"artifact_type": "direct_model_preflight", "error": "OperationalError: boom"}\n'
    ),
    "image-without-script": (
        "/usr/local/bin/python: No module named run_direct_model_preflight\n"
    ),
    "empty": "",
}


def analyzers() -> dict[str, dict]:
    manifest = render_troubleshoot_secrets("directModelPreflight.enabled=true").stdout
    for doc in yaml.safe_load_all(manifest):
        if doc and doc["metadata"]["name"].endswith("-preflight"):
            spec = yaml.safe_load(doc["stringData"]["preflight.yaml"])["spec"]
            found = [
                a["textAnalyze"]
                for a in spec["analyzers"]
                if a.get("textAnalyze", {}).get("fileName") == LOG_FILE
            ]
            assert len(found) == 2
            return {ALL_CLEAR: found[0], MODELS: found[1]}
    raise AssertionError("preflight secret not rendered")


def outcome(analyzer: dict, log: str) -> str | None:
    matched = re.search(analyzer["regex"], log) is not None
    for entry in analyzer["outcomes"]:
        (kind, body), = entry.items()
        if (body["when"] == "true") == matched:
            return kind
    return None


@pytest.mark.parametrize("name", sorted(LOGS))
def test_every_analyzer_matches_an_outcome(name: str) -> None:
    for key, analyzer in analyzers().items():
        assert outcome(analyzer, LOGS[name]) is not None, f"{key} has no outcome for {name}"


def test_clean_scan_passes() -> None:
    result = {key: outcome(a, LOGS["clean"]) for key, a in analyzers().items()}
    assert result == {ALL_CLEAR: "pass", MODELS: "pass"}


@pytest.mark.parametrize("name", ["azure", "bedrock", "vertex"])
def test_provider_direct_models_warn(name: str) -> None:
    result = {key: outcome(a, LOGS[name]) for key, a in analyzers().items()}
    assert result == {ALL_CLEAR: "warn", MODELS: "warn"}


@pytest.mark.parametrize("name", ["partial", "error", "image-without-script", "empty"])
def test_an_incomplete_or_missing_scan_never_reads_as_clean(name: str) -> None:
    assert outcome(analyzers()[ALL_CLEAR], LOGS[name]) == "warn"


def test_nothing_is_added_when_disabled() -> None:
    manifest = render_troubleshoot_secrets().stdout
    assert "direct-model-preflight" not in manifest
