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
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
config_lines = (ROOT / "replicated" / "config.yaml").read_text().splitlines()
openhands_lines = (ROOT / "replicated" / "openhands.yaml").read_text().splitlines()

# Password (secret) field names declared in config.yaml.
password_fields = []
last_name = None
for line in config_lines:
    name_match = re.match(r"\s*- name:\s*(\S+)", line)
    if name_match:
        last_name = name_match.group(1).strip("\"'")
    elif re.match(r"\s*type:\s*password\b", line) and last_name:
        password_fields.append(last_name)

# ConfigOption names referenced inside the secretsChecksum value.
checksum_line = next((l for l in openhands_lines if "secretsChecksum:" in l), "")
covered = set(re.findall(r'ConfigOption\s+"([^"]+)"', checksum_line))

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
