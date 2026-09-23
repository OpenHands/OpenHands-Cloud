#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyYAML"]
# ///
"""Fail when the charts' agent-server image tag drifts from the SDK the pinned
enterprise-server release was built against.

The enterprise server imports the agent-server as a Python package
(``openhands-agent-server``, published from OpenHands/software-agent-sdk) and
talks to it over HTTP in every sandbox. The client the app ships and the server
image the sandbox runs are the same codebase, so they have to be the same
version: a mismatch is a protocol mismatch.

Two independent pins encode that pair, and nothing links them:

  * ``charts/openhands/values.yaml`` -> ``image.tag`` -- the enterprise-server
    release, which is a tag in the OpenHands/enterprise repo.
  * ``global.agentServerImage.tag`` (plus the image-loader's ``image.tag``) --
    the agent-server image the sandbox runs.

So we resolve the first pin to its commit on the enterprise remote, read the
``openhands-*`` versions that release pinned in its ``pyproject.toml``, and
require the second pin to match. Bumping one without the other is the failure
this catches.

Run it from the chart repo root:

    uv run scripts/check_agent_server_sync.py

Exit status is 0 when the pins agree and 1 when they do not (or when a pin
cannot be read, the enterprise tag does not exist, etc.).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml

# Repo holding the enterprise-server source, in owner/name form. Its git tags
# are the enterprise-server release versions the chart's `image.tag` names.
DEFAULT_ENTERPRISE_REPO = "OpenHands/enterprise"

# Packages published from OpenHands/software-agent-sdk that the enterprise
# server pins. They move together on every SDK bump, and the agent-server image
# is tagged with that same version, so all of them must agree with the chart.
SDK_PACKAGES = ("openhands-agent-server", "openhands-sdk", "openhands-tools")

API_ROOT = "https://api.github.com"

# `1.43.1-python` -> the tag is the SDK version plus an image-variant suffix.
_VARIANT_SUFFIX_RE = re.compile(r"^(?P<version>[^-]+)(?:-(?P<variant>.+))?$")

# `sha-2809208` tags are built from a commit rather than a release, so there is
# no tag to look up on the remote -- the commit itself is the ref.
_SHA_TAG_RE = re.compile(r"^sha-(?P<sha>[0-9a-f]{7,40})$")

# `openhands-agent-server[extra]==1.43.1 ; python_version >= "3.12"`
_REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*==\s*(?P<version>[^\s,;]+)"
)


class CheckError(Exception):
    """A user-facing failure: unreadable pin, missing tag, network error, ..."""


@dataclass(frozen=True)
class Pin:
    """A single scalar in a chart values file, addressed by its key path."""

    path: Path
    keys: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.path} -> .{'.'.join(self.keys)}"


# The enterprise-server release the chart deploys.
ENTERPRISE_SERVER_PIN = Pin(Path("charts/openhands/values.yaml"), ("image", "tag"))

# Every place the agent-server image tag is written down. They are separate
# files with no shared default, so they can drift from each other as well as
# from the SDK, and all of them are checked.
AGENT_SERVER_PINS = (
    Pin(
        Path("charts/openhands/values.yaml"),
        ("global", "agentServerImage", "tag"),
    ),
    Pin(
        Path("charts/openhands/charts/runtime-api/values.yaml"),
        ("global", "agentServerImage", "tag"),
    ),
    Pin(Path("charts/image-loader/values.yaml"), ("image", "tag")),
)


def read_pin(repo_root: Path, pin: Pin) -> str:
    """Return the scalar `pin` addresses, as a string."""
    full_path = repo_root / pin.path
    try:
        document = yaml.safe_load(full_path.read_text())
    except FileNotFoundError as error:
        raise CheckError(f"{pin.path}: no such file") from error
    except yaml.YAMLError as error:
        raise CheckError(f"{pin.path}: not valid YAML: {error}") from error

    node = document
    for index, key in enumerate(pin.keys):
        if not isinstance(node, dict) or key not in node:
            missing = ".".join(pin.keys[: index + 1])
            raise CheckError(f"{pin.path}: no value at .{missing}")
        node = node[key]

    # Only strings, so `tag: 1.10` (a YAML float that str()s back as "1.1")
    # fails here instead of silently comparing the wrong version.
    if not isinstance(node, str):
        raise CheckError(
            f"{pin} is a {type(node).__name__}, expected a quoted string"
        )
    return node


def split_variant(tag: str) -> tuple[str, str | None]:
    """Split an agent-server image tag into its version and variant suffix.

    The image is published once per language runtime (`1.43.1-python`,
    `1.43.1-golang`, ...) off a single SDK release, so only the leading segment
    is comparable to a package version.
    """
    match = _VARIANT_SUFFIX_RE.match(tag)
    if match is None:
        raise CheckError(f"cannot read a version out of agent-server tag {tag!r}")
    return match.group("version"), match.group("variant")


def http_get(url: str, token: str | None, accept: str, missing: str | None = None) -> bytes:
    """GET `url`, raising CheckError on any non-200 response.

    `missing` replaces the message for a 404, which is the one status with a
    cause worth naming: the ref we were told to look up is not on the remote.
    """
    request = urllib.request.Request(url, headers={"Accept": accept})
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404 and missing:
            raise CheckError(missing) from error
        raise CheckError(f"GET {url} failed: HTTP {error.code} {error.reason}") from error
    except urllib.error.URLError as error:
        raise CheckError(f"GET {url} failed: {error.reason}") from error


def resolve_enterprise_ref(repo: str, tag: str, token: str | None) -> tuple[str, str]:
    """Resolve the enterprise-server pin to a commit on the remote.

    Returns the commit sha and a human-readable description of how we got there.
    A `sha-<commit>` tag names its commit directly; anything else has to exist as
    a git tag in `repo`.
    """
    sha_match = _SHA_TAG_RE.match(tag)
    if sha_match:
        sha = sha_match.group("sha")
        # Round-trip through the remote so a tag naming a commit that was never
        # pushed (or was force-pushed away) fails here rather than on the fetch.
        payload = json.loads(
            http_get(
                f"{API_ROOT}/repos/{repo}/commits/{sha}",
                token,
                "application/vnd.github+json",
                missing=(
                    f"{ENTERPRISE_SERVER_PIN} is {tag}, but commit {sha} does not "
                    f"exist in {repo}"
                ),
            )
        )
        return payload["sha"], f"commit {sha} in {repo}"

    payload = json.loads(
        http_get(
            f"{API_ROOT}/repos/{repo}/git/ref/tags/{tag}",
            token,
            "application/vnd.github+json",
            missing=(
                f"{ENTERPRISE_SERVER_PIN} is {tag}, but {repo} has no tag {tag}. "
                f"The enterprise-server pin must name a released version."
            ),
        )
    )
    obj = payload["object"]
    if obj["type"] == "tag":
        # Annotated tag: one more hop to reach the commit.
        annotated = json.loads(
            http_get(
                f"{API_ROOT}/repos/{repo}/git/tags/{obj['sha']}",
                token,
                "application/vnd.github+json",
            )
        )
        return annotated["object"]["sha"], f"tag {tag} in {repo}"
    return obj["sha"], f"tag {tag} in {repo}"


def read_sdk_pins(repo: str, ref: str, token: str | None) -> dict[str, str]:
    """Return the `SDK_PACKAGES` versions `repo` pins at `ref`.

    `[project].dependencies` is the pin uv installs from; the mirrored
    `[tool.poetry.dependencies]` block is deliberately ignored so there is one
    source of truth.
    """
    raw = http_get(
        f"{API_ROOT}/repos/{repo}/contents/pyproject.toml?ref={ref}",
        token,
        "application/vnd.github.raw+json",
        missing=f"{repo}@{ref} has no pyproject.toml",
    )
    try:
        pyproject = tomllib.loads(raw.decode())
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise CheckError(f"{repo}@{ref}: pyproject.toml is not valid TOML: {error}") from error

    dependencies = pyproject.get("project", {}).get("dependencies")
    if not isinstance(dependencies, list):
        raise CheckError(f"{repo}@{ref}: pyproject.toml has no [project].dependencies list")

    found: dict[str, str] = {}
    for requirement in dependencies:
        if not isinstance(requirement, str):
            continue
        match = _REQUIREMENT_RE.match(requirement.strip())
        if match is None:
            continue
        name = match.group("name").lower().replace("_", "-")
        if name in SDK_PACKAGES:
            found[name] = match.group("version")

    missing = [package for package in SDK_PACKAGES if package not in found]
    if missing:
        raise CheckError(
            f"{repo}@{ref}: pyproject.toml [project].dependencies pins no exact "
            f"version for {', '.join(missing)}"
        )
    return found


def single_value(label: str, values: dict[str, str]) -> str:
    """Return the one distinct value in `values`, or fail describing the split."""
    distinct = sorted(set(values.values()))
    if len(distinct) > 1:
        detail = "\n".join(f"  {key}: {value}" for key, value in sorted(values.items()))
        raise CheckError(f"{label} disagree with each other:\n{detail}")
    return distinct[0]


def check(repo_root: Path, enterprise_repo: str, token: str | None) -> list[str]:
    """Run the check. Returns the report lines; raises CheckError on mismatch."""
    enterprise_tag = read_pin(repo_root, ENTERPRISE_SERVER_PIN)

    chart_tags = {str(pin): read_pin(repo_root, pin) for pin in AGENT_SERVER_PINS}
    chart_tag = single_value("chart agent-server image tags", chart_tags)
    chart_version, variant = split_variant(chart_tag)

    ref, ref_description = resolve_enterprise_ref(enterprise_repo, enterprise_tag, token)
    sdk_pins = read_sdk_pins(enterprise_repo, ref, token)
    sdk_version = single_value(f"{enterprise_repo}@{ref[:7]} SDK pins", sdk_pins)

    report = [
        f"enterprise-server pin:  {enterprise_tag}  ({ref_description}, {ref[:7]})",
        f"software-agent-sdk pin: {sdk_version}  (openhands-agent-server in pyproject.toml)",
        f"chart agent-server tag: {chart_tag}",
    ]

    if chart_version != sdk_version:
        expected = f"{sdk_version}-{variant}" if variant else sdk_version
        pin_list = "\n".join(f"  {pin}: {tag}" for pin, tag in sorted(chart_tags.items()))
        raise CheckError(
            f"agent-server {chart_version} is pinned in the charts, but "
            f"enterprise-server {enterprise_tag} was built against "
            f"software-agent-sdk {sdk_version}.\n\n"
            f"The enterprise server's agent-server client and the agent-server "
            f"image the sandbox runs must be the same version.\n\n"
            f"Fix: set every agent-server tag below to {expected}, or move "
            f"{ENTERPRISE_SERVER_PIN} to an enterprise-server release that pins "
            f"software-agent-sdk {chart_version}.\n{pin_list}"
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Chart repo root to read the pins from (default: cwd).",
    )
    parser.add_argument(
        "--enterprise-repo",
        default=DEFAULT_ENTERPRISE_REPO,
        help=f"Enterprise-server repo in owner/name form (default: {DEFAULT_ENTERPRISE_REPO}).",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    try:
        for line in check(args.repo_root, args.enterprise_repo, token):
            print(line)
    except CheckError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("\nagent-server and software-agent-sdk pins are in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
