#!/usr/bin/env python3
"""Fail if the LiteLLM credentials checksum does not cover every provider secret.

Background: LiteLLM receives its provider credentials from the
``litellm-env-secrets`` Kubernetes Secret via ``envFrom.secretRef``
(charts/openhands/values.yaml -> litellm-helm.environmentSecrets). Kubernetes
never restarts a pod when an ``envFrom`` Secret changes, and LiteLLM reads its
env vars only at startup. The only thing that rolls the pod when a provider key
changes is the pod-template annotation ``checksum/litellm-credentials`` in
replicated/openhands.yaml: a KOTS-rendered sha256 over the provider config
values. If a provider field is not part of that hash, changing it in the admin
console silently has no effect until someone manually restarts the pod
(PLTF-3560).

This checker keeps the annotation in sync with the Secret template. It is the
LiteLLM-specific sibling of ``check_secret_checksum.py`` (which guards the
``openhands`` deployment's ``secretsChecksum``). It is intentionally a separate,
importable module so tests can call ``check()`` without side effects.

Discovery is provably complete within the structure it parses: instead of
scanning for known-good patterns, it defines the permitted grammar and requires
every ``.Values`` reference in the Secret template to fall inside it. That
guarantee only holds for a flat template with literal, direct config
references, so the checker also asserts the preconditions that keep it flat
(no helpers, no aliasing) and refuses access forms it cannot statically resolve.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

import yaml

# The Secret template that feeds LiteLLM's provider credentials.
TEMPLATE_REL = pathlib.Path("charts/openhands-secrets/templates/litellm-env-secrets.yaml")
# KOTS HelmChart that derives every chart config value from a ConfigOption.
SECRETS_REL = pathlib.Path("replicated/secrets.yaml")
# KOTS HelmChart that carries the checksum/litellm-credentials annotation.
OPENHANDS_REL = pathlib.Path("replicated/openhands.yaml")

# Number of distinct .Values.config keys the Secret template depends on today.
# This is a backstop against accidental net changes to the dependency set:
# changing it is deliberate and MUST come in the same commit that changes the
# checksum annotation. It is secondary to total-coverage accounting (which
# catches a simultaneous add-plus-remove that leaves the count unchanged), but
# it is a real assertion that independently fails the checker.
EXPECTED_DEPENDENCY_COUNT = 25

# Chart config keys whose value in replicated/secrets.yaml is derived from a
# DIFFERENT ConfigOption than the key name. bedrock_model_id is derived from the
# first non-empty line of the bedrock_model_ids textarea, so the checksum must
# hash bedrock_model_ids (the user-facing input), never bedrock_model_id.
KNOWN_INDIRECTIONS: dict[str, set[str]] = {
    "bedrock_model_id": {"bedrock_model_ids"},
}

# ConfigOptions allowed in the checksum that derivation will never produce.
# litellm_api_key reaches the pod as PROXY_MASTER_KEY through a secretKeyRef to
# the separate lite-llm-api-key Secret (not litellm-env-secrets); a secretKeyRef
# has the same no-auto-restart behavior, so it must stay hashed even though it
# never appears in the template this checker parses.
ALLOWED_EXTRA = {"litellm_api_key"}

# The three inputs the annotation carried before PLTF-3560; asserted present
# independently of derivation so a refactor can never silently drop one.
ORIGINAL_INPUTS = {"litellm_api_key", "litellm_admin_password", "litellm_salt_key"}

# ---------------------------------------------------------------------------
# Grammar
# ---------------------------------------------------------------------------
# The three (and only three) supported access forms:
#   .Values.config.<key>
#   index .Values.config "<key>"
#   get   .Values.config "<key>"
_DOTTED = re.compile(r"\.Values\.config\.([A-Za-z0-9_]+)")
_INDEX_GET = re.compile(r"(?:index|get)\s+\.Values\.config\s+\"([^\"]+)\"")

# Any occurrence of .Values at all (for total-coverage accounting).
_ANY_VALUES = re.compile(r"\.Values")

# Explicitly rejected access forms, detected for precise diagnostics.
_INDIRECT_MAP = re.compile(r"index\s+\.Values\s+\"config\"")
_DYNAMIC_KEY = re.compile(r"(?:index|get)\s+\.Values\.config\s+(?!\")\S+")

# Template-structure constructs that move dependencies outside this checker's
# visibility. Their presence is a hard failure, not a silent gap.
_STRUCTURE_CONSTRUCTS = [
    (re.compile(r"\{\{-?\s*include\b"), "include"),
    (re.compile(r"\{\{-?\s*define\b"), "define"),
    (re.compile(r"\{\{-?\s*with\b"), "with"),
    (re.compile(r"\{\{-?\s*range\b"), "range"),
    (re.compile(r"\$\w+\s*:?=\s*\.Values\.config\b"), "an assignment binding .Values.config to a variable"),
]

# ConfigOption / ConfigOptionData references inside a KOTS-rendered value.
_CONFIG_OPTION = re.compile(r"ConfigOption(?:Data)?\s+\"([^\"]+)\"")


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def extract_config_keys(template_text: str) -> set[str]:
    """Return the set of config keys referenced via the supported access forms.

    Pure parser: no validation, no side effects. Recognizes exactly the dotted,
    ``index`` and ``get`` forms and ignores everything else. Callers that need
    completeness guarantees must additionally run the coverage/structure checks.
    """
    keys = set(_DOTTED.findall(template_text))
    keys |= set(_INDEX_GET.findall(template_text))
    return keys


def resolve_config_options(keys: set[str], secrets_doc: dict) -> dict[str, set[str]]:
    """Map each chart key to the ConfigOption(s) its secrets.yaml value reads.

    Only keys that have a ``spec.values.config`` entry appear in the result;
    callers detect unresolved keys by their absence.
    """
    config = (((secrets_doc or {}).get("spec") or {}).get("values") or {}).get("config") or {}
    resolved: dict[str, set[str]] = {}
    for key in keys:
        if key in config:
            value = config[key]
            text = value if isinstance(value, str) else str(value)
            resolved[key] = set(_CONFIG_OPTION.findall(text))
    return resolved


def extract_checksum_options(openhands_doc: dict) -> set[str]:
    """Return the ConfigOption(s) referenced by checksum/litellm-credentials."""
    annotations = (
        (((openhands_doc or {}).get("spec") or {}).get("values") or {})
        .get("litellm-helm", {})
        .get("podAnnotations", {})
    ) or {}
    expr = annotations.get("checksum/litellm-credentials", "")
    return set(_CONFIG_OPTION.findall(expr))


def _check_template_structure(text: str, rel: pathlib.Path) -> list[str]:
    errors: list[str] = []
    for pattern, label in _STRUCTURE_CONSTRUCTS:
        for m in pattern.finditer(text):
            errors.append(
                f"{rel}:{_line_of(text, m.start())}: template-structure violation: "
                f"found {label} ('{m.group(0).strip()}'). This moves config "
                f"dependencies outside the checker's visibility (helpers live in "
                f"files it does not read; aliased/rebound context yields references "
                f"with no literal .Values). Refusing rather than silently "
                f"under-covering. Extend the checker deliberately if this is intended."
            )
    return errors


def _check_coverage(text: str, rel: pathlib.Path) -> list[str]:
    """Total-coverage accounting: every .Values must sit inside a supported form."""
    covered: list[tuple[int, int]] = []
    for m in _DOTTED.finditer(text):
        covered.append((m.start(), m.end()))
    for m in _INDEX_GET.finditer(text):
        covered.append((m.start(), m.end()))

    indirect = [(m.start(), m.end()) for m in _INDIRECT_MAP.finditer(text)]
    dynamic = [(m.start(), m.end()) for m in _DYNAMIC_KEY.finditer(text)]

    def within(pos: int, spans: list[tuple[int, int]]) -> bool:
        return any(s <= pos < e for s, e in spans)

    errors: list[str] = []
    for m in _ANY_VALUES.finditer(text):
        pos = m.start()
        if within(pos, covered):
            continue
        line = _line_of(text, pos)
        snippet = text[pos : pos + 60].splitlines()[0].strip()
        if within(pos, indirect):
            errors.append(
                f"{rel}:{line}: unsupported access form (indirect map access): "
                f"'{snippet}'. Use .Values.config.<key> or index/get "
                f".Values.config \"<key>\"."
            )
        elif within(pos, dynamic):
            errors.append(
                f"{rel}:{line}: unsupported access form (dynamic key access): "
                f"'{snippet}'. The key is not a static literal and cannot be "
                f"statically discovered; refuse rather than guess."
            )
        else:
            errors.append(
                f"{rel}:{line}: unsupported .Values reference: '{snippet}'. Only "
                f".Values.config.<key> and index/get .Values.config \"<key>\" are "
                f"allowed in this template."
            )
    return errors


def check(root: pathlib.Path) -> list[str]:
    """Validate the LiteLLM credentials checksum against its dependencies.

    Returns an aggregated list of error strings (empty when everything is in
    sync). Never calls sys.exit. Errors are collected in this precedence order,
    each stage contributing before the next runs:

      1. template-structure preconditions
      2. access-form grammar / total coverage
      3. key resolution through secrets.yaml
      4. mapping recognition against KNOWN_INDIRECTIONS
      5. coverage against the checksum, plus the over-broad check and preservation
      6. dependency-count backstop (rendered last, but enforced)
    """
    errors: list[str] = []

    template_text = (root / TEMPLATE_REL).read_text(encoding="utf-8")
    secrets_doc = yaml.safe_load((root / SECRETS_REL).read_text(encoding="utf-8"))

    openhands_doc = None
    for doc in yaml.safe_load_all((root / OPENHANDS_REL).read_text(encoding="utf-8")):
        if not isinstance(doc, dict):
            continue
        annotations = (
            ((doc.get("spec") or {}).get("values") or {})
            .get("litellm-helm", {})
            .get("podAnnotations", {})
        ) or {}
        if "checksum/litellm-credentials" in annotations:
            openhands_doc = doc
            break
    if openhands_doc is None:
        errors.append(
            f"{OPENHANDS_REL}: could not find spec.values.litellm-helm.podAnnotations."
            f"checksum/litellm-credentials"
        )

    # 1. template-structure preconditions
    errors += _check_template_structure(template_text, TEMPLATE_REL)

    # 2. access-form grammar / total coverage
    errors += _check_coverage(template_text, TEMPLATE_REL)

    keys = extract_config_keys(template_text)

    # 3. key resolution through secrets.yaml
    resolved = resolve_config_options(keys, secrets_doc)
    for key in sorted(keys):
        if key not in resolved:
            errors.append(
                f"{SECRETS_REL}: chart config key '{key}' (from {TEMPLATE_REL}) has "
                f"no spec.values.config entry, so it cannot be resolved to a "
                f"ConfigOption. Add a mapping."
            )

    # 4. mapping recognition against KNOWN_INDIRECTIONS
    derived: set[str] = set()
    for key in sorted(keys):
        if key not in resolved:
            continue
        opts = resolved[key]
        if opts == {key}:
            derived |= opts
        elif key in KNOWN_INDIRECTIONS and opts == KNOWN_INDIRECTIONS[key]:
            derived |= opts
        else:
            errors.append(
                f"{SECRETS_REL}: unrecognized mapping for chart key '{key}': it "
                f"resolves to {sorted(opts)} but must resolve either to itself "
                f"({{'{key}'}}) or to a declared indirection "
                f"({KNOWN_INDIRECTIONS.get(key, 'none')}). Never map name-to-name; "
                f"if this indirection is intentional add it to KNOWN_INDIRECTIONS."
            )

    # 5. coverage against the checksum, over-broad check, preservation
    if openhands_doc is not None:
        checksum_opts = extract_checksum_options(openhands_doc)
        expected = derived | ALLOWED_EXTRA

        for opt in sorted(expected - checksum_opts):
            errors.append(
                f"{OPENHANDS_REL}: coverage gap: config option '{opt}' feeds "
                f"litellm-env-secrets but is not in checksum/litellm-credentials. "
                f"A change to it will not roll the LiteLLM pod. Add it to the "
                f"sha256 printf."
            )

        for opt in sorted(checksum_opts - expected):
            errors.append(
                f"{OPENHANDS_REL}: over-broad checksum: config option '{opt}' is in "
                f"checksum/litellm-credentials but is not a LiteLLM credential "
                f"dependency (not in derived set ∪ allow-list {sorted(ALLOWED_EXTRA)}). "
                f"An over-broad hash causes needless restart churn. Remove it."
            )

        for opt in sorted(ORIGINAL_INPUTS):
            if opt not in checksum_opts:
                errors.append(
                    f"{OPENHANDS_REL}: preservation failure: original input '{opt}' "
                    f"is no longer in checksum/litellm-credentials. All three "
                    f"original inputs must remain hashed."
                )

    # 6. dependency-count backstop (rendered last, still enforced)
    if len(keys) != EXPECTED_DEPENDENCY_COUNT:
        errors.append(
            f"{TEMPLATE_REL}: dependency-count backstop: found {len(keys)} distinct "
            f".Values.config keys, expected {EXPECTED_DEPENDENCY_COUNT}. If this "
            f"change is intentional, update EXPECTED_DEPENDENCY_COUNT and the "
            f"checksum/litellm-credentials annotation in the same commit."
        )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = pathlib.Path(__file__).resolve().parents[1]
    parser.add_argument(
        "--root",
        type=pathlib.Path,
        default=default_root,
        help="Repository root to validate (defaults to the repo containing this script).",
    )
    args = parser.parse_args(argv)

    errors = check(args.root)
    if errors:
        print("ERROR: checksum/litellm-credentials is out of sync with its dependencies:")
        for err in errors:
            print(f"  - {err}")
        print(
            "\nEvery config value that feeds litellm-env-secrets must be hashed into "
            "checksum/litellm-credentials so changing it in the admin console rolls "
            "the LiteLLM pod. See scripts/check_litellm_checksum.py."
        )
        return 1

    print(
        f"OK: checksum/litellm-credentials covers all {EXPECTED_DEPENDENCY_COUNT} "
        f"LiteLLM credential dependencies (plus allow-listed {sorted(ALLOWED_EXTRA)})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
