"""Offline Helm renders for native upgrades and Argo-style reconciliation."""
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / 'charts/openhands'
CRONS = ('maintenanceTasks', 'budgetMaintenance', 'proactiveConvoClean',
         'appConversationStartTaskClean', 'resendSync', 'enrichUserInteractionData',
         'gitlabWebhookInstallation')


def render(*args):
    return subprocess.run(['helm', 'template', 'oh', str(CHART), '--set',
                           'automation.enabled=false', *args], capture_output=True, text=True)


@pytest.mark.parametrize('flags', [[], ['--is-upgrade'], ['--set', 'databaseMigrations.migrate=false']])
def test_default_refuses_enterprise_render(flags):
    result = render(*flags)
    assert result.returncode and 'Settings migration 223 is not rolling-compatible' in result.stderr


def test_migrate_requires_migrator():
    result = render('--set', 'settingsCutover=migrate,databaseMigrations.migrate=false')
    assert result.returncode and 'requires databaseMigrations.migrate=true' in result.stderr


@pytest.mark.parametrize(('phase', 'replicas'), [('drain', [0, 0, 0]), ('migrate', [1, 0, 0]),
                                                  ('confirmed', [15, 5, 1])])
def test_cutover_resources(phase, replicas):
    result = render('--is-upgrade', '--set', ','.join([
        f'settingsCutover={phase}', 'deployment.replicas=15', 'integrationEvents.deployment.replicas=5',
        'autoscaling.enabled=true', *(f'{key}.enabled=true' for key in CRONS)]))
    assert result.returncode == 0, result.stderr
    resources = {(d['kind'], d['metadata']['name']): d['spec']
                 for d in yaml.safe_load_all(result.stdout) if d and 'spec' in d}
    for name, n in zip(('openhands', 'openhands-integrations', 'openhands-mcp'), replicas):
        spec = resources['Deployment', name]
        assert spec['replicas'] == n
        init = spec['template']['spec']['initContainers']
        assert 'alembic upgrade head' in next(c for c in init if c['name'] == 'migrate-db')['args'][0]
    resumed = phase == 'confirmed'
    for key in [('HorizontalPodAutoscaler', 'openhands-hpa'), ('Job', 'oh-budget-preflight'),
                ('Job', 'oh-budget-reconcile-gate')]:
        assert (key in resources) == resumed
    crons = [s for (kind, _), s in resources.items() if kind == 'CronJob' and any(
        'enterprise-server:' in c['image'] for c in s['jobTemplate']['spec']['template']['spec']['containers'])]
    assert len(crons) == len(CRONS)
    assert all(s.get('suspend', False) == (not resumed) for s in crons)
