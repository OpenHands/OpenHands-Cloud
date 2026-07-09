#!/usr/bin/env python3
"""Wait until container image manifests are visible to registry clients.

This guards chart image-tag bump PRs from racing the image publication that
opened them. Trivy fails immediately when GHCR still returns MANIFEST_UNKNOWN;
waiting here lets the scan start only after the tag is actually pullable.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class ImageWaitError(RuntimeError):
    """Raised when an image manifest never becomes available."""


InspectImage = Callable[[str], CommandResult]
Sleep = Callable[[float], None]
Monotonic = Callable[[], float]


def inspect_with_docker(image_ref: str) -> CommandResult:
    completed = subprocess.run(
        ["docker", "manifest", "inspect", image_ref],
        check=False,
        capture_output=True,
        text=True,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def wait_for_images(
    image_refs: Sequence[str],
    *,
    timeout_seconds: float,
    interval_seconds: float,
    inspect_image: InspectImage = inspect_with_docker,
    sleep: Sleep = time.sleep,
    monotonic: Monotonic = time.monotonic,
) -> None:
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    for image_ref in image_refs:
        deadline = monotonic() + timeout_seconds
        while True:
            result = inspect_image(image_ref)
            if result.returncode == 0:
                print(f"Image available: {image_ref}", flush=True)
                break

            now = monotonic()
            if now >= deadline:
                raise ImageWaitError(
                    f"Timed out waiting for image manifest: {image_ref}\n"
                    f"Last registry response:\n{_command_message(result)}"
                )

            delay = min(interval_seconds, deadline - now)
            print(
                f"Waiting for image manifest: {image_ref} "
                f"(retrying in {delay:g}s)",
                flush=True,
            )
            sleep(delay)


def _command_message(result: CommandResult) -> str:
    message = (result.stderr or result.stdout).strip()
    if not message:
        message = f"command exited with code {result.returncode}"
    if len(message) > 2000:
        message = message[-2000:]
    return message


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wait for container image manifests to be available."
    )
    parser.add_argument("image_refs", nargs="+", help="Image references to inspect.")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=900,
        help="Maximum seconds to wait for each image. Defaults to 900.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=15,
        help="Seconds between registry checks. Defaults to 15.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        wait_for_images(
            args.image_refs,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
        )
    except (ImageWaitError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
