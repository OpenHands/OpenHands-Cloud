"""Deterministic automation entrypoint that exits without a callback.

Runs inside the sandbox with the standard library only. It does its work,
prints its outcome and exits non-zero without ever calling the completion
callback, as a script that crashes before it can report would. No LLM,
repository or credential of the target is involved.
"""

import sys

print("fixture finished its work but never reports it", flush=True)
sys.exit(1)
