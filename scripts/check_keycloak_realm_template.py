#!/usr/bin/env python3
"""Validate invariants in the Keycloak realm chart assets."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REALM_TEMPLATE = (
    REPO_ROOT
    / 'charts'
    / 'openhands'
    / 'files'
    / 'allhands-realm-github-provider.json.tmpl'
)
KEYCLOAK_CONFIG_SCRIPT = (
    REPO_ROOT / 'charts' / 'openhands' / 'templates' / 'keycloak-config-script.yaml'
)


def check_pkce_methods() -> list[str]:
    realm = json.loads(REALM_TEMPLATE.read_text())
    missing_pkce_method = []
    for provider in realm.get('identityProviders', []):
        config = provider.get('config') or {}
        if config.get('pkceEnabled') == 'true' and not config.get('pkceMethod'):
            missing_pkce_method.append(provider.get('alias', '<unknown>'))
    if missing_pkce_method:
        return [
            'Identity providers with pkceEnabled=true must set pkceMethod: '
            + ', '.join(missing_pkce_method)
        ]
    return []


def check_keycloak_error_guard() -> list[str]:
    script_template = KEYCLOAK_CONFIG_SCRIPT.read_text()
    match = re.search(
        r'keycloak_api_call\(\) \{\n(?P<body>.*?)\n    \}',
        script_template,
        re.DOTALL,
    )
    if not match:
        return ['Could not find keycloak_api_call() in keycloak-config-script.yaml']

    body = match.group('body')
    if 'errorMessage' not in body:
        return ['keycloak_api_call() must treat Keycloak errorMessage responses as errors']
    return []


def main() -> int:
    errors = check_pkce_methods() + check_keycloak_error_guard()
    if errors:
        for error in errors:
            print(f'ERROR: {error}', file=sys.stderr)
        return 1

    print('Keycloak realm template checks passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
