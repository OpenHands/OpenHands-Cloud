#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyGithub", "ruamel.yaml", "requests"]
# ///
"""Update OpenHands chart script."""

import argparse
import base64
import io
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests
from github import Auth, Github
from ruamel.yaml import YAML

# Suppress PyGithub's redirect messages
logging.getLogger("github").setLevel(logging.WARNING)

CLOUD_SEMVER_PATTERN = re.compile(r"^cloud-(\d+\.\d+\.\d+)$")
SHORT_SHA_LENGTH = 7
OPENHANDS_REPO = "All-Hands-AI/OpenHands"
OPENHANDS_ENTERPRISE_REPO = "OpenHands/OpenHands"
DEPLOY_REPO = "OpenHands/deploy"
SANDBOX_SPEC_PATH = "openhands/app_server/sandbox/sandbox_spec_service.py"
AGENT_SERVER_IMAGE_PATTERN = re.compile(r"AGENT_SERVER_IMAGE\s*=\s*'[^:]+:([^']+)'")
SEPARATOR = "=" * 60
SCRIPT_DIR = Path(__file__).parent


def find_repo_root(start: Path) -> Path:
    """Walk up from start to the directory containing the managed trees.

    The path constants below must resolve to the repo root even when the module
    runs from a relocated copy (mutmut executes mutants from a mutants/
    subdirectory). Falls back to the historical scripts/<name>/ grandparent
    layout when no marker directories are found.
    """
    for candidate in (start, *start.parents):
        if (candidate / "charts").is_dir() and (candidate / "replicated").is_dir():
            return candidate
    return start.parent.parent


REPO_ROOT = find_repo_root(SCRIPT_DIR)
CHART_PATH = REPO_ROOT / "charts" / "openhands" / "Chart.yaml"
VALUES_PATH = REPO_ROOT / "charts" / "openhands" / "values.yaml"
RUNTIME_API_CHART_PATH = REPO_ROOT / "charts" / "runtime-api" / "Chart.yaml"
RUNTIME_API_VALUES_PATH = REPO_ROOT / "charts" / "runtime-api" / "values.yaml"
AUTOMATION_CHART_PATH = REPO_ROOT / "charts" / "automation" / "Chart.yaml"
AUTOMATION_VALUES_PATH = REPO_ROOT / "charts" / "automation" / "values.yaml"
IMAGE_LOADER_CHART_PATH = REPO_ROOT / "charts" / "image-loader" / "Chart.yaml"
IMAGE_LOADER_VALUES_PATH = REPO_ROOT / "charts" / "image-loader" / "values.yaml"
REPLICATED_OPENHANDS_PATH = REPO_ROOT / "replicated" / "openhands.yaml"
REPLICATED_CONFIG_PATH = REPO_ROOT / "replicated" / "config.yaml"

# Regex patterns for values.yaml image tag updates
ENTERPRISE_SERVER_TAG_PATTERN = (
    r"(image:\s*\n\s*repository:\s*ghcr\.io/openhands/enterprise-server\s*\n\s*tag:\s*)(\S+)"
)
RUNTIME_TAG_PATTERN = (
    r"(runtime:\s*\n\s*image:\s*\n\s*repository:\s*ghcr\.io/openhands/agent-server\s*\n\s*tag:\s*)(\S+)"
)
WARM_RUNTIMES_TAG_PATTERN = r'(image:\s*"ghcr\.io/openhands/agent-server:)([^"]+)"'
RUNTIME_API_TAG_PATTERN = (
    r'(image:\n\s+repository: ghcr\.io/openhands/runtime-api\n\s+tag: )(sha-[a-f0-9]+)'
)
AUTOMATION_TAG_PATTERN = (
    r'(image:\n\s+repository: ghcr\.io/openhands/automation\n\s+tag: )(sha-[a-f0-9]+)'
)
# The proxy-style refs wrap the agent-server image in the custom_sandbox_image_enabled
# KOTS conditional ({{repl if ...}}...{{repl else}}<proxy image>{{repl end}}), so the
# proxy URL/tag no longer sits flush against the opening quote. Anchor on the
# ghcr.io/openhands/agent-server marker (a single-quoted scalar never contains an inner
# single quote, so [^']* stays within the value) and capture the version with [^'{]+ so
# it stops before the trailing {{repl end}} or closing quote — both of which are then
# left untouched (no replacement_suffix needed). The patterns also match the unwrapped
# form, where [^']* and the optional groups collapse to empty.
REPLICATED_PROXY_AGENT_SERVER_TAG_PATTERN = (
    r"(repository:\s*'[^']*ghcr\.io/openhands/agent-server(?:\{\{repl end\}\})?'\s*\n"
    r"(?:\s*#[^\n]*\n)*"
    r"\s*tag:\s*'(?:[^']*\{\{repl else\}\})?)([^'{]+)"
)
REPLICATED_PROXY_WARM_RUNTIME_IMAGE_PATTERN = (
    r"(image:\s*'[^']*ghcr\.io/openhands/agent-server:)([^'{]+)"
)
REPLICATED_LOCAL_AGENT_SERVER_TAG_PATTERN = (
    r"(repository:\s*'\{\{repl LocalRegistryHost \}\}/\{\{repl LocalRegistryNamespace \}\}/agent-server'\s*\n\s*tag:\s*')([^']+)'"
)
REPLICATED_LOCAL_WARM_RUNTIME_IMAGE_PATTERN = (
    r"(image:\s*'\{\{repl LocalRegistryHost \}\}/\{\{repl LocalRegistryNamespace \}\}/agent-server:)([^']+)'"
)
# Same shape as RUNTIME_TAG_PATTERN but without the runtime: prefix — image-loader's
# values.yaml has the agent-server image at the top level.
IMAGE_LOADER_TAG_PATTERN = (
    r"(image:\s*\n\s*repository:\s*ghcr\.io/openhands/agent-server\s*\n\s*tag:\s*)(\S+)"
)
# The custom_sandbox_image_tag option in replicated/config.yaml shows the agent-server
# tag to admins twice: as the help_text example and as the default value. Both patterns
# anchor on the option name and skip the option's own attribute lines without crossing
# into the next list item (the (?!\s*- name:) guard), so a reordered attribute keeps
# matching while a sibling option's help_text/default can never be picked up instead.
REPLICATED_CONFIG_SANDBOX_HELP_TEXT_PATTERN = (
    r"(- name: custom_sandbox_image_tag\n"
    r"(?:(?!\s*- name:)[^\n]*\n)*?"
    r"\s*help_text: Image tag, e\.g\. )(\S+)"
)
# The default pattern captures only the tag in group 2; the closing quote stays
# outside the capture and is restored by passing replacement_suffix='"'.
REPLICATED_CONFIG_SANDBOX_DEFAULT_PATTERN = (
    r"(- name: custom_sandbox_image_tag\n"
    r"(?:(?!\s*- name:)[^\n]*\n)*?"
    r'\s*default: ")([^"]+)"'
)


@dataclass
class UpdateResult:
    """Stores the outcome of a file update operation."""
    has_changes: bool = False
    changes: list[tuple[str, str, str]] = field(default_factory=list)  # [(key, old, new)]
    unchanged: list[tuple[str, str]] = field(default_factory=list)     # [(key, val)]
    errors: list[str] = field(default_factory=list)                    # [error_message]

    def is_unchanged(self, key: str) -> bool:
        """Check if a key exists in the unchanged list."""
        return any(k == key for k, _ in self.unchanged)

    def has_change_for(self, key: str) -> bool:
        """Check if a key exists in the changes list."""
        return any(k == key for k, _, _ in self.changes)

    def has_error_containing(self, substring: str) -> bool:
        """Check if any error message contains the given substring."""
        return any(substring in err for err in self.errors)

    @property
    def error_count(self) -> int:
        """Return the number of errors recorded."""
        return len(self.errors)

    @property
    def change_count(self) -> int:
        """Return the number of changes recorded."""
        return len(self.changes)

    @property
    def unchanged_count(self) -> int:
        """Return the number of unchanged items recorded."""
        return len(self.unchanged)

    def print_summary(self) -> None:
        """Prints the outcome of the update."""
        for key, old, new in self.changes:
            print(f"Updated {key}: {old} -> {new}")
        for key, val in self.unchanged:
            print(f"{key} unchanged: {val} (already latest)")
        for err in self.errors:
            print(f"Error: {err}")


def get_short_sha(sha: str) -> str:
    """Return the first 7 characters of a SHA hash."""
    return sha[:SHORT_SHA_LENGTH]


def extract_version_from_cloud_tag(cloud_tag: str) -> str | None:
    """Extract version number from cloud-X.Y.Z format."""
    match = CLOUD_SEMVER_PATTERN.match(cloud_tag)
    if match:
        return match.group(1)
    return None


def get_current_app_version(chart_path: Path) -> str | None:
    """Get the current appVersion from a Chart.yaml file."""
    if not chart_path.exists():
        return None
    try:
        yaml = YAML()
        chart_data = yaml.load(chart_path)
        return chart_data.get("appVersion")
    except Exception:
        return None


def format_sha_tag(sha: str) -> str:
    """Format a SHA hash into a sha-SHORT_SHA tag format."""
    return f"sha-{get_short_sha(sha)}"


@dataclass
class DeployConfig:
    """Configuration values from the deploy workflow."""

    runtime_api_sha: str
    automation_sha: str = ""


def get_latest_cloud_tag(token: str, repo_name: str) -> str | None:
    """Fetch the latest cloud-X.Y.Z tag from a GitHub repository."""
    gh = Github(auth=Auth.Token(token))
    try:
        repo = gh.get_repo(repo_name)
        tags = repo.get_tags()
        for tag in tags:
            if CLOUD_SEMVER_PATTERN.match(tag.name):
                return tag.name
    except Exception as e:
        print(f"Error fetching tags from {repo_name}: {e}")
    return None


def cloud_tag_exists(token: str, repo_name: str, tag_name: str) -> bool:
    """Check if a specific cloud tag exists in a GitHub repository."""
    gh = Github(auth=Auth.Token(token))
    try:
        repo = gh.get_repo(repo_name)
        repo.get_git_ref(f"tags/{tag_name}")
        return True
    except Exception:
        return False


def fetch_github_file_content(token: str, repo_name: str, path: str, ref: str | None = None) -> str:
    """Fetch and UTF-8 decode a file from the GitHub contents API.

    Raises on HTTP failure, a missing "content" key, or a decode error; callers
    wrap this in their own try/except to surface a context-specific message.
    """
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://api.github.com/repos/{repo_name}/contents/{path}"
    if ref:
        url += f"?ref={ref}"

    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return base64.b64decode(response.json()["content"]).decode("utf-8")


def get_deploy_config(token: str, repo_name: str, ref: str | None = None) -> DeployConfig | None:
    """Fetch deployment config values from deploy.yaml workflow."""
    try:
        content = fetch_github_file_content(
            token, repo_name, ".github/workflows/deploy.yaml", ref
        )
        yaml = YAML()
        workflow = yaml.load(io.StringIO(content))

        env = workflow.get("env", {})
        return DeployConfig(
            runtime_api_sha=env.get("RUNTIME_API_SHA", ""),
            automation_sha=env.get("AUTOMATION_SHA", ""),
        )
    except Exception as e:
        print(f"Error fetching deploy config: {e}")
        return None


def get_runtime_image_tag_from_sandbox_spec(token: str, repo_name: str, ref: str) -> str | None:
    """Fetch the agent-server image tag from sandbox_spec_service.py at the given cloud tag."""
    try:
        content = fetch_github_file_content(token, repo_name, SANDBOX_SPEC_PATH, ref)
        match = AGENT_SERVER_IMAGE_PATTERN.search(content)
        if not match:
            raise ValueError(f"AGENT_SERVER_IMAGE constant not found in {SANDBOX_SPEC_PATH}")
        return match.group(1)
    except Exception as e:
        print(f"Error fetching sandbox spec: {e}")
        return None


def create_yaml_parser() -> YAML:
    """Create a YAML parser configured for chart file preservation."""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    # Never re-wrap long scalars (e.g. chart descriptions) at the default
    # ~80-char width — that would rewrite untouched lines on every bump.
    yaml.width = 4096
    return yaml


def update_tag_in_content(
    content: str,
    pattern: str,
    new_tag: str,
    tag_name: str,
    result: UpdateResult,
    replacement_suffix: str = "",
    error_if_missing: bool = True,
) -> str:
    """Update a regex-matched tag in content and track the result.

    Args:
        content: The file content to update
        pattern: Regex pattern with group(2) capturing the old tag
        new_tag: The new tag value to set
        tag_name: Human-readable name for reporting (e.g., "enterprise-server image tag")
        result: UpdateResult to record changes/unchanged/errors
        replacement_suffix: Optional suffix to append after new_tag in replacement
        error_if_missing: If True (default), append an error when pattern not found.
            Pass False for optional patterns whose absence is expected (e.g., replicated
            wrapper-only tags that aren't present in upstream values.yaml).

    Returns:
        Updated content string
    """
    match = re.search(pattern, content)
    if match:
        old_tag = match.group(2)
        if old_tag == new_tag:
            result.unchanged.append((tag_name, old_tag))
        else:
            replacement = rf"\g<1>{new_tag}{replacement_suffix}"
            content = re.sub(pattern, replacement, content)
            result.changes.append((tag_name, old_tag, new_tag))
            result.has_changes = True
    elif error_if_missing:
        result.errors.append(f"Could not find {tag_name} in values.yaml")
    return content


def update_dependency(
    chart_data: dict,
    dep_name: str,
    new_version: str | None,
    result: UpdateResult,
) -> None:
    """Update a named dependency version in chart data."""
    if not new_version:
        return
    for dep in chart_data.get("dependencies", []):
        if dep.get("name") == dep_name:
            old_version = dep.get("version")
            result_key = f"{dep_name} version"
            if old_version == new_version:
                result.unchanged.append((result_key, old_version))
            else:
                dep["version"] = new_version
                result.changes.append((result_key, old_version, new_version))
                result.has_changes = True
            break
    else:
        result.errors.append(f"Could not find {dep_name} dependency in Chart.yaml")


def update_all_tags_in_content(
    content: str,
    pattern: str,
    new_tag: str,
    tag_name: str,
    result: UpdateResult,
    replacement_suffix: str = "",
    error_if_missing: bool = True,
) -> str:
    """Update all regex-matched tags in content and track grouped results."""
    matches = list(re.finditer(pattern, content))
    if not matches:
        if error_if_missing:
            result.errors.append(f"Could not find {tag_name} in values.yaml")
        return content

    old_tags = [match.group(2) for match in matches]
    if all(old_tag == new_tag for old_tag in old_tags):
        result.unchanged.append((tag_name, new_tag))
        return content

    replacement = rf"\g<1>{new_tag}{replacement_suffix}"
    content = re.sub(pattern, replacement, content)
    changed_old_tags = sorted({old_tag for old_tag in old_tags if old_tag != new_tag})
    old_value_summary = ", ".join(changed_old_tags)
    result.changes.append((tag_name, old_value_summary, new_tag))
    result.has_changes = True
    return content


def bump_patch_version(version: str) -> str:
    """Bump the patch version of a semantic version string.

    Args:
        version: A semantic version string in X.Y.Z format (e.g., "1.2.3")

    Returns:
        The version with patch incremented (e.g., "1.2.4")

    Raises:
        ValueError: If version is not a valid X.Y.Z semver format
    """
    parts = version.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid semver format: '{version}' (expected X.Y.Z)")

    major, minor, patch = parts

    # Validate all parts are numeric
    try:
        int(major)
        int(minor)
        new_patch = int(patch) + 1
    except ValueError:
        raise ValueError(f"Invalid semver format: '{version}' (all parts must be numeric)")

    return f"{major}.{minor}.{new_patch}"


def update_openhands_chart(
    chart_path: Path,
    new_app_version: str,
    new_runtime_api_version: str | None,
    new_automation_version: str | None = None,
    has_changes: bool = True,
    dry_run: bool = False,
) -> UpdateResult:
    """Update appVersion, bump patch version, and update dependencies.

    Only updates appVersion and bumps version if has_changes is True.
    """
    yaml = create_yaml_parser()
    chart_data = yaml.load(chart_path)
    result = UpdateResult()

    if not has_changes:
        old_version = chart_data.get("version")
        old_app_version = chart_data.get("appVersion")
        result.unchanged.append(("openhands chart version", f"{old_version} (no value changes)"))
        result.unchanged.append(("appVersion", f"{old_app_version} (no value changes)"))
        update_dependency(chart_data, "runtime-api", new_runtime_api_version, result)
        update_dependency(chart_data, "automation", new_automation_version, result)
        if not dry_run and result.has_changes and not result.errors:
            yaml.dump(chart_data, chart_path)
        return result

    old_app_version = chart_data.get("appVersion")
    if old_app_version == new_app_version:
        result.unchanged.append(("appVersion", old_app_version))
    else:
        chart_data["appVersion"] = new_app_version
        result.changes.append(("appVersion", old_app_version, new_app_version))
        result.has_changes = True

    old_version = chart_data.get("version")
    new_version = bump_patch_version(old_version)
    chart_data["version"] = new_version
    result.changes.append(("version", old_version, new_version))
    result.has_changes = True

    update_dependency(chart_data, "runtime-api", new_runtime_api_version, result)
    update_dependency(chart_data, "automation", new_automation_version, result)

    if not dry_run and result.has_changes and not result.errors:
        yaml.dump(chart_data, chart_path)

    return result


def update_openhands_values(
    values_path: Path,
    openhands_version: str,
    runtime_image_tag: str,
    dry_run: bool = False,
) -> UpdateResult:
    """Update image tags in values.yaml using cloud version format.

    Args:
        values_path: Path to the values.yaml file
        openhands_version: The cloud version tag (e.g., 'cloud-1.21.0')
        runtime_image_tag: The agent-server image tag from sandbox spec (e.g., '1.21.0-python')
        dry_run: If True, don't write changes to file

    Returns UpdateResult containing changes made.
    """
    content = values_path.read_text()
    result = UpdateResult()

    content = update_tag_in_content(
        content,
        ENTERPRISE_SERVER_TAG_PATTERN,
        openhands_version,
        "enterprise-server image tag",
        result,
    )
    content = update_tag_in_content(
        content,
        RUNTIME_TAG_PATTERN,
        runtime_image_tag,
        "runtime image tag",
        result,
    )
    content = update_tag_in_content(
        content,
        WARM_RUNTIMES_TAG_PATTERN,
        runtime_image_tag,
        "warmRuntimes image tag",
        result,
        replacement_suffix='"',
    )

    if not dry_run and result.has_changes:
        values_path.write_text(content)

    return result


def update_replicated_openhands_values(
    values_path: Path,
    runtime_image_tag: str,
    dry_run: bool = False,
) -> UpdateResult:
    """Update agent-server image tags in the replicated/openhands.yaml KOTS wrapper.

    The wrapper carries its own copy of the agent-server tag in four locations:
    proxy-style and LocalRegistry-style image refs, each in both the chart-level
    image block and the warmRuntimes default config. The chart-values updater
    cannot reach these because the templating only renders inside the KOTS wrapper.
    """
    content = values_path.read_text()
    result = UpdateResult()

    content = update_all_tags_in_content(
        content,
        REPLICATED_PROXY_AGENT_SERVER_TAG_PATTERN,
        runtime_image_tag,
        "replicated runtime image tag",
        result,
    )
    content = update_tag_in_content(
        content,
        REPLICATED_PROXY_WARM_RUNTIME_IMAGE_PATTERN,
        runtime_image_tag,
        "replicated warmRuntimes image tag",
        result,
    )
    content = update_all_tags_in_content(
        content,
        REPLICATED_LOCAL_AGENT_SERVER_TAG_PATTERN,
        runtime_image_tag,
        "replicated local registry runtime image tag",
        result,
        replacement_suffix="'",
    )
    content = update_tag_in_content(
        content,
        REPLICATED_LOCAL_WARM_RUNTIME_IMAGE_PATTERN,
        runtime_image_tag,
        "replicated local registry warmRuntimes image tag",
        result,
        replacement_suffix="'",
    )

    if not dry_run and result.has_changes:
        values_path.write_text(content)

    return result


def update_replicated_config(
    config_path: Path,
    runtime_image_tag: str,
    dry_run: bool = False,
) -> UpdateResult:
    """Update the sandbox image tag shown in the replicated/config.yaml KOTS config screen.

    The custom_sandbox_image_tag option carries the agent-server tag twice:
    as the help_text example and as the default value admins see when they
    enable a custom sandbox image. Both must track the sandbox spec tag.
    """
    content = config_path.read_text()
    result = UpdateResult()

    content = update_tag_in_content(
        content,
        REPLICATED_CONFIG_SANDBOX_HELP_TEXT_PATTERN,
        runtime_image_tag,
        "replicated config sandbox image tag help text",
        result,
    )
    content = update_tag_in_content(
        content,
        REPLICATED_CONFIG_SANDBOX_DEFAULT_PATTERN,
        runtime_image_tag,
        "replicated config sandbox image tag default",
        result,
        replacement_suffix='"',
    )

    if not dry_run and result.has_changes:
        config_path.write_text(content)

    return result


def update_image_loader_values(
    values_path: Path,
    runtime_image_tag: str,
    dry_run: bool = False,
) -> UpdateResult:
    """Update the agent-server image tag in image-loader values.yaml.

    The image-loader DaemonSet pre-pulls the agent-server image onto nodes;
    its tag must track the sandbox spec tag or nodes warm the wrong image.
    """
    content = values_path.read_text()
    result = UpdateResult()

    content = update_tag_in_content(
        content,
        IMAGE_LOADER_TAG_PATTERN,
        runtime_image_tag,
        "image-loader image tag",
        result,
    )

    if not dry_run and result.has_changes:
        values_path.write_text(content)

    return result


def bump_chart_version(
    chart_path: Path,
    chart_name: str,
    has_changes: bool = True,
    dry_run: bool = False,
) -> tuple[str, UpdateResult]:
    """Bump the patch version of a chart and return the new/current version.

    Only bumps the version if has_changes is True.
    """
    yaml = create_yaml_parser()
    chart_data = yaml.load(chart_path)
    old_version = chart_data.get("version")
    result = UpdateResult()
    result_key = f"{chart_name} chart version"

    if not has_changes:
        result.unchanged.append((result_key, f"{old_version} (no value changes)"))
        return old_version, result

    new_version = bump_patch_version(old_version)
    chart_data["version"] = new_version
    result.changes.append((result_key, old_version, new_version))
    result.has_changes = True

    if not dry_run and result.has_changes:
        yaml.dump(chart_data, chart_path)

    return new_version, result


def update_runtime_api_chart(
    chart_path: Path,
    has_changes: bool = True,
    dry_run: bool = False,
) -> tuple[str, UpdateResult]:
    """Bump the patch version of the runtime-api chart and return the new/current version."""
    return bump_chart_version(chart_path, "runtime-api", has_changes=has_changes, dry_run=dry_run)


def update_runtime_api_values(
    values_path: Path,
    runtime_api_sha: str,
    runtime_image_tag: str,
    dry_run: bool = False,
) -> UpdateResult:
    """Update image tag and warmRuntimes default config image in runtime-api values.yaml.

    Args:
        values_path: Path to the values.yaml file
        runtime_api_sha: The runtime-api commit SHA
        runtime_image_tag: The agent-server image tag from sandbox spec (e.g., '1.21.0-python')
        dry_run: If True, don't write changes to file

    Returns UpdateResult containing changes made.
    """
    content = values_path.read_text()
    result = UpdateResult()

    content = update_tag_in_content(
        content,
        RUNTIME_API_TAG_PATTERN,
        format_sha_tag(runtime_api_sha),
        "runtime-api image tag",
        result,
    )
    content = update_tag_in_content(
        content,
        WARM_RUNTIMES_TAG_PATTERN,
        runtime_image_tag,
        "runtime-api warmRuntimes image tag",
        result,
        replacement_suffix='"',
    )

    if not dry_run and result.has_changes:
        values_path.write_text(content)

    return result


def update_automation_values(
    values_path: Path,
    automation_sha: str,
    dry_run: bool = False,
) -> UpdateResult:
    """Update image tag in automation values.yaml.

    Args:
        values_path: Path to the values.yaml file
        automation_sha: The automation commit SHA from deploy config
        dry_run: If True, don't write changes to file

    Returns UpdateResult containing changes made.
    """
    result = UpdateResult()
    if not automation_sha:
        result.errors.append("AUTOMATION_SHA missing from deploy config")
        return result
    content = values_path.read_text()

    content = update_tag_in_content(
        content,
        AUTOMATION_TAG_PATTERN,
        format_sha_tag(automation_sha),
        "automation image tag",
        result,
    )

    if not dry_run and result.has_changes:
        values_path.write_text(content)

    return result


def print_section_header(title: str) -> None:
    """Print a visually distinct section header."""
    print(SEPARATOR)
    print(title)
    print(SEPARATOR)


def parse_args(args=None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Update OpenHands, runtime-api, automation, and image-loader charts based on a SaaS deploy."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without making changes.",
    )
    parser.add_argument(
        "--cloud-tag",
        type=str,
        default=None,
        help="A cloud tag from OpenHands (e.g., cloud-1.19.0) to use instead of fetching the latest.",
    )
    parser.add_argument(
        "--skip-version-check",
        action="store_true",
        help="Continue even if charts are already up to date, re-fetching and applying image tags.",
    )
    return parser.parse_args(args)


def resolve_openhands_version(token: str, cloud_tag: str | None) -> str | None:
    """Resolve the OpenHands cloud version to use for updates.

    Returns the cloud tag (e.g., 'cloud-1.19.0') or None if resolution fails.
    """
    if cloud_tag:
        print(f"Using specified cloud tag: {cloud_tag}")
        if not cloud_tag_exists(token, OPENHANDS_REPO, cloud_tag):
            print(f"Error: Cloud tag '{cloud_tag}' does not exist in {OPENHANDS_REPO}")
            return None
        return cloud_tag

    openhands_version = get_latest_cloud_tag(token, OPENHANDS_REPO)
    if openhands_version:
        print(f"OpenHands cloud tag: {openhands_version}")
    else:
        print("No cloud tag found in OpenHands releases")
    return openhands_version


def update_runtime_api_workflow(
    deploy_config: DeployConfig,
    runtime_image_tag: str,
    dry_run: bool,
) -> str:
    """Update runtime-api chart and values. Returns the new chart version."""
    print_section_header("Updating runtime-api chart...")

    print("Updating runtime-api values.yaml...")
    values_result = update_runtime_api_values(
        RUNTIME_API_VALUES_PATH,
        deploy_config.runtime_api_sha,
        runtime_image_tag,
        dry_run=dry_run,
    )
    values_result.print_summary()

    print()
    print("Updating runtime-api Chart.yaml...")
    chart_version, chart_result = update_runtime_api_chart(
        RUNTIME_API_CHART_PATH,
        has_changes=values_result.has_changes,
        dry_run=dry_run,
    )
    chart_result.print_summary()

    return chart_version


def update_openhands_workflow(
    deploy_config: DeployConfig,
    openhands_version: str,
    runtime_api_version: str,
    runtime_image_tag: str,
    dry_run: bool,
    automation_version: str | None = None,
) -> None:
    """Update openhands chart and values."""
    print_section_header("Updating openhands chart...")

    print("Updating openhands values.yaml...")
    values_result = update_openhands_values(
        VALUES_PATH,
        openhands_version,
        runtime_image_tag,
        dry_run=dry_run,
    )
    values_result.print_summary()

    print()
    print("Updating replicated/openhands.yaml...")
    replicated_result = update_replicated_openhands_values(
        REPLICATED_OPENHANDS_PATH,
        runtime_image_tag,
        dry_run=dry_run,
    )
    replicated_result.print_summary()

    print()
    print("Updating replicated/config.yaml...")
    replicated_config_result = update_replicated_config(
        REPLICATED_CONFIG_PATH,
        runtime_image_tag,
        dry_run=dry_run,
    )
    replicated_config_result.print_summary()

    print()
    print("Updating openhands Chart.yaml...")
    chart_result = update_openhands_chart(
        CHART_PATH,
        openhands_version,
        runtime_api_version,
        automation_version,
        has_changes=values_result.has_changes,
        dry_run=dry_run,
    )
    chart_result.print_summary()


def update_image_loader_workflow(
    runtime_image_tag: str,
    dry_run: bool,
) -> None:
    """Update image-loader chart values and bump chart version."""
    print_section_header("Updating image-loader chart...")

    print("Updating image-loader values.yaml...")
    values_result = update_image_loader_values(
        IMAGE_LOADER_VALUES_PATH,
        runtime_image_tag,
        dry_run=dry_run,
    )
    values_result.print_summary()

    print()
    print("Updating image-loader Chart.yaml...")
    _, chart_result = bump_chart_version(
        IMAGE_LOADER_CHART_PATH,
        "image-loader",
        has_changes=values_result.has_changes,
        dry_run=dry_run,
    )
    chart_result.print_summary()


def update_automation_workflow(
    deploy_config: DeployConfig,
    dry_run: bool,
) -> str:
    """Update automation chart values and bump chart version. Returns new chart version."""
    print_section_header("Updating automation chart...")

    print("Updating automation values.yaml...")
    values_result = update_automation_values(
        AUTOMATION_VALUES_PATH,
        deploy_config.automation_sha,
        dry_run=dry_run,
    )
    values_result.print_summary()

    print()
    print("Updating automation Chart.yaml...")
    chart_version, chart_result = bump_chart_version(
        AUTOMATION_CHART_PATH,
        "automation",
        has_changes=values_result.has_changes,
        dry_run=dry_run,
    )
    chart_result.print_summary()

    return chart_version


def process_updates(
    token: str,
    dry_run: bool = False,
    cloud_tag: str | None = None,
    skip_version_check: bool = False,
) -> None:
    print_section_header("Fetching latest versions...")

    openhands_version = resolve_openhands_version(token, cloud_tag)
    if not openhands_version:
        return

    current_app_version = get_current_app_version(CHART_PATH)
    if current_app_version:
        print(f"OpenHands-Cloud openhands chart appVersion: {current_app_version}")
        if current_app_version == openhands_version and not skip_version_check:
            print()
            print_section_header("Charts are already up to date - no changes needed")
            return

    version_number = extract_version_from_cloud_tag(openhands_version)
    if not version_number:
        print(f"Could not extract version from cloud tag: {openhands_version}")
        return

    print(f"Using deploy tag: {version_number}")

    runtime_image_tag = get_runtime_image_tag_from_sandbox_spec(
        token, OPENHANDS_ENTERPRISE_REPO, ref=openhands_version
    )
    if not runtime_image_tag:
        print(f"Could not fetch runtime image tag from sandbox spec at {openhands_version}")
        return

    deploy_config = get_deploy_config(token, DEPLOY_REPO, ref=version_number)
    if not deploy_config:
        print(f"Could not fetch deploy config from tag {version_number}")
        return
    # All charts are released together; abort if any deploy-config SHA is missing.
    if not deploy_config.automation_sha:
        print("AUTOMATION_SHA missing from deploy config")
        return

    print(f"Deploy config (from {version_number}):")
    print(f"  RUNTIME_API_SHA: {deploy_config.runtime_api_sha}")
    print(f"  AUTOMATION_SHA: {deploy_config.automation_sha}")
    print(f"  AGENT_SERVER_IMAGE tag (from sandbox spec): {runtime_image_tag}")

    print()
    runtime_api_version = update_runtime_api_workflow(deploy_config, runtime_image_tag, dry_run)

    print()
    automation_version = update_automation_workflow(deploy_config, dry_run)

    print()
    update_image_loader_workflow(runtime_image_tag, dry_run)

    print()
    update_openhands_workflow(
        deploy_config,
        openhands_version,
        runtime_api_version,
        runtime_image_tag,
        dry_run,
        automation_version=automation_version,
    )


def main(dry_run: bool = False, cloud_tag: str | None = None, skip_version_check: bool = False) -> None:
    if dry_run:
        print_section_header("DRY RUN MODE - No changes will be made")
        print()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Environment variable GITHUB_TOKEN is required. Try getting with: gh auth status --show-token")
        return

    process_updates(token, dry_run=dry_run, cloud_tag=cloud_tag, skip_version_check=skip_version_check)


if __name__ == "__main__":
    args = parse_args()
    main(dry_run=args.dry_run, cloud_tag=args.cloud_tag, skip_version_check=args.skip_version_check)
