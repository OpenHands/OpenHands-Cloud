#!/usr/bin/env python3
"""Archive stale per-branch *feature* channels in Replicated.

Every time a non-main branch is pushed, `make release` promotes a release to a
channel named after the git branch (see the Makefile: CHANNEL := git branch).
Branch names contain a slash (``jl/foo``, ``alona/bar``, ``codex/baz``), so the
channel names do too. Over time these per-branch channels pile up long after the
branch is merged or abandoned. This script archives the dead ones.

A channel is a "feature channel" if its name contains a ``/``. A feature channel
is archived only when EVERY one of the following holds, so we never remove a
channel that anyone is still using:

  1. it is not already archived;
  2. its name contains ``/`` (so the long-lived release channels -- Stable,
     Beta, Unstable, sysbox -- which have no slash, are never candidates);
  3. its name/slug is not in PROTECTED (a redundant, explicit safety net on top
     of rule 2 -- belt and suspenders);
  4. it has zero customers assigned, checked two independent ways: the explicit
     license->channel assignments from ``customer ls`` AND the server-computed
     ``customers.totalCustomers`` rollup on the channel;
  5. it has zero active instances (``numActiveInstances``);
  6. its last activity -- the most recent of the latest release's ``releasedAt``
     and the channel's own ``updated`` timestamp -- is older than --stale-days
     (default 7).

The script is DRY-RUN by default: it prints what it *would* archive and changes
nothing. Pass ``--delete`` (or set ``DRY_RUN=false``) to actually archive.
Replicated "delete" is an archive, which Vendor support can restore, so this is
reversible -- but the guards above are deliberately conservative anyway.

Auth: shells out to the ``replicated`` CLI, which picks up REPLICATED_API_TOKEN
and REPLICATED_APP from the environment exactly like the rest of CI. Nothing in
this file ever reads a token directly.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

# Long-lived channels that must never be touched, matched case-insensitively
# against both the channel name and slug. Rule 2 (must contain "/") already
# excludes all of these; this set is an explicit second line of defence.
PROTECTED = {"stable", "beta", "unstable", "sysbox"}

DEFAULT_APP = os.environ.get("REPLICATED_APP", "openhands")
DEFAULT_STALE_DAYS = int(os.environ.get("STALE_DAYS", "7"))


def run_replicated(args, *, parse_json=True):
    """Run a `replicated` CLI command and return parsed JSON (or raw stdout).

    stderr is left attached to our own stderr so the CLI's "Update available"
    banner and any auth errors are visible in the logs but never pollute the
    JSON we parse from stdout.
    """
    try:
        proc = subprocess.run(
            ["replicated", *args],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        sys.exit("ERROR: the `replicated` CLI is not installed or not on PATH.")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"ERROR: `replicated {' '.join(args)}` failed (exit {exc.returncode}).")

    out = proc.stdout
    if not parse_json:
        return out
    # Be tolerant of any stray banner text that lands on stdout: parse from the
    # first JSON token onward.
    start = min(
        (i for i in (out.find("{"), out.find("[")) if i != -1),
        default=-1,
    )
    if start == -1:
        sys.exit(f"ERROR: no JSON in output of `replicated {' '.join(args)}`.")
    return json.loads(out[start:])


def parse_ts(value):
    """Parse an RFC3339 timestamp; return None for empty/None/unparseable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def resolve_app_id(app):
    """Resolve an app slug or id to its app id via `replicated app ls`."""
    for entry in run_replicated(["app", "ls", "-o", "json"]):
        a = entry.get("app", entry)
        if app in (a.get("slug"), a.get("id"), a.get("name")):
            return a["id"]
    sys.exit(f"ERROR: app {app!r} not found for this API token.")


def assignment_counts():
    """Map channel id -> number of customers whose license is assigned to it."""
    counts = {}
    for cust in run_replicated(["customer", "ls", "-o", "json"]) or []:
        for ch in cust.get("channels") or []:
            cid = ch.get("id")
            if cid:
                counts[cid] = counts.get(cid, 0) + 1
    return counts


def last_activity(channel):
    """Most recent activity timestamp for a channel, or None if unknown.

    Uses the max of every release's releasedAt/created and the channel's own
    updated/created. Taking the max is the conservative choice: it makes a
    channel look as recently active as possible, which can only *prevent*
    deletion, never cause a wrongful one.
    """
    candidates = []
    for rel in channel.get("releases") or []:
        candidates.append(parse_ts(rel.get("releasedAt") or rel.get("created")))
    candidates.append(parse_ts(channel.get("updated") or channel.get("created")))
    candidates = [c for c in candidates if c is not None]
    return max(candidates) if candidates else None


def is_protected(channel):
    name = (channel.get("name") or "").strip().lower()
    slug = (channel.get("channelSlug") or "").strip().lower()
    return name in PROTECTED or slug in PROTECTED


def evaluate(channel, assigned, cutoff):
    """Return (should_archive, reason). reason explains the skip when not."""
    name = channel.get("name") or ""

    if channel.get("isArchived"):
        return False, "already archived"
    if "/" not in name:
        return False, "not a feature channel (no '/')"
    if is_protected(channel):
        return False, "protected channel"

    api_customers = (channel.get("customers") or {}).get("totalCustomers", 0)
    if assigned > 0 or api_customers > 0:
        return False, f"has customers (assigned={assigned}, api={api_customers})"

    active = channel.get("numActiveInstances", 0)
    if active > 0:
        return False, f"has {active} active instance(s)"

    activity = last_activity(channel)
    if activity is None:
        # No timestamp at all is unusual; refuse to delete rather than guess.
        return False, "no activity timestamp available"
    if activity >= cutoff:
        age = datetime.now(timezone.utc) - activity
        return False, f"recent activity ({age.days}d ago)"

    return True, "stale"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Actually archive matching channels. Default is a dry run.",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=DEFAULT_STALE_DAYS,
        help=f"Days without activity before a channel is stale (default {DEFAULT_STALE_DAYS}).",
    )
    parser.add_argument(
        "--app",
        default=DEFAULT_APP,
        help=f"App slug or id (default {DEFAULT_APP!r}, or $REPLICATED_APP).",
    )
    args = parser.parse_args()

    # DRY_RUN=false/0/no in the environment is equivalent to passing --delete.
    env_dry = os.environ.get("DRY_RUN")
    env_force_delete = env_dry is not None and env_dry.strip().lower() in ("false", "0", "no")
    delete = args.delete or env_force_delete

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=args.stale_days)
    mode = "DELETE" if delete else "DRY-RUN"
    print(
        f"[{mode}] app={args.app!r}  stale-days={args.stale_days}  "
        f"cutoff={cutoff.isoformat()}  protected={sorted(PROTECTED)}"
    )

    app_id = resolve_app_id(args.app)
    assigned_by_id = assignment_counts()
    payload = run_replicated(["api", "get", f"/v3/app/{app_id}/channels"])
    channels = payload.get("channels", payload) if isinstance(payload, dict) else payload

    to_archive = []
    for ch in channels:
        assigned = assigned_by_id.get(ch.get("id"), 0)
        should, reason = evaluate(ch, assigned, cutoff)
        if should:
            activity = last_activity(ch)
            age_days = (now - activity).days if activity else None
            to_archive.append((ch, age_days))

    print(f"\nScanned {len(channels)} channels; {len(to_archive)} match all archive criteria.\n")
    if not to_archive:
        print("Nothing to do.")
        return 0

    to_archive.sort(key=lambda item: item[1] if item[1] is not None else -1, reverse=True)
    failures = []
    for ch, age_days in to_archive:
        label = f"{ch['name']} (id={ch['id']}, last activity {age_days}d ago)"
        if not delete:
            print(f"  WOULD ARCHIVE  {label}")
            continue
        try:
            run_replicated(["channel", "rm", ch["id"]], parse_json=False)
            print(f"  ARCHIVED       {label}")
        except SystemExit as exc:
            print(f"  FAILED         {label}: {exc}")
            failures.append(ch["name"])

    print()
    if not delete:
        print(
            f"DRY-RUN: {len(to_archive)} channel(s) would be archived. "
            "Re-run with --delete (or DRY_RUN=false) to apply."
        )
        return 0

    archived = len(to_archive) - len(failures)
    print(f"Archived {archived} channel(s); {len(failures)} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
