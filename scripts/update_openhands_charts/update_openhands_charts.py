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
from dataclasses import dataclass
from pathlib import Path

import requests
from github import Auth, Github
from ruamel.yaml import YAML

# Suppress PyGithub's redirect messages
logging.getLogger("github").setLevel(logging.WARNING)

CLOUD_SEMVER_PATTERN = re.compile(r"^cloud-(\d+\.\d+\.\d+)$")
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHORT_SHA_LENGTH = 7
OPENHANDS_REPO = "All-Hands-AI/OpenHands"
DEPLOY_REPO = "OpenHands/deploy"
SEPARATOR = "=" * 60
SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
CHART_PATH = REPO_ROOT / "charts" / "openhands" / "Chart.yaml"
VALUES_PATH = REPO_ROOT / "charts" / "openhands" / "values.yaml"
RUNTIME_API_CHART_PATH = REPO_ROOT / "charts" / "runtime-api" / "Chart.yaml"
RUNTIME_API_VALUES_PATH = REPO_ROOT / "charts" / "runtime-api" / "values.yaml"
AUTOMATION_CHART_PATH = REPO_ROOT / "charts" / "automation" / "Chart.yaml"
AUTOMATION_VALUES_PATH = REPO_ROOT / "charts" / "automation" / "values.yaml"

# Regex patterns for values.yaml image tag updates
ENTERPRISE_SERVER_TAG_PATTERN = (
    r"(image:\s*\n\s*repository:\s*ghcr\.io/openhands/enterprise-server\s*\n\s*tag:\s*)(\S+)"
)
RUNTIME_TAG_PATTERN = (
    r"(runtime:\s*\n\s*image:\s*\n\s*repository:\s*ghcr\.io/openhands/runtime\s*\n\s*tag:\s*)(\S+)"
)
WARM_RUNTIMES_TAG_PATTERN = r'(image:\s*"ghcr\.io/openhands/runtime:)([^"]+)"'
RUNTIME_API_TAG_PATTERN = (
    r'(image:\n\s+repository: ghcr\.io/openhands/runtime-api\n\s+tag: )(sha-[a-f0-9]+)'
)
AUTOMATION_TAG_PATTERN = (
    r'(image:\n\s+repository: ghcr\.io/openhands/automation\n\s+tag: )(\S+)'
)


@dataclass
class UpdateResult:
    """Stores the outcome of a file update operation."""
    has_changes: bool = False
    changes: list[tuple[str, str, str]] = None  # [(key, old, new)]
    unchanged: list[tuple[str, str]] = None     # [(key, val)]
    errors: list[str] = None                    # [error_message]

    def __post_init__(self):
        if self.changes is None:
            self.changes = []
        if self.unchanged is None:
            self.unchanged = []
        if self.errors is None:
            self.errors = []

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


def format_deploy_image_tag(tag_or_sha: str) -> str:
    """Format a deploy config ref as the image tag used by deployment."""
    if FULL_SHA_PATTERN.fullmatch(tag_or_sha):
        return f"sha-{tag_or_sha}"
    return tag_or_sha


@dataclass
class DeployConfig:
    """Configuration values from the deploy workflow."""

    runtime_api_sha: str
    automation_sha: str
    openhands_runtime_image_tag: str


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


def get_deploy_config(token: str, repo_name: str, ref: str | None = None) -> DeployConfig | None:
    """Fetch deployment config values from deploy.yaml workflow."""
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://api.github.com/repos/{repo_name}/contents/.github/workflows/deploy.yaml"
    if ref:
        url += f"?ref={ref}"

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        content = base64.b64decode(response.json()["content"]).decode("utf-8")
        yaml = YAML()
        workflow = yaml.load(io.StringIO(content))

        env = workflow.get("env", {})
        return DeployConfig(
            runtime_api_sha=env.get("RUNTIME_API_SHA", ""),
            automation_sha=env.get("AUTOMATION_SHA", ""),
            openhands_runtime_image_tag=env.get("OPENHANDS_RUNTIME_IMAGE_TAG", ""),
        )
    except Exception as e:
        print(f"Error fetching deploy config: {e}")
        return None


def create_yaml_parser() -> YAML:
    """Create a YAML parser configured for chart file preservation."""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def update_tag_in_content(
    content: str,
    pattern: str,
    new_tag: str,
    tag_name: str,
    result: UpdateResult,
    replacement_suffix: str = "",
) -> str:
    """Update a regex-matched tag in content and track the result.
    
    Args:
        content: The file content to update
        pattern: Regex pattern with group(2) capturing the old tag
        new_tag: The new tag value to set
        tag_name: Human-readable name for reporting (e.g., "enterprise-server image tag")
        result: UpdateResult to record changes/unchanged/errors
        replacement_suffix: Optional suffix to append after new_tag in replacement
        
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
    else:
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
    new_automation_version: str | None,
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
        if not dry_run and result.has_changes:
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

    if not dry_run and result.has_changes:
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
        runtime_image_tag: The runtime image tag from deploy config (e.g., 'cloud-1.21.0-nikolaik')
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
        runtime_image_tag: The runtime image tag from deploy config (e.g., 'cloud-1.21.0-nikolaik')
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
        automation_sha: The automation deploy ref (40-char SHA or direct tag)
        dry_run: If True, don't write changes to file

    Returns UpdateResult containing changes made.
    """
    content = values_path.read_text()
    result = UpdateResult()

    content = update_tag_in_content(
        content,
        AUTOMATION_TAG_PATTERN,
        format_deploy_image_tag(automation_sha),
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


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Update OpenHands and runtime-api charts based on a SaaS deploy."
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
    return parser.parse_args()


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
    dry_run: bool,
) -> str:
    """Update runtime-api chart and values. Returns the new chart version."""
    print_section_header("Updating runtime-api chart...")

    print("Updating runtime-api values.yaml...")
    values_result = update_runtime_api_values(
        RUNTIME_API_VALUES_PATH,
        deploy_config.runtime_api_sha,
        deploy_config.openhands_runtime_image_tag,
        dry_run=dry_run,
    )
    values_result.print_summary()

    print()
    print("Updating runtime-api Chart.yaml...")
    chart_version, chart_result = bump_chart_version(
        RUNTIME_API_CHART_PATH,
        "runtime-api",
        has_changes=values_result.has_changes,
        dry_run=dry_run,
    )
    chart_result.print_summary()

    return chart_version


def update_openhands_workflow(
    deploy_config: DeployConfig,
    openhands_version: str,
    runtime_api_version: str,
    automation_version: str,
    dry_run: bool,
) -> None:
    """Update openhands chart and values."""
    print_section_header("Updating openhands chart...")

    print("Updating openhands values.yaml...")
    values_result = update_openhands_values(
        VALUES_PATH,
        openhands_version,
        deploy_config.openhands_runtime_image_tag,
        dry_run=dry_run,
    )
    values_result.print_summary()

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
    new_version, chart_result = bump_chart_version(
        AUTOMATION_CHART_PATH,
        "automation",
        has_changes=values_result.has_changes,
        dry_run=dry_run,
    )
    chart_result.print_summary()
    return new_version


def process_updates(token: str, dry_run: bool = False, cloud_tag: str | None = None) -> None:
    print_section_header("Fetching latest versions...")

    openhands_version = resolve_openhands_version(token, cloud_tag)
    if not openhands_version:
        return

    current_app_version = get_current_app_version(CHART_PATH)
    if current_app_version:
        print(f"OpenHands-Cloud openhands chart appVersion: {current_app_version}")
        if current_app_version == openhands_version:
            print()
            print_section_header("Charts are already up to date - no changes needed")
            return

    version_number = extract_version_from_cloud_tag(openhands_version)
    if not version_number:
        print(f"Could not extract version from cloud tag: {openhands_version}")
        return

    print(f"Using deploy tag: {version_number}")

    deploy_config = get_deploy_config(token, DEPLOY_REPO, ref=version_number)
    if not deploy_config:
        print(f"Could not fetch deploy config from tag {version_number}")
        return

    print(f"Deploy config (from {version_number}):")
    print(f"  RUNTIME_API_SHA: {deploy_config.runtime_api_sha}")
    print(f"  AUTOMATION_SHA: {deploy_config.automation_sha}")
    print(f"  OPENHANDS_RUNTIME_IMAGE_TAG: {deploy_config.openhands_runtime_image_tag}")

    print()
    runtime_api_version = update_runtime_api_workflow(deploy_config, dry_run)

    print()
    automation_version = update_automation_workflow(deploy_config, dry_run)

    print()
    update_openhands_workflow(deploy_config, openhands_version, runtime_api_version, automation_version, dry_run)


def main(dry_run: bool = False, cloud_tag: str | None = None) -> None:
    if dry_run:
        print_section_header("DRY RUN MODE - No changes will be made")
        print()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Environment variable GITHUB_TOKEN is required. Try getting with: gh auth status --show-token")
        return

    process_updates(token, dry_run=dry_run, cloud_tag=cloud_tag)


if __name__ == "__main__":
    args = parse_args()
    main(dry_run=args.dry_run, cloud_tag=args.cloud_tag)
