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
covered = set(re.findall(r'ConfigOption\s+"([^"]+)"', checksum_expr))

# 3. Every secret field must be covered by the checksum.
missing = [f for f in password_fields if f not in covered]
if missing:
    print(
        "ERROR: these password config fields are not included in secretsChecksum "
        "(replicated/openhands.yaml):"
    )
    for field in missing:
        print(f"  - {field}")
    print(
        "\nAdd each one to the secretsChecksum sha256 so changing it in the admin "
        "console restarts the openhands pod. See scripts/check_secret_checksum.py."
    )
    sys.exit(1)

print(f"OK: all {len(password_fields)} password config fields are covered by secretsChecksum.")
