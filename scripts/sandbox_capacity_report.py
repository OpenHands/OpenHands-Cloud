#!/usr/bin/env python3
"""Sandbox capacity report from an OpenHands support bundle.

Reads what the bundle's sandbox-capacity collectors gathered (kubelet usage,
the per-node cgroup probe, sandbox history from the databases) and answers:
how many sandboxes fit today, what binds first, and what to change to reach a
target number of concurrent sandboxes.

    python3 scripts/sandbox_capacity_report.py support-bundle.tar.gz --target 30
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
import tarfile
import tempfile

GI = 1024**3
MI = 1024**2
DEFAULT_PER_USER_CAP = 10  # app falls back to this when the setting is 0/unset

_SUFFIX = {
    'Ki': 1024, 'Mi': MI, 'Gi': GI, 'Ti': 1024**4,
    'k': 1e3, 'K': 1e3, 'M': 1e6, 'G': 1e9, 'T': 1e12, 'm': 1e-3,
}


def quantity(value) -> float:
    """Kubernetes quantity -> float (bytes or cores)."""
    if value is None:
        return 0.0
    s = str(value).strip()
    for suffix in sorted(_SUFFIX, key=len, reverse=True):
        if s.endswith(suffix):
            return float(s[: -len(suffix)]) * _SUFFIX[suffix]
    return float(s)


def pct(values, p):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    k = (len(values) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def gib(b):
    return 'n/a' if b is None else f'{b / GI:.1f} GiB'


def mib(b):
    return 'n/a' if b is None else f'{b / MI:.0f} MiB'


# ---------------------------------------------------------------- bundle IO


class Bundle:
    def __init__(self, path: str):
        if os.path.isfile(path):
            self._tmp = tempfile.TemporaryDirectory()
            with tarfile.open(path) as tar:
                try:
                    tar.extractall(self._tmp.name, filter='data')
                except TypeError:  # Python < 3.12
                    tar.extractall(self._tmp.name)
            path = self._tmp.name
        self.root = path

    def find(self, pattern: str) -> list[str]:
        # KOTS bundles embed a stale copy of the last preflight's cluster resources.
        paths = glob.glob(os.path.join(self.root, '**', pattern), recursive=True)
        return sorted(p for p in paths if '/last-preflight-result/' not in p)

    def json(self, pattern: str):
        files = self.find(pattern)
        if not files:
            return None
        with open(files[0]) as f:
            return json.load(f)

    def items(self, pattern: str) -> list[dict]:
        out = {}
        for path in self.find(pattern):
            with open(path) as f:
                data = json.load(f)
            for item in (data.get('items') if isinstance(data, dict) else data) or []:
                meta = item.get('metadata', {})
                out[meta.get('uid') or meta.get('name') or id(item)] = item
        return list(out.values())

    def text(self, pattern: str) -> str:
        # All matches: a bundle may merge the app spec with a standalone capacity spec.
        parts = []
        for path in self.find(pattern):
            with open(path) as f:
                parts.append(f.read())
        return '\n'.join(parts)


def sections(text: str) -> dict[str, list[dict]]:
    """Parse the '== label ==' / header / JSON-row blocks the DB collectors print."""
    out: dict[str, list[dict]] = {}
    label, cols = None, None
    for line in text.splitlines():
        m = re.match(r'^== (.*) ==$', line)
        if m:
            label, cols = m.group(1), None
            out[label] = []
            continue
        if label is None or not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:  # ERROR lines and collector preambles
            continue
        if cols is None and isinstance(row, list):
            cols = row
            continue
        out[label].append(row if isinstance(row, dict) else dict(zip(cols or [], row)))
    return out


# ---------------------------------------------------------------- model


def resources(pod: dict, kind: str) -> dict[str, float]:
    total = {'cpu': 0.0, 'memory': 0.0, 'ephemeral-storage': 0.0}
    for c in pod.get('spec', {}).get('containers', []):
        for r, v in (c.get('resources', {}).get(kind) or {}).items():
            if r in total:
                total[r] += quantity(v)
    return total


def is_sandbox(pod: dict) -> bool:
    return 'runtime_id' in (pod.get('metadata', {}).get('labels') or {})


def active(pod: dict) -> bool:
    return pod.get('status', {}).get('phase') not in ('Succeeded', 'Failed')


def analyze(bundle: Bundle, target: int | None) -> dict:
    nodes = bundle.items('cluster-resources/nodes.json')
    pods = [p for p in bundle.items('cluster-resources/pods/*.json') if active(p)]
    pvs = bundle.items('cluster-resources/pvs.json')

    usage: dict[str, dict] = {}  # pod uid -> kubelet usage
    node_fs: dict[str, dict] = {}
    node_ws: dict[str, float] = {}
    for path in bundle.find('node-metrics/*.json'):
        with open(path) as f:
            summary = json.load(f)
        name = summary['node']['nodeName']
        node_fs[name] = summary['node'].get('fs', {})
        node_ws[name] = summary['node'].get('memory', {}).get('workingSetBytes') or 0
        for p in summary.get('pods', []):
            usage[p['podRef']['uid']] = {
                'mem_ws': (p.get('memory') or {}).get('workingSetBytes'),
                'cpu': ((p.get('cpu') or {}).get('usageNanoCores') or 0) / 1e9,
                'eph_used': (p.get('ephemeral-storage') or {}).get('usedBytes'),
            }

    probe: dict[str, dict] = {}
    probe_nodes: dict[str, dict] = {}
    volumes: dict[str, int] = {}
    for path in bundle.find('sandbox-capacity-node-probe/*.log'):
        with open(path) as f:
            try:
                data = json.loads(f.read().strip().splitlines()[-1])
            except (ValueError, IndexError):
                continue
        probe.update(data.get('pods', {}))
        probe_nodes[data.get('node') or os.path.basename(path)[:-4]] = data
        for name, kb in (data.get('local_volumes_kb') or {}).items():
            if isinstance(kb, int):
                volumes[name] = kb * 1024

    pv_dir_by_claim = {}
    for pv in pvs:
        claim = (pv.get('spec', {}).get('claimRef') or {}).get('name')
        local = (pv['spec'].get('local') or pv['spec'].get('hostPath') or {}).get('path')
        if claim and local:
            pv_dir_by_claim[claim] = os.path.basename(local.rstrip('/'))

    # Sandbox settings come from live sandbox pods (they carry the real reservation).
    sandboxes = [p for p in pods if is_sandbox(p)]
    running = [p for p in sandboxes if p['status'].get('phase') == 'Running']
    pending = [p for p in sandboxes if p['status'].get('phase') == 'Pending' and not p['spec'].get('nodeName')]
    spec_pod = (running or sandboxes or [None])[0]
    per_req = resources(spec_pod, 'requests') if spec_pod else None
    per_lim = resources(spec_pod, 'limits') if spec_pod else None

    sb_rows = []
    for p in running:
        uid = p['metadata']['uid']
        u, c = usage.get(uid, {}), probe.get(uid, {})
        rid = p['metadata']['labels']['runtime_id']
        ws_dir = pv_dir_by_claim.get(f'runtime-{rid}')
        throttled = None
        if c.get('nr_periods'):
            throttled = c['nr_throttled'] / c['nr_periods']
        sb_rows.append({
            'runtime_id': rid, 'node': p['spec'].get('nodeName'),
            'mem_ws': u.get('mem_ws'), 'mem_peak': c.get('memory_peak') if isinstance(c.get('memory_peak'), int) else None,
            'anon': c.get('anon'), 'cpu': u.get('cpu'), 'eph_used': u.get('eph_used'),
            'oom_kill': c.get('oom_kill') or 0, 'throttled': throttled,
            'workspace': volumes.get(ws_dir) if ws_dir else None,
        })

    node_rows = []
    for n in nodes:
        name = n['metadata']['name']
        alloc = {r: quantity(n['status']['allocatable'].get(r)) for r in ('cpu', 'memory', 'ephemeral-storage')}
        on_node = [p for p in pods if p['spec'].get('nodeName') == name]
        other = [p for p in on_node if not is_sandbox(p)]
        other_req = {r: sum(resources(p, 'requests')[r] for p in other) for r in alloc}
        sb_ws = sum((usage.get(p['metadata']['uid'], {}).get('mem_ws') or 0) for p in on_node if is_sandbox(p))
        fit = {}
        if per_req:
            for r in alloc:
                fit[r] = math.floor((alloc[r] - other_req[r]) / per_req[r]) if per_req[r] else None
        fs = node_fs.get(name, {})
        pn = probe_nodes.get(name, {})
        node_rows.append({
            'name': name, 'alloc': alloc, 'other_req': other_req, 'fit': fit,
            'baseline_mem': (node_ws.get(name) or 0) - sb_ws if name in node_ws else None,
            'mem_total': (pn.get('meminfo_kb') or {}).get('MemTotal', 0) * 1024 or None,
            'cpus': pn.get('cpus'),
            'fs_capacity': fs.get('capacityBytes'), 'fs_available': fs.get('availableBytes'),
            'pressure': pn.get('summary', {}),
            'sandboxes': sum(1 for p in on_node if is_sandbox(p)),
        })

    rt = sections(bundle.text('*runtime-api-db-diagnostics-stdout.txt'))
    oh = sections(bundle.text('*openhands-db-diagnostics-stdout.txt'))
    au = sections(bundle.text('*automation-db-diagnostics-stdout.txt'))
    snap = bundle.text('*openhands-config-snapshot-stdout.txt')
    m = re.search(r'"OH_SANDBOX_MAX_NUM_SANDBOXES":\s*"?(\d*)"?', snap)
    raw_cap = m.group(1) if m else None
    per_user_cap = int(raw_cap) if raw_cap and int(raw_cap) > 0 else DEFAULT_PER_USER_CAP

    def peak(rows, key):
        vals = [int(r.get(key) or 0) for r in rows]
        return max(vals) if vals else None

    history = {
        'runtime_peak_30d': peak(rt.get('runtimes daily peak concurrent (approx, 30d, 10-min samples)', []), 'peak_concurrent'),
        'automation_peak_30d': peak(au.get('automation_runs daily peak concurrent (30d, 5-min samples)', []), 'peak_concurrent_runs'),
        'automation_owner_peak_30d': peak(au.get('automation_runs peak concurrent per owner (30d)', []), 'peak_concurrent_runs'),
        'user_max_started_one_hour': peak(oh.get('sandboxes started per user (30d)', []), 'max_started_in_one_hour'),
        'session_minutes': (rt.get('runtimes session length minutes (30d, finished)') or [None])[0],
    }

    return {
        'nodes': node_rows, 'sandboxes': sb_rows, 'pending_sandboxes': len(pending),
        'per_request': per_req, 'per_limit': per_lim, 'per_user_cap': per_user_cap,
        'per_user_cap_raw': raw_cap, 'history': history, 'target': target,
    }


# ---------------------------------------------------------------- advice


def recommend(a: dict) -> list[str]:
    recs: list[str] = []
    req, lim, target, rows = a['per_request'], a['per_limit'], a['target'], a['sandboxes']
    if not req:
        return ['No sandbox pods were running, so per-sandbox usage is unknown. Collect the bundle while sandboxes are busy.']
    n = target or len(rows)
    nodes = a['nodes']

    if len(rows) < 5:
        recs.append(f'Only {len(rows)} sandboxes were running when collected; usage figures are thin. Re-collect during a busy period.')

    # Scheduling reservations (what Kubernetes admits).
    for r, label in (('ephemeral-storage', 'ephemeral storage'), ('memory', 'memory'), ('cpu', 'CPU')):
        fits = sum(max(nd['fit'].get(r) or 0, 0) for nd in nodes)
        if req[r] and target and fits < target:
            fmt = (lambda v: f'{v:.2f} cores') if r == 'cpu' else gib
            recs.append(f'Reservations cap sandboxes at {fits} by {label} (each reserves {fmt(req[r])}); '
                        f'reaching {target} needs {fmt((target - fits) * req[r])} more allocatable {label}.')

    # Ephemeral reservation vs actual use.
    eph = [s['eph_used'] for s in rows if s['eph_used'] is not None]
    if eph and req['ephemeral-storage']:
        top = max(eph)
        size = max(2 * GI, math.ceil(max(top * 3, (pct(eph, 95) or 0) * 4) / GI) * GI)
        if size < req['ephemeral-storage']:
            recs.append(f'Sandboxes use at most {gib(top)} ephemeral storage (p95 {gib(pct(eph, 95))}) of the '
                        f'{gib(req["ephemeral-storage"])} reserved. Lowering Ephemeral Storage Size to '
                        f'{size // GI}Gi keeps 3x headroom over the observed max and frees reservations.')
        elif top > 0.5 * req['ephemeral-storage']:
            recs.append(f'Sandboxes already use up to {gib(top)} of the {gib(req["ephemeral-storage"])} ephemeral '
                        'reservation; do not lower it.')

    # Memory: what the node needs if n sandboxes run at observed levels.
    has_peak = any(s['mem_peak'] for s in rows)
    peaks = [s['mem_peak'] or s['mem_ws'] for s in rows if (s['mem_peak'] or s['mem_ws'])]
    typical = pct([s['mem_ws'] for s in rows], 50)
    total_mem = sum(nd['mem_total'] or nd['alloc']['memory'] for nd in nodes)
    baseline = sum(nd['baseline_mem'] or 0 for nd in nodes)
    if peaks and total_mem:
        busy = baseline + n * pct(peaks, 95)
        steady = baseline + n * (typical or 0)
        basis = 'observed p95 peak' if has_peak else 'p95 current usage (no peak data in this bundle)'
        line = (f'At {n} sandboxes, memory need is about {gib(steady)} typical and {gib(busy)} if all reach the '
                f'{basis} ({mib(pct(peaks, 95))} each), against {gib(total_mem)} on the nodes.')
        if busy > 0.9 * total_mem:
            line += f' Add about {gib(busy - 0.9 * total_mem)} of memory, or cap concurrency, for the busy case.'
        recs.append(line)
        if req['memory'] and typical and req['memory'] < 0.5 * typical:
            recs.append(f'Each sandbox reserves {mib(req["memory"])} memory but typically uses {mib(typical)}. '
                        f'Raising Memory Request toward {mib(typical)} makes Kubernetes stop admitting sandboxes '
                        'before the node runs out, instead of evicting or OOM-killing under load.')
    ooms = sum(1 for s in rows if s['oom_kill'])
    if ooms:
        recs.append(f'{ooms} sandbox(es) had processes OOM-killed at the {mib(lim["memory"])} limit. '
                    'Raise Memory Limit if agents build or test large projects.')

    # CPU contention.
    thr = [s['throttled'] for s in rows if s['throttled'] is not None]
    if thr and pct(thr, 95) > 0.2:
        recs.append(f'p95 sandbox spends {pct(thr, 95):.0%} of CPU periods throttled at the '
                    f'{lim["cpu"]:.2f}-core limit; raise CPU Limit for faster builds.')
    cpus = sum(nd['cpus'] or nd['alloc']['cpu'] for nd in nodes)
    if lim['cpu'] and cpus and n * lim['cpu'] > 2 * cpus:
        recs.append(f'{n} sandboxes at a {lim["cpu"]:.2f}-core limit can demand {n * lim["cpu"]:.0f} cores on '
                    f'{cpus} available. Busy periods will be slower but not fail.')

    # Disk: ephemeral use plus workspaces land on the node root filesystem.
    ws = [s['workspace'] for s in rows if s['workspace'] is not None]
    avail = sum(nd['fs_available'] or 0 for nd in nodes)
    if avail and (ws or eph):
        grow = (n - len(rows)) * ((pct(ws, 95) or 0) + (pct(eph, 95) or 0))
        if grow > 0.8 * avail:
            recs.append(f'{n} sandboxes would add about {gib(grow)} of workspace and scratch data; only '
                        f'{gib(avail)} is free on the node disk. Grow the disk.')

    # Per-user cap.
    cap = a['per_user_cap']
    h = a['history']
    owner_peak = max(h.get('automation_owner_peak_30d') or 0, h.get('user_max_started_one_hour') or 0)
    if target and cap < target:
        recs.append(f'Max Running Sandboxes per User is effectively {cap}. If one account (for example the '
                    f'owner of automations) runs the {target} workloads, its oldest sandboxes get paused. '
                    f'Set it to at least {target}.' + (f' Observed per-owner peak: {owner_peak}.' if owner_peak else ''))
    if a['pending_sandboxes']:
        recs.append(f'{a["pending_sandboxes"]} sandbox pod(s) were Pending (unschedulable) at collection time.')
    return recs


def render(a: dict) -> str:
    out = ['# Sandbox capacity report', '']
    req, lim = a['per_request'], a['per_limit']
    if req:
        out.append(f'Per sandbox: request {req["cpu"]:.2f} CPU / {mib(req["memory"])} / {gib(req["ephemeral-storage"])} '
                   f'ephemeral; limit {lim["cpu"]:.2f} CPU / {mib(lim["memory"])}.')
    out.append(f'Per-user sandbox cap: {a["per_user_cap"]} (setting value: {a["per_user_cap_raw"]!r}).')
    out += ['', '## Nodes', '', '| node | alloc CPU | alloc mem | alloc ephemeral | fits by cpu/mem/eph | disk free | sandboxes | PSI cpu/mem |',
            '|---|---|---|---|---|---|---|---|']
    for nd in a['nodes']:
        f = nd['fit']
        p = nd['pressure'] or {}
        out.append(f'| {nd["name"]} | {nd["alloc"]["cpu"]:.1f} | {gib(nd["alloc"]["memory"])} | '
                   f'{gib(nd["alloc"]["ephemeral-storage"])} | {f.get("cpu")}/{f.get("memory")}/{f.get("ephemeral-storage")} | '
                   f'{gib(nd["fs_available"])} | {nd["sandboxes"]} | '
                   f'{p.get("cpu_pressure_avg300_pct", "?")}%/{p.get("memory_pressure_avg300_pct", "?")}% |')
    rows = a['sandboxes']
    out += ['', f'## Running sandboxes ({len(rows)})', '', '| metric | p50 | p95 | max |', '|---|---|---|---|']
    for key, label, fmt in (('mem_ws', 'memory now', mib), ('mem_peak', 'memory peak', mib), ('anon', 'anon memory now', mib),
                            ('cpu', 'CPU cores now', lambda v: 'n/a' if v is None else f'{v:.2f}'),
                            ('eph_used', 'ephemeral used', gib), ('workspace', 'workspace size', gib),
                            ('throttled', 'CPU periods throttled', lambda v: 'n/a' if v is None else f'{v:.0%}')):
        vals = [s[key] for s in rows]
        out.append(f'| {label} | {fmt(pct(vals, 50))} | {fmt(pct(vals, 95))} | {fmt(pct(vals, 100))} |')
    out.append(f'\nOOM kills: {sum(1 for s in rows if s["oom_kill"])} sandbox(es). '
               f'Pending sandboxes: {a["pending_sandboxes"]}.')
    h = a['history']
    out += ['', '## History (30 days)', '',
            f'- Peak concurrent sandboxes (approx): {h["runtime_peak_30d"]}',
            f'- Peak concurrent automation runs: {h["automation_peak_30d"]}; per single owner: {h["automation_owner_peak_30d"]}',
            f'- Most sandboxes one user started in an hour: {h["user_max_started_one_hour"]}',
            f'- Sandbox running minutes p50/p90/max: {h["session_minutes"]}']
    out += ['', f'## Recommendations{" for " + str(a["target"]) + " concurrent sandboxes" if a["target"] else ""}', '']
    out += [f'- {r}' for r in recommend(a)]
    return '\n'.join(out) + '\n'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('bundle', help='support bundle .tar.gz or extracted directory')
    ap.add_argument('--target', type=int, help='concurrent sandboxes to size for')
    ap.add_argument('--json', action='store_true', help='print the raw analysis as JSON')
    args = ap.parse_args(argv)
    result = analyze(Bundle(args.bundle), args.target)
    if args.json:
        print(json.dumps({**result, 'recommendations': recommend(result)}, indent=2, default=str))
    else:
        sys.stdout.write(render(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
