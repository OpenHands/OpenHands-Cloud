import json
import pathlib
import sys
import tarfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import sandbox_capacity_report as report

GI = 1024**3
MI = 1024**2


def _pod(name, uid, node, requests, limits=None, labels=None, phase='Running'):
    return {
        'metadata': {'name': name, 'uid': uid, 'labels': labels or {}},
        'spec': {'nodeName': node, 'containers': [{'resources': {'requests': requests, 'limits': limits or {}}}]},
        'status': {'phase': phase},
    }


def _write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))


def make_bundle(root: pathlib.Path, sandboxes=5, cap='0', eph_used=200 * MI, oom=False, pending=True):
    node = 'node-a'
    _write(root, 'b/cluster-resources/nodes.json', {'items': [{
        'metadata': {'name': node},
        'status': {'allocatable': {'cpu': '16', 'memory': '63Gi', 'ephemeral-storage': '180Gi'}},
    }]})
    pods = [_pod('openhands-app', 'app-uid', node, {'cpu': '3', 'memory': '6Gi'})]
    metrics = [{'podRef': {'name': 'openhands-app', 'uid': 'app-uid'}, 'memory': {'workingSetBytes': 6 * GI}}]
    probe, volumes, pvs = {}, {}, []
    for i in range(sandboxes):
        uid = f'00000000-0000-0000-0000-00000000000{i}'
        pods.append(_pod(f'runtime-r{i}-abc', uid, node,
                         {'cpu': '100m', 'memory': '100Mi', 'ephemeral-storage': '10Gi'},
                         {'cpu': '1', 'memory': '2048Mi', 'ephemeral-storage': '10Gi'},
                         {'runtime_id': f'r{i}'}))
        metrics.append({'podRef': {'name': f'runtime-r{i}', 'uid': uid}, 'memory': {'workingSetBytes': 500 * MI},
                        'cpu': {'usageNanoCores': 200_000_000}, 'ephemeral-storage': {'usedBytes': eph_used}})
        probe[uid] = {'memory_peak': 900 * MI, 'anon': 400 * MI, 'oom_kill': 1 if (oom and i == 0) else 0,
                      'nr_periods': 1000, 'nr_throttled': 50}
        volumes[f'pvc-{i}'] = 1024 * 1024  # 1 GiB in KiB
        pvs.append({'spec': {'claimRef': {'name': f'runtime-r{i}'}, 'local': {'path': f'/var/lib/x/pvc-{i}'}}})
    if pending:
        pods.append(_pod('runtime-pending', 'p-uid', None,
                         {'cpu': '100m', 'memory': '100Mi', 'ephemeral-storage': '10Gi'},
                         labels={'runtime_id': 'p'}, phase='Pending'))
    _write(root, 'b/cluster-resources/pods/openhands.json', {'items': pods})
    _write(root, 'b/cluster-resources/pods/empty.json', {'items': None})
    _write(root, 'b/cluster-resources/pvs.json', {'items': pvs})
    _write(root, f'b/node-metrics/{node}.json', {
        'node': {'nodeName': node, 'memory': {'workingSetBytes': 10 * GI},
                 'fs': {'capacityBytes': 200 * GI, 'availableBytes': 120 * GI}},
        'pods': metrics,
    })
    _write(root, f'b/sandbox-capacity-node-probe/{node}.log', json.dumps({
        'node': node, 'cpus': 16, 'meminfo_kb': {'MemTotal': 64 * 1024 * 1024},
        'summary': {'oom_kill_pods': int(oom), 'memory_pressure_avg300_pct': 0, 'cpu_pressure_avg300_pct': 3},
        'pods': probe, 'local_volumes_kb': volumes,
    }) + '\n')
    _write(root, 'b/app/rt/ns/pod/runtime-api-db-diagnostics-stdout.txt', '\n'.join([
        '== runtimes daily peak concurrent (approx, 30d, 10-min samples) ==',
        '["day", "peak_concurrent"]',
        '{"day": "2026-09-01", "peak_concurrent": 12}',
        '{"day": "2026-09-02", "peak_concurrent": 18}',
        '',
        '== runtimes session length minutes (30d, finished) ==',
        'ERROR: boom',
    ]))
    _write(root, 'b/app/oh/ns/pod/openhands-db-diagnostics-stdout.txt', '\n'.join([
        '== sandboxes started per user (30d) ==',
        '["created_by_user_id", "sandboxes", "max_started_in_one_hour"]',
        '["u1", "40", 14]',
    ]))
    _write(root, 'b/app/cfg/ns/pod/openhands-config-snapshot-stdout.txt',
           json.dumps({'config': {'OH_SANDBOX_MAX_NUM_SANDBOXES': cap}}, indent=2))
    return root


def test_quantity():
    assert report.quantity('100m') == 0.1
    assert report.quantity('10Gi') == 10 * GI
    assert report.quantity('32207104Ki') == 32207104 * 1024
    assert report.quantity('238632394357') == 238632394357
    assert report.quantity(None) == 0.0


def test_sections_handles_dict_list_and_error_rows():
    text = '== a ==\n["x", "y"]\n{"x": 1, "y": 2}\n\n== b ==\n["x"]\n[3]\nERROR: nope\n'
    assert report.sections(text) == {'a': [{'x': 1, 'y': 2}], 'b': [{'x': 3}]}


def test_analyze_for_target(tmp_path):
    a = report.analyze(report.Bundle(str(make_bundle(tmp_path, oom=True))), 30)
    assert len(a['sandboxes']) == 5
    assert a['pending_sandboxes'] == 1
    assert a['per_user_cap'] == 10 and a['per_user_cap_raw'] == '0'
    node = a['nodes'][0]
    assert node['fit']['ephemeral-storage'] == 18
    assert node['fit']['cpu'] == 130
    assert a['history']['runtime_peak_30d'] == 18
    assert a['history']['user_max_started_one_hour'] == 14
    assert all(s['workspace'] == GI for s in a['sandboxes'])
    assert a['sandboxes'][0]['throttled'] == 0.05

    recs = '\n'.join(report.recommend(a))
    assert 'cap sandboxes at 18 by ephemeral storage' in recs
    assert 'Lowering Ephemeral Storage Size to 2Gi' in recs
    assert 'OOM-killed' in recs
    assert 'effectively 10 per user' in recs and 'at least 30' in recs
    assert 'Raising Memory Request' in recs
    assert '1 sandbox pod(s) were Pending' in recs


def test_no_ephemeral_cut_when_usage_is_high(tmp_path):
    a = report.analyze(report.Bundle(str(make_bundle(tmp_path, eph_used=6 * GI))), 30)
    recs = '\n'.join(report.recommend(a))
    assert 'Lowering Ephemeral' not in recs
    assert 'do not lower it' in recs


def test_explicit_cap_meeting_target_is_quiet(tmp_path):
    a = report.analyze(report.Bundle(str(make_bundle(tmp_path, cap='40'))), 30)
    assert a['per_user_cap'] == 40
    assert 'per user' not in '\n'.join(report.recommend(a))


def test_reads_tarball_and_renders(tmp_path, capsys):
    src = make_bundle(tmp_path / 'src')
    archive = tmp_path / 'bundle.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(src / 'b', arcname='support-bundle')
    assert report.main([str(archive), '--target', '30']) == 0
    out = capsys.readouterr().out
    assert '# Sandbox capacity report' in out
    assert 'Recommendations for 30 concurrent sandboxes' in out


def test_only_pending_sandboxes_still_reports_reservations(tmp_path):
    a = report.analyze(report.Bundle(str(make_bundle(tmp_path, sandboxes=0))), 30)
    recs = '\n'.join(report.recommend(a))
    assert 'Only 0 sandboxes were running' in recs
    assert 'by ephemeral storage' in recs


def test_no_sandboxes(tmp_path):
    a = report.analyze(report.Bundle(str(make_bundle(tmp_path, sandboxes=0, pending=False))), 30)
    assert report.recommend(a) == [
        'No sandbox pods were running, so per-sandbox usage is unknown. Collect the bundle while sandboxes are busy.'
    ]
