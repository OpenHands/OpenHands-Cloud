"""Execute the preview login workflow's authorization filters without credentials."""

import json
import shlex
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/preview-browser-login.yml'


def workflow():
    return YAML(typ='safe').load(WORKFLOW.read_text())


def jq_filter(step_name):
    for job in workflow()['jobs'].values():
        for step in job['steps']:
            if step.get('name') == step_name:
                line = next(line for line in step['run'].splitlines() if line.startswith('jq -e '))
                args = shlex.split(line)
                return args[args.index('--arg') + 3] if '--arg' in args else args[2]
    raise AssertionError(step_name)


def accepts(expression, payload):
    return subprocess.run(
        ['jq', '-e', '--arg', 'sha', 'approved-sha', expression],
        input=json.dumps(payload), text=True, capture_output=True, check=False,
    ).returncode == 0


@pytest.mark.parametrize('change', [{}, {'state': 'closed'}, {'base': {'ref': 'production'}}, {'labels': []}, {'head': {'sha': 'other-sha', 'repo': {'full_name': 'OpenHands/saas-deploy'}}}, {'head': {'sha': 'approved-sha', 'repo': {'full_name': 'attacker/saas-deploy'}}}])
def test_rechecks_target_revision_before_login(change):
    payload = {'state': 'open', 'base': {'ref': 'main'}, 'labels': [{'name': 'feature'}], 'head': {'sha': 'approved-sha', 'repo': {'full_name': 'OpenHands/saas-deploy'}}}
    payload.update(change)
    assert accepts(jq_filter('Recheck preview revision before login'), payload) is (not change)


def test_dispatch_is_main_only_and_never_uploads_session_artifacts():
    data = workflow()
    assert set(data['on']) == {'workflow_dispatch'}
    assert data['jobs']['validate']['if'] == "github.ref == 'refs/heads/main'"
    login = data['jobs']['login']
    assert login['needs'] == 'validate'
    assert login['environment'] == 'preview-browser-tests'
    checkout = next(step for step in login['steps'] if step.get('uses', '').startswith('actions/checkout@'))
    assert checkout['with']['ref'] == '${{ github.sha }}'
    assert checkout['with']['persist-credentials'] is False
    assert not any('upload-artifact' in step.get('uses', '') for step in login['steps'])
    for step in login['steps']:
        if 'run' in step:
            subprocess.run(['bash', '-n'], input=step['run'], text=True, check=True)


def test_private_repo_validation_uses_cross_repo_credential_without_second_approval():
    steps = workflow()['jobs']['validate']['steps']
    assert not any('approval protection' in step.get('name', '') for step in steps)
    target = next(step for step in steps if step.get('id') == 'target')
    assert target['env']['GH_TOKEN'] == '${{ secrets.PAT_TOKEN }}'
    assert "--jq '.private' | grep -qx true" in target['run']
