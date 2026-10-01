#!/usr/bin/env python3
"""Fail if a password-type KOTS config field is missing from secretsChecksum.

Background: secret-backed env vars on the openhands deployment (sourced via
secretKeyRef) are read only at pod start, and Kubernetes does not restart a pod
when a referenced Secret changes. The openhands pod template carries a
checksum/config-secrets annotation whose value (secretsChecksum in
replicated/openhands.yaml) is a KOTS-rendered sha256 of every secret config
value. When a secret changes, the hash changes, the pod template changes, and
the pod rolls so the new secret is picked up.

That only works if every secret (password) field in config.yaml is part of the
hash. If someone adds a new password field but forgets to add it to the hash,
changing it in the admin console silently has no effect until a manual restart.
This check keeps the two in sync, the same way the chart-version check keeps
chart changes and version bumps in sync.

NOTE: this check only blocks a merge if it is configured as a required status
check on the protected branch (ideally strict / require-up-to-date). Otherwise
it is advisory. It also runs on push to main so drift is surfaced immediately.
"""

import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 1. Password (secret) field names declared in config.yaml, parsed structurally
# so reformatting the YAML can't make the check silently miss a field.
config = yaml.safe_load((ROOT / "replicated" / "config.yaml").read_text())
password_fields = []


def collect(items):
    for item in items or []:
        if isinstance(item, dict):
            if item.get("type") == "password" and "name" in item:
                password_fields.append(item["name"])
            collect(item.get("items"))


for group in config.get("spec", {}).get("groups", []):
    collect(group.get("items"))

# 2. ConfigOption names referenced inside the secretsChecksum value.
checksum_expr = ""
for doc in yaml.safe_load_all((ROOT / "replicated" / "openhands.yaml").read_text()):
    if isinstance(doc, dict):
        value = (doc.get("spec", {}).get("values", {}) or {}).get("secretsChecksum")
        if value:
            checksum_expr = value
            break
# Positional order matters: Go's printf silently discards args that run past
# the format string, so an arg beyond position N (where N = count of %s) never
# contributes to the hash. We validate both membership and arity.
ordered_args = re.findall(r'ConfigOption\s+"([^"]+)"', checksum_expr)
covered = set(ordered_args)

# Count %s placeholders in the format string (the first "..." inside printf).
printf_fmt = re.search(r'printf\s+"([^"]*)"', checksum_expr)
num_placeholders = printf_fmt.group(1).count("%s") if printf_fmt else 0
num_args = len(ordered_args)

errors = []

# 3a. Every secret field must appear in the ConfigOption arg list.
missing = [f for f in password_fields if f not in covered]
if missing:
    errors.append(
        "These password config fields are not referenced in secretsChecksum "
        "(replicated/openhands.yaml):\n"
        + "\n".join(f"  - {f}" for f in missing)
        + "\n\nAdd each one so changing it in the admin console restarts the "
        "openhands pod."
    )

# 3b. Arity: Go's printf discards extra args. Fields beyond the last %s are
# silently excluded from the hash, so rotating them in the admin console does
# NOT roll the pod — the new value stays dormant until an unrelated restart.
if num_args != num_placeholders:
    dropped = ordered_args[num_placeholders:]
    errors.append(
        f"secretsChecksum printf has {num_placeholders} '%s' placeholders but "
        f"{num_args} ConfigOption args. Go's printf discards the extras, so "
        "these fields are silently excluded from the hash (changing them does "
        "NOT restart the pod):\n"
        + "\n".join(f"  - position {num_placeholders + i + 1}: {name}"
                    for i, name in enumerate(dropped))
        + "\n\nAdd matching '%s|' entries to the format string so every arg "
        "contributes to the hash."
    )

if errors:
    print("ERROR: " + "\n\nERROR: ".join(errors))
    print("\nSee scripts/check_secret_checksum.py.")
    sys.exit(1)

print(
    f"OK: all {len(password_fields)} password config fields are covered by "
    f"secretsChecksum ({num_placeholders} placeholders / {num_args} args)."
)
