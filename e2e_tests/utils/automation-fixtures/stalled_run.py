"""Deterministic automation entrypoint for runs that never report back.

Runs inside the sandbox with the standard library only. It prints a
heartbeat and then stalls without calling the completion callback, as a
script stuck on a dead upstream would. It stops on its own after
MAX_STALL_SECONDS, so a run the suite abandons cannot stay active.
"""

import sys
import time

MAX_STALL_SECONDS = __MAX_STALL_SECONDS__


def main():
    deadline = time.time() + MAX_STALL_SECONDS
    print("fixture stalled by design: no completion callback", flush=True)
    while time.time() < deadline:
        time.sleep(1)
    print("fixture stall bound reached", file=sys.stderr, flush=True)
    sys.exit(1)


main()
