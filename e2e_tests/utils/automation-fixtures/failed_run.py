"""Deterministic automation entrypoint for the FAILED-run journey.

Runs inside the sandbox with the standard library only. It raises a known
error and reports FAILED with that error through the completion callback, as
a script automation that hits a real fault would. No LLM, repository or
credential of the target is involved, and no conversation is started.
"""

import json
import os
import sys
import urllib.request

EXPECTED_ERROR = __EXPECTED_ERROR__


def fire_callback(status, error=None):
    body = {"status": status, "run_id": os.environ["AUTOMATION_RUN_ID"]}
    if error:
        body["error"] = error
    request = urllib.request.Request(
        os.environ["AUTOMATION_CALLBACK_URL"],
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.environ["AUTOMATION_CALLBACK_API_KEY"],
        },
    )
    urllib.request.urlopen(request, timeout=30).read()


def main():
    try:
        raise RuntimeError(EXPECTED_ERROR)
    except RuntimeError as exc:
        print(f"fixture failed as designed: {exc}", file=sys.stderr, flush=True)
        fire_callback("FAILED", error=str(exc))
        sys.exit(1)


main()
