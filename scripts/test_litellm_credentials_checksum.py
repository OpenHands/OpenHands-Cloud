#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyYAML", "pytest"]
# ///
"""Behavior tests for the LiteLLM credentials checksum guard.

Styled after test_replicated_minio_storage.py: plain pytest + PyYAML. The
static assertions (arity, shape, optionalValues guard, repo coverage) run
against the real tree; the negative cases build a three-file fixture root and
call ``check(root)`` directly so the assertions can be precise and fast.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))

import check_litellm_checksum as clc  # noqa: E402
from check_litellm_checksum import (  # noqa: E402
    EXPECTED_DEPENDENCY_COUNT,
    OPENHANDS_REL,
    SECRETS_REL,
    TEMPLATE_REL,
    check,
    extract_config_keys,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "scripts" / "check_litellm_checksum.py"


# ---------------------------------------------------------------------------
# Helpers to read the real checksum annotation
# ---------------------------------------------------------------------------
def _load_openhands_doc() -> dict:
    for doc in yaml.safe_load_all((REPO_ROOT / OPENHANDS_REL).read_text(encoding="utf-8")):
        if not isinstance(doc, dict):
            continue
        annotations = (
            ((doc.get("spec") or {}).get("values") or {})
            .get("litellm-helm", {})
            .get("podAnnotations", {})
        ) or {}
        if "checksum/litellm-credentials" in annotations:
            return doc
    raise AssertionError("checksum/litellm-credentials annotation not found")


def _annotation_value() -> str:
    doc = _load_openhands_doc()
    return doc["spec"]["values"]["litellm-helm"]["podAnnotations"]["checksum/litellm-credentials"]


# ---------------------------------------------------------------------------
# Coverage test (red at BASELINE_COMMIT, green after Step 3)
# ---------------------------------------------------------------------------
def test_repo_checksum_covers_all_dependencies() -> None:
    errors = check(REPO_ROOT)
    assert errors == [], "checksum/litellm-credentials out of sync:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# Argument arity: %s count must equal ConfigOption/ConfigOptionData count
# ---------------------------------------------------------------------------
def test_printf_arity_matches_config_option_count() -> None:
    value = _annotation_value()
    fmt = re.search(r'printf\s+"([^"]*)"', value)
    assert fmt, "could not locate the printf format string"
    placeholders = fmt.group(1).count("%s")
    calls = len(re.findall(r"ConfigOption(?:Data)?\s+\"", value))
    assert placeholders == calls, (
        f"printf has {placeholders} %s placeholders but {calls} ConfigOption "
        f"calls; a mismatch makes Go emit %!s(MISSING) into the hashed string."
    )


# ---------------------------------------------------------------------------
# Shape of the annotation value
# ---------------------------------------------------------------------------
def test_annotation_shape() -> None:
    value = _annotation_value()
    assert "\n" not in value, "annotation must be a single-line value"
    assert value.startswith('repl{{ sha256sum (printf "'), value
    assert value.endswith(") }}"), value

    names = re.findall(r"ConfigOption(?:Data)?\s+\"([^\"]+)\"", value)
    assert len(names) == len(set(names)), f"duplicate ConfigOption names: {names}"

    # google_vertex_credentials is a file-type option -> ConfigOptionData.
    assert re.search(r'ConfigOptionData\s+"google_vertex_credentials"', value), (
        "google_vertex_credentials must be read with ConfigOptionData (file type)"
    )


def test_openhands_yaml_is_valid_yaml() -> None:
    docs = list(yaml.safe_load_all((REPO_ROOT / OPENHANDS_REL).read_text(encoding="utf-8")))
    assert docs, "replicated/openhands.yaml did not parse into any documents"


# ---------------------------------------------------------------------------
# optionalValues guard: on the block, not the field
# ---------------------------------------------------------------------------
def test_optional_values_litellm_blocks_are_recursive_and_dont_override() -> None:
    doc = _load_openhands_doc()
    optional_values = doc["spec"].get("optionalValues", []) or []
    checked = 0
    for entry in optional_values:
        values = entry.get("values", {}) or {}
        if "litellm-helm" not in values:
            continue
        checked += 1
        assert entry.get("recursiveMerge") is True, (
            f"optionalValues block '{entry.get('when')}' touches litellm-helm but "
            f"is not recursiveMerge:true; a non-recursive block replaces the whole "
            f"subtree and drops the annotation."
        )
        pod_annotations = (values["litellm-helm"] or {}).get("podAnnotations", {}) or {}
        assert "checksum/litellm-credentials" not in pod_annotations, (
            f"optionalValues block '{entry.get('when')}' overrides "
            f"checksum/litellm-credentials, defeating the fix even under recursiveMerge."
        )
    assert checked > 0, "expected at least one litellm-helm optionalValues block"


# ---------------------------------------------------------------------------
# Isolated extractor units (no repo context, no count applied)
# ---------------------------------------------------------------------------
def test_extract_dotted_form() -> None:
    assert extract_config_keys("{{ .Values.config.openai_api_key }}") == {"openai_api_key"}


def test_extract_index_form() -> None:
    assert extract_config_keys('{{ index .Values.config "openai_api_key" }}') == {"openai_api_key"}


def test_extract_get_form() -> None:
    assert extract_config_keys('{{ get .Values.config "groq_api_key" }}') == {"groq_api_key"}


def test_extract_mixed_forms() -> None:
    snippet = (
        "{{ .Values.config.anthropic_api_key }}\n"
        '{{ index .Values.config "openai_api_key" }}\n'
        '{{ get .Values.config "groq_api_key" }}\n'
    )
    assert extract_config_keys(snippet) == {
        "anthropic_api_key",
        "openai_api_key",
        "groq_api_key",
    }


# ---------------------------------------------------------------------------
# Fixture plumbing
# ---------------------------------------------------------------------------
FIXTURE_FILES = (OPENHANDS_REL, SECRETS_REL, TEMPLATE_REL)


def _make_fixture_root(tmp_path: Path) -> Path:
    for rel in FIXTURE_FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((REPO_ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def _read(root: Path, rel: Path) -> str:
    return (root / rel).read_text(encoding="utf-8")


def _write(root: Path, rel: Path, text: str) -> None:
    (root / rel).write_text(text, encoding="utf-8")


def _mutate_annotation_line(text: str, fn) -> str:
    """Apply fn to the single checksum/litellm-credentials line and return text."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if "checksum/litellm-credentials:" in line:
            new_line = fn(line)
            assert new_line != line, "annotation line mutation was a no-op"
            lines[i] = new_line
            return "".join(lines)
    raise AssertionError("checksum/litellm-credentials line not found")


def _remove_checksum_option(text: str, option: str) -> str:
    def fn(line: str) -> str:
        out = line.replace(f' (ConfigOption "{option}")', "", 1)
        assert out != line, f"option {option} not found in annotation"
        # drop one %s so the printf format stays plausible (checker ignores arity)
        return out.replace("%s|", "", 1)

    return _mutate_annotation_line(text, fn)


def _add_checksum_option(text: str, option: str) -> str:
    def fn(line: str) -> str:
        # insert the ConfigOption just before the final `)) }}` and add a %s
        out = line.replace(') }}\'', f' (ConfigOption "{option}")) }}}}\'', 1)
        return out.replace('%s"', '%s|%s"', 1)

    return _mutate_annotation_line(text, fn)


# ---------------------------------------------------------------------------
# Control: unmodified fixture must be clean
# ---------------------------------------------------------------------------
def test_fixture_control_is_clean(tmp_path: Path) -> None:
    root = _make_fixture_root(tmp_path)
    assert check(root) == []


# ---------------------------------------------------------------------------
# Negative fixtures
# ---------------------------------------------------------------------------
def test_missing_mapping(tmp_path: Path) -> None:
    root = _make_fixture_root(tmp_path)
    text = _read(root, OPENHANDS_REL)
    _write(root, OPENHANDS_REL, _remove_checksum_option(text, "anthropic_api_key"))
    errors = check(root)
    assert any("coverage gap" in e and "anthropic_api_key" in e for e in errors), errors


def test_changed_indirection(tmp_path: Path) -> None:
    root = _make_fixture_root(tmp_path)
    text = _read(root, SECRETS_REL)
    # re-point bedrock_model_id to an unrelated ConfigOption
    new = re.sub(
        r"^(\s*bedrock_model_id:).*$",
        lambda m: m.group(1) + " '{{repl ConfigOption \"some_other_option\"}}'",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    assert new != text
    _write(root, SECRETS_REL, new)
    errors = check(root)
    assert any(
        "unrecognized mapping" in e and "bedrock_model_id" in e and "some_other_option" in e
        for e in errors
    ), errors


def test_new_dependency(tmp_path: Path) -> None:
    root = _make_fixture_root(tmp_path)
    tmpl = _read(root, TEMPLATE_REL)
    tmpl += (
        "\n  {{- if .Values.config.newprovider_api_key }}\n"
        "  NEWPROVIDER_API_KEY: {{ .Values.config.newprovider_api_key | b64enc | quote }}\n"
        "  {{- end }}\n"
    )
    _write(root, TEMPLATE_REL, tmpl)
    secrets = _read(root, SECRETS_REL)
    secrets = secrets.replace(
        '      custom_api_key:',
        "      newprovider_api_key: '{{repl ConfigOption \"newprovider_api_key\"}}'\n"
        "      custom_api_key:",
        1,
    )
    _write(root, SECRETS_REL, secrets)
    errors = check(root)
    assert any(
        "coverage gap" in e and "newprovider_api_key" in e for e in errors
    ), errors
    # the intended diagnostic must not be the ONLY message when count also fires
    assert not (len(errors) == 1 and "dependency-count backstop" in errors[0]), errors


def test_unresolvable_key(tmp_path: Path) -> None:
    root = _make_fixture_root(tmp_path)
    tmpl = _read(root, TEMPLATE_REL)
    tmpl += (
        "\n  {{- if .Values.config.phantom_api_key }}\n"
        "  PHANTOM_API_KEY: {{ .Values.config.phantom_api_key | b64enc | quote }}\n"
        "  {{- end }}\n"
    )
    _write(root, TEMPLATE_REL, tmpl)
    errors = check(root)
    assert any(
        "no spec.values.config entry" in e and "phantom_api_key" in e for e in errors
    ), errors
    assert not (len(errors) == 1 and "dependency-count backstop" in errors[0]), errors


def test_over_broad(tmp_path: Path) -> None:
    root = _make_fixture_root(tmp_path)
    text = _read(root, OPENHANDS_REL)
    _write(root, OPENHANDS_REL, _add_checksum_option(text, "slack_signing_secret"))
    errors = check(root)
    assert any(
        "over-broad checksum" in e and "slack_signing_secret" in e for e in errors
    ), errors


def test_indirect_map_access(tmp_path: Path) -> None:
    root = _make_fixture_root(tmp_path)
    tmpl = _read(root, TEMPLATE_REL)
    new = tmpl.replace(
        "MISTRAL_API_KEY: {{ .Values.config.mistral_api_key | b64enc | quote }}",
        'MISTRAL_API_KEY: {{ index .Values "config" "mistral_api_key" | b64enc | quote }}',
        1,
    )
    assert new != tmpl
    _write(root, TEMPLATE_REL, new)
    errors = check(root)
    assert any(
        "indirect map access" in e and str(TEMPLATE_REL) in e and re.search(r":\d+:", e)
        for e in errors
    ), errors


def test_dynamic_key_access(tmp_path: Path) -> None:
    root = _make_fixture_root(tmp_path)
    tmpl = _read(root, TEMPLATE_REL)
    new = tmpl.replace(
        "DEEPSEEK_API_KEY: {{ .Values.config.deepseek_api_key | b64enc | quote }}",
        "DEEPSEEK_API_KEY: {{ index .Values.config $providerKey | b64enc | quote }}",
        1,
    )
    assert new != tmpl
    _write(root, TEMPLATE_REL, new)
    errors = check(root)
    assert any(
        "dynamic key access" in e and "not a static literal" in e for e in errors
    ), errors


def test_aliased_context(tmp_path: Path) -> None:
    root = _make_fixture_root(tmp_path)
    tmpl = _read(root, TEMPLATE_REL)
    new = tmpl.replace("type: Opaque\n", "type: Opaque\n{{- $cfg := .Values.config }}\n", 1)
    new = new.replace(
        "DEEPSEEK_API_KEY: {{ .Values.config.deepseek_api_key | b64enc | quote }}",
        "DEEPSEEK_API_KEY: {{ $cfg.deepseek_api_key | b64enc | quote }}",
        1,
    )
    assert new != tmpl
    _write(root, TEMPLATE_REL, new)
    errors = check(root)
    assert any(
        "template-structure violation" in e and "assignment binding" in e for e in errors
    ), errors


# ---------------------------------------------------------------------------
# CLI integration (exercise main(), printing, exit-code mapping)
# ---------------------------------------------------------------------------
def test_cli_success(tmp_path: Path) -> None:
    root = _make_fixture_root(tmp_path)
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(root)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK:" in result.stdout


def test_cli_failure(tmp_path: Path) -> None:
    root = _make_fixture_root(tmp_path)
    text = _read(root, OPENHANDS_REL)
    _write(root, OPENHANDS_REL, _remove_checksum_option(text, "anthropic_api_key"))
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(root)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "coverage gap" in out and "anthropic_api_key" in out, out


# ---------------------------------------------------------------------------
# CI wiring: assert the workflow route (parse YAML, don't grep)
# ---------------------------------------------------------------------------
def test_ci_workflow_wires_new_checker_and_paths() -> None:
    wf_path = REPO_ROOT / ".github" / "workflows" / "check-secret-checksum.yml"
    wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    # PyYAML parses the bare `on:` key as boolean True.
    triggers = wf.get("on", wf.get(True))
    required_paths = {
        "replicated/config.yaml",
        "replicated/openhands.yaml",
        "replicated/secrets.yaml",
        "charts/openhands-secrets/**",
        "scripts/check_secret_checksum.py",
        "scripts/check_litellm_checksum.py",
        "scripts/test_litellm_credentials_checksum.py",
        ".github/workflows/check-secret-checksum.yml",
    }
    for section in ("pull_request", "push"):
        paths = set(triggers[section].get("paths", []))
        missing = required_paths - paths
        assert not missing, f"{section} path filter missing: {sorted(missing)}"

    # The checker must actually run in the workflow. A substring match against
    # the raw file text is not enough: the path appears in the `paths:` filter
    # comment block too, so it stays satisfied even if the run step is deleted.
    # test_checker_is_actually_run_by_a_workflow_step walks the parsed steps.

    # The tests must be executed. Route: test-scripts.yml must list this
    # workflow in its own paths so an edit here triggers the test job.
    ts_path = REPO_ROOT / ".github" / "workflows" / "test-scripts.yml"
    ts = yaml.safe_load(ts_path.read_text(encoding="utf-8"))
    ts_triggers = ts.get("on", ts.get(True))
    ts_paths = set(ts_triggers["pull_request"].get("paths", []))
    assert ".github/workflows/check-secret-checksum.yml" in ts_paths, (
        "test-scripts.yml must include check-secret-checksum.yml in its paths so "
        "edits to the guard workflow run the wiring assertions."
    )


# ---------------------------------------------------------------------------
# CI wiring: the checker must run as an actual workflow step (walk the steps,
# don't substring-match the file text). Deleting the run step must fail here.
# ---------------------------------------------------------------------------
def test_checker_is_actually_run_by_a_workflow_step() -> None:
    wf_path = REPO_ROOT / ".github" / "workflows" / "check-secret-checksum.yml"
    wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    runs = [
        step.get("run", "")
        for job in wf["jobs"].values()
        for step in job.get("steps", [])
    ]
    assert any("scripts/check_litellm_checksum.py" in r for r in runs), (
        "no workflow step runs scripts/check_litellm_checksum.py"
    )


# ---------------------------------------------------------------------------
# printf separators: the '|' between placeholders is load-bearing. Without it,
# adjacent values can trade a character and leave the hash unchanged.
# ---------------------------------------------------------------------------
def test_printf_format_is_pipe_separated() -> None:
    value = _annotation_value()
    fmt = re.search(r'printf\s+"([^"]*)"', value).group(1)
    n = len(re.findall(r"ConfigOption(?:Data)?\s+\"", value))
    assert fmt == "|".join(["%s"] * n), (
        f"format must be {n} '%s' joined by '|'; without separators two adjacent "
        f"values can trade a character and leave the hash unchanged, so the pod "
        f"never rolls. Got: {fmt!r}"
    )


# ---------------------------------------------------------------------------
# Stage 6: the dependency-count backstop must actually fire when the count
# diverges from EXPECTED_DEPENDENCY_COUNT.
# ---------------------------------------------------------------------------
def test_dependency_count_backstop_fires(tmp_path: Path, monkeypatch) -> None:
    root = _make_fixture_root(tmp_path)
    monkeypatch.setattr(clc, "EXPECTED_DEPENDENCY_COUNT", EXPECTED_DEPENDENCY_COUNT + 1)
    errors = check(root)
    assert any("dependency-count backstop" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Stage 5: the ORIGINAL_INPUTS preservation loop must fire when an original
# input is dropped from the checksum.
# ---------------------------------------------------------------------------
def test_preservation_failure_for_original_input(tmp_path: Path) -> None:
    root = _make_fixture_root(tmp_path)
    text = _read(root, OPENHANDS_REL)
    _write(root, OPENHANDS_REL, _remove_checksum_option(text, "litellm_salt_key"))
    errors = check(root)
    assert any(
        "preservation failure" in e and "litellm_salt_key" in e for e in errors
    ), errors


# ---------------------------------------------------------------------------
# Stage 1: every structure construct in _STRUCTURE_CONSTRUCTS must be rejected,
# not just the assignment-binding case covered by test_aliased_context.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "snippet,label",
    [
        ('{{- include "openhands.helper" . }}', "include"),
        ('{{- define "openhands.helper" }}{{- end }}', "define"),
        ("{{- with .Values.config }}{{- end }}", "with"),
        ("{{- range $k := list }}{{- end }}", "range"),
    ],
)
def test_structure_constructs_are_rejected(tmp_path: Path, snippet: str, label: str) -> None:
    root = _make_fixture_root(tmp_path)
    tmpl = _read(root, TEMPLATE_REL)
    _write(root, TEMPLATE_REL, tmpl + "\n" + snippet + "\n")
    errors = check(root)
    assert any(
        "template-structure violation" in e and label in e for e in errors
    ), errors


# ---------------------------------------------------------------------------
# Stage 2: the generic "unsupported .Values reference" branch must fire for a
# .Values reference that matches no recognised access form.
# ---------------------------------------------------------------------------
def test_unsupported_values_reference(tmp_path: Path) -> None:
    root = _make_fixture_root(tmp_path)
    tmpl = _read(root, TEMPLATE_REL)
    _write(root, TEMPLATE_REL, tmpl + "\n  EXTRA: {{ .Values.global.imageTag | quote }}\n")
    errors = check(root)
    assert any("unsupported .Values reference" in e for e in errors), errors


# ---------------------------------------------------------------------------
# _line_of: diagnostics must point at the correct 1-based line, not off by one.
# ---------------------------------------------------------------------------
def test_diagnostic_line_number_is_correct(tmp_path: Path) -> None:
    root = _make_fixture_root(tmp_path)
    tmpl = _read(root, TEMPLATE_REL)
    bad = '  EXTRA: {{ index .Values "config" "mistral_api_key" }}'
    new = tmpl + "\n" + bad + "\n"
    _write(root, TEMPLATE_REL, new)
    expected_line = new.splitlines().index(bad) + 1
    errors = check(root)
    assert any(
        f"{TEMPLATE_REL}:{expected_line}:" in e and "indirect map access" in e
        for e in errors
    ), (expected_line, errors)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
