#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyYAML"]
# ///
"""Fail unless the fusey agent-server image the charts name is published.

The fuse warm pool (runtime-api ``warmRuntimes.fuse``) runs
``<repository>:<agent-server tag>-fusey<fusey version>``. The charts pin the
agent-server tag and the fusey version separately and derive the image tag. A
chart release must not name an image that does not exist, so this check reads
those pins and asks the registry for the tag, for amd64 and arm64.

runtime-api owns the pin (``fusey-image.env``) and builds the image when it
releases, before the bump PR for this chart opens. This repo only checks that the
image the charts name exists, which catches pins that have drifted from runtime-api.

Run it from the chart repo root:

    uv run scripts/fusey_image.py

Exit status is 0 when the image is published and 1 when it is not or a pin cannot
be read.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED_ARCHITECTURES = frozenset({"amd64", "arm64"})

# The registry serves the multi-arch index under any of these.
INDEX_MEDIA_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)

DEFAULT_REGISTRY_URL = "https://ghcr.io"

BUILD_REPO = "OpenHands/runtime-api"
BUILD_WORKFLOW = "fusey-image.yaml"
BUILD_PIN_FILE = "fusey-image.env"


class FuseyImageError(Exception):
    """A user-facing failure: unreadable pin, pins that disagree, missing image."""


class ImageNotPublishedError(FuseyImageError):
    """The pins are fine, but the registry lacks the image (or an architecture of it)."""


@dataclass(frozen=True)
class Pin:
    """A scalar in a chart values file, addressed by its key path."""

    path: Path
    keys: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.path} -> .{'.'.join(self.keys)}"


# The umbrella chart and the runtime-api subchart each carry a `global` default
# so the subchart renders on its own; the umbrella's wins when both are present.
# Every copy has to agree, or a standalone render names a different image.
VALUES_FILES = (
    Path("charts/openhands/values.yaml"),
    Path("charts/openhands/charts/runtime-api/values.yaml"),
)
REPOSITORY_KEYS = ("global", "fuseyImage", "repository")
VERSION_KEYS = ("global", "fuseyImage", "version")
AGENT_SERVER_TAG_KEYS = ("global", "agentServerImage", "tag")


@dataclass(frozen=True)
class FuseyImage:
    repository: str
    agent_server_tag: str
    fusey_version: str

    @property
    def tag(self) -> str:
        return f"{self.agent_server_tag}-fusey{self.fusey_version}"

    @property
    def reference(self) -> str:
        return f"{self.repository}:{self.tag}"

    @property
    def build_instructions(self) -> str:
        """How to get this image published through runtime-api."""
        return (
            f"{BUILD_REPO} owns this image and builds it when it releases, from "
            f"{BUILD_PIN_FILE}. The charts are ahead of it or out of step with it.\n"
            f"To publish this image, set these in {BUILD_PIN_FILE} in {BUILD_REPO}, "
            f"release it, then bump the runtime-api chart tag here:\n"
            f"  AGENT_SERVER_TAG={self.agent_server_tag}\n"
            f"  FUSEY_VERSION={self.fusey_version}\n\n"
            f"To build it before that release:\n"
            f"  gh workflow run {BUILD_WORKFLOW} --repo {BUILD_REPO} "
            f"-f agent_server_tag={self.agent_server_tag} "
            f"-f fusey_version=v{self.fusey_version}\n\n"
            f"Then rerun this check."
        )


def read_scalar(repo_root: Path, path: Path, keys: tuple[str, ...]) -> str:
    full_path = repo_root / path
    try:
        node = yaml.safe_load(full_path.read_text())
    except FileNotFoundError as error:
        raise FuseyImageError(f"{path}: no such file") from error
    except yaml.YAMLError as error:
        raise FuseyImageError(f"{path}: not valid YAML: {error}") from error

    for index, key in enumerate(keys):
        if not isinstance(node, dict) or key not in node:
            raise FuseyImageError(f"{path}: no value at .{'.'.join(keys[: index + 1])}")
        node = node[key]

    # Strings only: `version: 0.1` is a YAML float that renders back as "0.1"
    # in some places and "0.10" in others, so an unquoted version fails here.
    if not isinstance(node, str):
        raise FuseyImageError(
            f"{Pin(path, keys)} is a {type(node).__name__}, expected a quoted string"
        )
    return node


def single_value(label: str, values: dict[str, str]) -> str:
    distinct = sorted(set(values.values()))
    if len(distinct) > 1:
        detail = "\n".join(f"  {where}: {value}" for where, value in sorted(values.items()))
        raise FuseyImageError(f"{label} disagree with each other:\n{detail}")
    return distinct[0]


def resolve(repo_root: Path) -> FuseyImage:
    """Read the pins from every values file and derive the image."""

    def agreed(label: str, keys: tuple[str, ...]) -> str:
        return single_value(
            label,
            {str(Pin(path, keys)): read_scalar(repo_root, path, keys) for path in VALUES_FILES},
        )

    return FuseyImage(
        repository=agreed("fusey image repositories", REPOSITORY_KEYS),
        agent_server_tag=agreed("agent-server image tags", AGENT_SERVER_TAG_KEYS),
        fusey_version=agreed("fusey versions", VERSION_KEYS),
    )


def registry_path(image: FuseyImage, registry_url: str) -> str:
    """Return the repository path of `image` on the registry, which must host it."""
    host = urllib.parse.urlparse(registry_url).netloc
    prefix = f"{host}/"
    if not image.repository.startswith(prefix):
        raise FuseyImageError(
            f"fusey image repository {image.repository!r} is not on {host}; "
            f"this check only reads {host}"
        )
    return image.repository[len(prefix) :]


def registry_get(url: str, token: str | None, accept: str) -> dict | None:
    """GET a registry JSON document. Returns None for 404, raises on anything else."""
    request = urllib.request.Request(url, headers={"Accept": accept})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise FuseyImageError(f"GET {url} failed: HTTP {error.code} {error.reason}") from error
    except urllib.error.URLError as error:
        raise FuseyImageError(f"GET {url} failed: {error.reason}") from error


def published_architectures(image: FuseyImage, registry_url: str) -> set[str] | None:
    """Return the linux architectures published for `image`, or None if the tag is absent."""
    path = registry_path(image, registry_url)
    token_document = registry_get(
        f"{registry_url}/token?scope=repository:{path}:pull", None, "application/json"
    )
    # A package that was never pushed can answer the token request with 404.
    if token_document is None:
        return None
    index = registry_get(
        f"{registry_url}/v2/{path}/manifests/{image.tag}",
        token_document.get("token"),
        ", ".join(INDEX_MEDIA_TYPES),
    )
    if index is None:
        return None
    return {
        manifest["platform"]["architecture"]
        for manifest in index.get("manifests", [])
        if manifest.get("platform", {}).get("os") == "linux"
    }


def check(repo_root: Path, registry_url: str = DEFAULT_REGISTRY_URL) -> str:
    """Fail unless the derived image is published for every required architecture."""
    image = resolve(repo_root)
    architectures = published_architectures(image, registry_url)
    if architectures is None:
        raise ImageNotPublishedError(
            f"{image.reference} is not published.\n\n"
            f"The charts name it (agent-server {image.agent_server_tag} + fusey "
            f"{image.fusey_version}), but the registry has no such tag.\n\n"
            f"{image.build_instructions}"
        )
    missing = REQUIRED_ARCHITECTURES - architectures
    if missing:
        raise ImageNotPublishedError(
            f"{image.reference} is published without {', '.join(sorted(missing))} "
            f"(found: {', '.join(sorted(architectures)) or 'none'}).\n\n"
            f"{image.build_instructions}"
        )
    return f"{image.reference} is published for {', '.join(sorted(architectures))}."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo-root", type=Path, default=Path.cwd(), help="Chart repo root (default: cwd)."
    )
    parser.add_argument(
        "--registry-url",
        default=DEFAULT_REGISTRY_URL,
        help=f"Registry to query (default: {DEFAULT_REGISTRY_URL}).",
    )
    args = parser.parse_args(argv)

    try:
        print(check(args.repo_root, args.registry_url))
    except FuseyImageError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
