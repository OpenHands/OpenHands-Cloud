#!/usr/bin/env python3
"""Create evaluation secrets without exposing credentials in command arguments.

Requires KUBECONFIG, ANTHROPIC_API_KEY and a private GitHub App credentials JSON.
Does not overwrite existing Secrets. Retain them using your secret manager.
"""
import argparse
import json
import os
import secrets
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--github-credentials', required=True)
args = parser.parse_args()
github = json.loads(Path(args.github_credentials).read_text())
llm_key = os.environ['ANTHROPIC_API_KEY']

def apply(document):
    subprocess.run(['kubectl', 'apply', '-f', '-'], input=json.dumps(document),
                   text=True, check=True)

for namespace in ('openhands', 'openhands-runtimes'):
    apply({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': namespace}})

existing = json.loads(subprocess.check_output(
    ['kubectl', '-n', 'openhands', 'get', 'secrets', '-o', 'json'], text=True))
names = {item['metadata']['name'] for item in existing['items']}
shared_runtime = secrets.token_hex(32)
postgres = secrets.token_hex(32)
access_key = secrets.token_hex(16)
secret_key = secrets.token_hex(32)
documents = {
    'automation-db-secret': {'db-password': secrets.token_hex(32)},
    'automation-service-key': {'automation-service-key': secrets.token_hex(32)},
    'automation-webhook-secret': {'webhook-secret': secrets.token_hex(32)},
    'automation-git-sync-secret': {'git-sync-secret': secrets.token_hex(32)},
    'jwt-secret': {'jwt-secret': secrets.token_hex(32)},
    'keycloak-admin': {'admin-password': secrets.token_hex(32)},
    'keycloak-realm': {'realm-name': 'allhands', 'server-url': 'http://keycloak',
                       'client-id': 'allhands', 'client-secret': secrets.token_hex(32),
                       'smtp-password': ''},
    'postgres-password': {'username': 'postgres', 'password': postgres,
                          'postgres-password': postgres},
    'redis': {'redis-password': secrets.token_hex(32)},
    'lite-llm-api-key': {'lite-llm-api-key': secrets.token_hex(32)},
    'admin-password': {'admin-password': secrets.token_hex(32)},
    'default-api-key': {'default-api-key': shared_runtime},
    'sandbox-api-key': {'sandbox-api-key': shared_runtime},
    'litellm-env-secrets': {'ANTHROPIC_API_KEY': llm_key},
    'github-app': {'app-id': str(github['id']), 'app-slug': github['slug'],
                   'client-id': github['client_id'], 'client-secret': github['client_secret'],
                   'private-key': github['pem'], 'webhook-secret': github['webhook_secret']},
    'filestore-credentials': {'AWS_ACCESS_KEY_ID': access_key,
                              'AWS_SECRET_ACCESS_KEY': secret_key,
                              'RUSTFS_ACCESS_KEY': access_key,
                              'RUSTFS_SECRET_KEY': secret_key},
}
if ('default-api-key' in names) != ('sandbox-api-key' in names):
    raise SystemExit('One Runtime API Secret exists without its matching pair; reconcile before rerunning.')
for name, fields in documents.items():
    if name in names:
        print(f'Secret {name} already exists; retained.')
        continue
    apply({'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
           'metadata': {'name': name, 'namespace': 'openhands'}, 'stringData': fields})
