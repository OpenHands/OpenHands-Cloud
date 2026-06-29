"""Shared pytest fixtures for update_openhands_charts tests.

This module provides reusable fixtures for creating temporary YAML files
used across multiple test classes, reducing duplication and improving
maintainability.

Module Organization
-------------------
1. **Fixture Baseline Constants**: Values matching the sample fixture content.
   Import these in tests to make assertions self-documenting (e.g.,
   `assert version == OPENHANDS_CHART_VERSION` instead of `== "0.1.0"`).

2. **Test Input Constants**: Values used as inputs when testing update
   operations (e.g., NEW_APP_VERSION for testing chart updates).

3. **Assertion Helpers**: Functions like `assert_file_contains()` and
   `get_chart_value()` that abstract YAML parsing, making tests more
   maintainable when file formats change.

4. **Temporary File Fixtures**: `make_temp_yaml_file` factory for creating
   test files with automatic cleanup.

5. **Sample Content Fixtures**: Pre-defined YAML content for Chart.yaml and
   values.yaml files in various configurations (minimal, full, with_deps).

6. **GitHub API Mock Fixtures**: `mock_github_tags`, `mock_github_ref`, etc.
   for fast, deterministic tests without network calls.

Usage Example
-------------
    def test_chart_update(make_temp_yaml_file, sample_openhands_chart_minimal):
        temp_file = make_temp_yaml_file(sample_openhands_chart_minimal)
        update_openhands_chart(temp_file, NEW_APP_VERSION)
        assert get_chart_value(temp_file, "appVersion") == NEW_APP_VERSION
"""

import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest
from ruamel.yaml import YAML

import update_openhands_charts

# =============================================================================
# Fixture baseline constants
# These values correspond to the sample fixtures below. Use these in tests
# to make assertions self-documenting instead of using magic strings.
# =============================================================================

# Shared openhands chart constants (same across all variants)
OPENHANDS_CHART_VERSION = "0.1.0"  # Chart version (semver)
OPENHANDS_CHART_APP_VERSION = "cloud-1.0.0"  # OpenHands uses cloud-X.Y.Z tags
# runtime-api and automation are embedded subcharts: their dependency entries
# carry no repository and a wildcard version (they version with openhands).
OPENHANDS_CHART_SUBCHART_DEP_VERSION = "*"

# Variant-specific openhands chart values (only in with_deps variant)
OPENHANDS_CHART_WITH_DEPS_OTHER_DEP_VERSION = "1.0.0"

# sample_image_loader_chart fixture values
IMAGE_LOADER_CHART_VERSION = "0.1.6"
IMAGE_LOADER_CHART_APP_VERSION = "1.0.0"

# =============================================================================
# Test input constants
# These values are used as inputs when testing update operations.
# Using named constants makes tests self-documenting.
# =============================================================================

# New versions used when testing chart updates
NEW_APP_VERSION = "cloud-2.0.0"  # OpenHands appVersion uses cloud-X.Y.Z tags
# Runtime image tag constants — agent-server uses X.Y.Z-python format (no cloud- prefix)
RUNTIME_IMAGE_TAG = "1.0.0-python"       # Baseline tag matching sample fixtures
NEW_RUNTIME_IMAGE_TAG = "1.1.0-python"   # New tag used when testing updates


def get_dependency_version(file_path: Path, dep_name: str) -> str | None:
    """Get the version of a dependency from a Chart.yaml file.

    Searches the 'dependencies' array for a matching name and returns its version.
    This abstracts YAML structure details, making tests more maintainable when
    the Chart.yaml format changes.

    Args:
        file_path: Path to the Chart.yaml file
        dep_name: Name of the dependency to find (e.g., "runtime-api")

    Returns:
        str: The version string (e.g., "*" for embedded subcharts) if dependency exists
        None: If dependency not found OR if chart has no dependencies section

    Example:
        >>> get_dependency_version(chart_path, "runtime-api")
        "*"
        >>> get_dependency_version(chart_path, "nonexistent")
        None
    """
    yaml = YAML()
    chart_data = yaml.load(file_path)
    for dep in chart_data.get("dependencies", []):
        if dep.get("name") == dep_name:
            return dep.get("version")
    return None


def get_chart_value(file_path: Path, key: str) -> Any:
    """Get a top-level value from a Chart.yaml file.

    Provides a simple interface for reading chart metadata without exposing
    YAML parsing details to tests. Only reads top-level keys; for nested
    values, use get_dependency_version or access the YAML directly.

    Args:
        file_path: Path to the Chart.yaml file
        key: The top-level key to retrieve (e.g., "version", "appVersion", "name")

    Returns:
        Any: The value at the key (str, list, dict, etc.) if key exists
        None: If key not found in the chart

    Example:
        >>> get_chart_value(chart_path, "appVersion")
        "cloud-1.0.0"
        >>> get_chart_value(chart_path, "dependencies")
        [{"name": "runtime-api", "version": "*"}]
    """
    yaml = YAML()
    chart_data = yaml.load(file_path)
    return chart_data.get(key)


def assert_file_contains(file_path: Path, expected: str) -> None:
    """Assert that a file contains a specific string.

    Single-pattern variant for concise assertions when checking one value.
    Reduces the read-assert boilerplate pattern to a single call.

    Args:
        file_path: Path to the file to check
        expected: String that must appear in the file

    Raises:
        AssertionError: If expected string is not found.
            Error message format: "Expected '<expected>' not found in file"
            This format shows the exact string being searched, making it easy
            to identify typos or unexpected whitespace in test expectations.

    Example:
        >>> assert_file_contains(values_path, "tag: cloud-1.1.0")
        # Passes silently if found
        # Raises: AssertionError("Expected 'tag: cloud-1.1.0' not found in file")
    """
    content = file_path.read_text()
    assert expected in content, f"Expected '{expected}' not found in file"


def assert_file_contains_all(file_path: Path, expected_strings: list[str]) -> None:
    """Assert that a file contains all expected strings.

    Verifies that YAML/config file modifications preserve content that should
    not be changed. Useful for ensuring update functions don't accidentally
    remove or corrupt unrelated configuration.

    Args:
        file_path: Path to the file to check
        expected_strings: List of strings that must appear in the file

    Raises:
        AssertionError: If ANY expected string is not found. Fails fast on
            first missing string with message format from assert_file_contains:
            "Expected '<missing_string>' not found in file"
            This allows quick identification of which specific string is missing.

    Example:
        >>> assert_file_contains_all(values_path, ["replicaCount: 1", "enabled: true"])
        # Passes silently if both strings found
        # If "enabled: true" missing: AssertionError("Expected 'enabled: true' not found in file")
    """
    for expected in expected_strings:
        assert_file_contains(file_path, expected)


def assert_version_bumped(file_path: Path, original_version: str) -> None:
    """Assert that a chart's version was bumped by exactly one patch increment.

    Encapsulates the pattern: read current version, verify it equals
    bump_patch_version(original). Catches both "forgot to bump" and
    "bumped too much" errors.

    Args:
        file_path: Path to the Chart.yaml file (must contain 'version' key)
        original_version: The semver version before update (e.g., "0.1.0")

    Raises:
        AssertionError: If current version != original + 1 patch, with message
                        showing expected vs actual (e.g., "Expected 0.1.1, got 0.1.0")

    Example:
        >>> # If chart was updated from 1.2.3 to 1.2.4
        >>> assert_version_bumped(chart_path, "1.2.3")  # passes
        >>> assert_version_bumped(chart_path, "1.2.2")  # fails: expected 1.2.3
    """
    from update_openhands_charts import bump_patch_version
    expected = bump_patch_version(original_version)
    actual = get_chart_value(file_path, "version")
    assert actual == expected, f"Expected version {expected}, got {actual}"


@pytest.fixture
def make_temp_yaml_file(tmp_path):
    """Factory fixture that creates temporary YAML files with cleanup.

    Returns a function that accepts YAML content and returns a Path to a
    temporary file. Cleanup is handled automatically by pytest's tmp_path fixture.

    Usage:
        def test_something(make_temp_yaml_file):
            yaml_content = '''
            apiVersion: v2
            name: test
            '''
            temp_file = make_temp_yaml_file(yaml_content)
            # Use temp_file...
    """
    counter = [0]

    def _make_temp_file(content: str) -> Path:
        counter[0] += 1
        path = tmp_path / f"test_{counter[0]}.yaml"
        path.write_text(content)
        return path

    return _make_temp_file


# =============================================================================
# Common Chart.yaml fixtures
# =============================================================================

@pytest.fixture
def sample_openhands_chart_with_deps():
    """Sample openhands Chart.yaml with embedded-subchart dependency entries.

    runtime-api and automation mirror the real chart: no repository and a
    wildcard version, because they are embedded subcharts living in the
    openhands chart's charts/ directory. The entries exist only for the
    condition flags.
    """
    return """\
apiVersion: v2
description: Test chart
name: test-chart
appVersion: cloud-1.0.0
version: 0.1.0
maintainers:
  - name: test
dependencies:
  - name: runtime-api
    version: "*"
    condition: runtime-api.enabled
  - name: automation
    version: "*"
    condition: automation.enabled
  - name: other-dep
    version: 1.0.0
"""


@pytest.fixture
def sample_openhands_chart_minimal():
    """Minimal openhands Chart.yaml for simple tests."""
    return """\
apiVersion: v2
appVersion: cloud-1.0.0
version: 0.1.0
name: openhands
dependencies:
  - name: runtime-api
    version: "*"
  - name: automation
    version: "*"
"""


@pytest.fixture(params=["with_deps", "minimal"])
def openhands_chart_variant(request, sample_openhands_chart_with_deps, sample_openhands_chart_minimal):
    """Parameterized fixture providing both openhands chart variants.

    Use this fixture when a test should verify behavior works across
    different chart structures (rich vs minimal).

    Returns a dict with:
        - content: The chart YAML content
        - variant: The variant name ("with_deps" or "minimal")

    Use shared constants directly for values:
        - OPENHANDS_CHART_VERSION
        - OPENHANDS_CHART_APP_VERSION
        - OPENHANDS_CHART_SUBCHART_DEP_VERSION
    """
    variant_name = request.param
    content = sample_openhands_chart_with_deps if variant_name == "with_deps" else sample_openhands_chart_minimal

    return {"content": content, "variant": variant_name}


@pytest.fixture
def sample_image_loader_chart():
    """Sample image-loader Chart.yaml (no dependencies, mirrors the real chart)."""
    return """\
apiVersion: v2
name: image-loader
description: A Helm chart for loading images on nodes using a DaemonSet with configurable runtime class
version: 0.1.6
appVersion: "1.0.0"
"""


# =============================================================================
# Common values.yaml fixtures
# =============================================================================

@pytest.fixture
def sample_openhands_values_full():
    """Sample openhands values.yaml.

    The agent-server image lives once in global.agentServerImage; runtime.image
    and the warmRuntimes configsByName entry omit it and fall back to the global,
    mirroring the real chart.
    """
    return """\
allowedUsers: null

image:
  repository: ghcr.io/openhands/enterprise-server
  tag: cloud-1.0.0

runtime:
  image:
    repository: ""
    tag: ""
  runAsRoot: true

runtime-api:
  enabled: true
  replicaCount: 1
  warmRuntimes:
    enabled: true
    count: 1
    configsByName:
      default:
        working_dir: "/openhands/code/"

global:
  agentServerImage:
    repository: ghcr.io/openhands/agent-server
    tag: 1.0.0-python
"""


@pytest.fixture
def sample_openhands_values_minimal():
    """Minimal openhands values.yaml for dry-run tests.

    Carries the two tags update_openhands_values touches: the enterprise-server
    image tag and the global agent-server image tag.
    """
    return """\
image:
  repository: ghcr.io/openhands/enterprise-server
  tag: cloud-1.0.0

global:
  agentServerImage:
    repository: ghcr.io/openhands/agent-server
    tag: 1.0.0-python
"""


@pytest.fixture
def sample_runtime_api_values():
    """Sample runtime-api values.yaml.

    The subchart carries its own global.agentServerImage default; the warmRuntimes
    configsByName entry omits its image and falls back to it.
    """
    return """\
nameOverride: ""
fullnameOverride: ""

replicaCount: 1

image:
  repository: ghcr.io/openhands/runtime-api
  tag: sha-0c907c9
  pullPolicy: Always

warmRuntimes:
  enabled: false
  configMapName: warm-runtimes-config
  count: 0
  configsByName:
    default:
      working_dir: "/openhands/code/"
      environment: {}

global:
  agentServerImage:
    repository: ghcr.io/openhands/agent-server
    tag: 1.0.0-python
"""


@pytest.fixture
def sample_automation_values():
    """Sample automation values.yaml."""
    return """\
image:
  repository: ghcr.io/openhands/automation
  tag: sha-c58faa1

imagePullSecrets: []

deployment:
  replicas: 1
  resources:
    requests:
      memory: 256Mi
      cpu: 100m
    limits:
      memory: 512Mi
      cpu: 500m
"""


@pytest.fixture
def sample_image_loader_values():
    """Sample image-loader values.yaml with the agent-server image pre-loaded on nodes."""
    return """\
image:
  repository: ghcr.io/openhands/agent-server
  tag: 1.0.0-python
  pullPolicy: Always

runtimeClass: sysbox-runc

nodeSelector:
  sysbox-install: "yes"
"""


@pytest.fixture
def sample_replicated_config():
    """Sample replicated config.yaml with the custom_sandbox_image_tag option.

    Mirrors the real replicated/config.yaml structure: the option carries the
    agent-server tag in two places (the help_text example and the default
    value), and sits between sibling options that also have defaults — those
    must never be touched by the updater.
    """
    return """\
apiVersion: kots.io/v1beta1
kind: Config
metadata:
  name: openhands-config
spec:
  groups:
    - name: sandbox
      title: Sandbox
      items:
        - name: custom_sandbox_image_enabled
          title: Use Custom Sandbox Image
          type: bool
          default: "0"
        - name: custom_sandbox_image_repository
          title: Sandbox Image Repository
          help_text: 'Full repository path with no tag, e.g. my-registry.example.com/openhands/agent-server'
          type: text
          when: 'repl{{ ConfigOptionEquals "custom_sandbox_image_enabled" "1" }}'
          required: true
        - name: custom_sandbox_image_tag
          title: Sandbox Image Tag
          help_text: Image tag, e.g. 1.0.0-python
          type: text
          default: "1.0.0-python"
          when: 'repl{{ ConfigOptionEquals "custom_sandbox_image_enabled" "1" }}'
          required: true
        - name: sandbox_warm_runtime_count
          title: Warm Runtime Count
          type: text
          default: "1"
"""


@pytest.fixture
def sample_replicated_openhands_wrapper_values():
    """Sample replicated openhands wrapper YAML with agent-server image references.

    The proxy block wraps its agent-server repository/tag/image refs in the
    custom_sandbox_image_enabled KOTS conditional, mirroring the real
    replicated/openhands.yaml: when the toggle is on an admin-supplied
    repository/tag takes over, otherwise the Replicated-proxied image is used.
    The proxy URL therefore no longer sits flush against the opening quote.

    A commented-out alternate repository line sits between repository: and tag:
    to exercise the pattern's tolerance of interleaved comments.
    """
    return """\
spec:
  values:
    runtime:
      image:
        # this is what we need to use for real deployments
        repository: '{{repl if ConfigOptionEquals "custom_sandbox_image_enabled" "1"}}{{repl ConfigOption "custom_sandbox_image_repository"}}{{repl else}}images.r9.all-hands.dev/proxy/{{repl LicenseFieldValue "appSlug"}}/ghcr.io/openhands/agent-server{{repl end}}'
        # repository: 'ghcr.io/openhands/agent-server'
        tag: '{{repl if ConfigOptionEquals "custom_sandbox_image_enabled" "1"}}{{repl ConfigOption "custom_sandbox_image_tag"}}{{repl else}}1.19.0-python{{repl end}}'
      warmRuntimes:
        configs:
          - name: default
            image: '{{repl if ConfigOptionEquals "custom_sandbox_image_enabled" "1"}}{{repl ConfigOption "custom_sandbox_image_repository"}}:{{repl ConfigOption "custom_sandbox_image_tag"}}{{repl else}}images.r9.all-hands.dev/proxy/{{repl LicenseFieldValue "appSlug"}}/ghcr.io/openhands/agent-server:1.19.0-python{{repl end}}'
    helmChart:
      values:
        runtime:
          image:
            repository: '{{repl LocalRegistryHost }}/{{repl LocalRegistryNamespace }}/agent-server'
            tag: '1.19.0-python'
          warmRuntimes:
            configs:
              - name: default
                image: '{{repl LocalRegistryHost }}/{{repl LocalRegistryNamespace }}/agent-server:1.19.0-python'
"""



# =============================================================================
# GitHub API mock fixtures
# =============================================================================

def _make_mock_tag(name: str) -> MagicMock:
    """Create a mock tag with the given name.

    MagicMock uses 'name' for its own purposes, so we must set it explicitly
    after creation.
    """
    tag = MagicMock()
    tag.name = name
    return tag


@pytest.fixture
def mock_github_tags(monkeypatch):
    """Factory fixture for mocking GitHub API with tags.

    Returns a function that sets up the GitHub mock and returns the mock objects
    for additional assertions.

    Usage:
        def test_something(mock_github_tags):
            mock_github, mock_repo = mock_github_tags(["cloud-1.0.0", "latest"])
            # ... test code ...
            mock_repo.get_tags.assert_called_once()
    """
    def _mock_github(tag_names: list[str] | None = None, repo_error: Exception | None = None):
        mock_github = MagicMock()

        if repo_error:
            mock_github.get_repo.side_effect = repo_error
        else:
            mock_tags = [_make_mock_tag(name) for name in (tag_names or [])]
            mock_repo = MagicMock()
            mock_repo.get_tags.return_value = mock_tags
            mock_github.get_repo.return_value = mock_repo

        monkeypatch.setattr("update_openhands_charts.Github", lambda auth: mock_github)

        # Return mock objects for assertions
        if repo_error:
            return mock_github, None
        return mock_github, mock_github.get_repo.return_value

    return _mock_github


@pytest.fixture
def mock_main_early_exit(monkeypatch):
    """Factory fixture for mocking main() dependencies for early exit scenarios.

    Sets up all mocks needed to run main() in a controlled way where it
    exits early (when current appVersion matches latest cloud tag).

    Returns a function that accepts a cloud_tag and an optional
    runtime_image_tag, and sets up all necessary mocks. Pass
    runtime_image_tag (default: None) for tests that exercise the
    --skip-version-check path past the early-exit guard.

    Usage:
        def test_something(mock_main_early_exit, capsys):
            mock_main_early_exit("cloud-1.20.0")
            main(dry_run=True)
            captured = capsys.readouterr()
            assert "cloud-1.20.0" in captured.out
    """
    def _mock_main(cloud_tag: str, runtime_image_tag: str | None = None):
        # Mock GITHUB_TOKEN environment variable
        monkeypatch.setenv("GITHUB_TOKEN", "dummy-token")

        # Mock get_latest_cloud_tag to return the specified cloud tag
        monkeypatch.setattr(
            "update_openhands_charts.get_latest_cloud_tag",
            lambda token, repo: cloud_tag
        )
        # Mock cloud_tag_exists to return True
        monkeypatch.setattr(
            "update_openhands_charts.cloud_tag_exists",
            lambda token, repo, tag: True
        )
        # Mock get_current_app_version to return matching version (triggers early exit)
        monkeypatch.setattr(
            "update_openhands_charts.get_current_app_version",
            lambda path: cloud_tag
        )
        # Mock sandbox spec fetch — required when callers skip the early-exit guard
        monkeypatch.setattr(
            "update_openhands_charts.get_runtime_image_tag_from_sandbox_spec",
            lambda token, repo, ref: runtime_image_tag,
        )

    return _mock_main


@pytest.fixture
def stub_cloud_tag_exists(monkeypatch):
    """Factory fixture that stubs `update_openhands_charts.cloud_tag_exists` to return a fixed bool."""
    def _stub(exists: bool):
        monkeypatch.setattr(
            "update_openhands_charts.cloud_tag_exists",
            lambda token, repo, tag: exists,
        )
    return _stub


@pytest.fixture
def stub_latest_cloud_tag(monkeypatch):
    """Factory fixture that stubs `update_openhands_charts.get_latest_cloud_tag` to return a fixed value."""
    def _stub(tag: str | None):
        monkeypatch.setattr(
            "update_openhands_charts.get_latest_cloud_tag",
            lambda token, repo: tag,
        )
    return _stub


@pytest.fixture
def stub_process_updates_chain(monkeypatch):
    """Factory fixture for stubbing the call chain inside process_updates().

    Defaults give a fully-successful chain up to the deploy-config fetch.
    Pass None to any kwarg to simulate that step failing — this triggers the
    corresponding early-return guard so tests can verify downstream calls
    are skipped.

    Usage:
        def test_runtime_tag_guard(stub_process_updates_chain):
            stub_process_updates_chain(runtime_image_tag=None)
            process_updates("token")
            # ... assert downstream call was NOT made
    """
    def _stub(
        openhands_version: str | None = "cloud-1.20.0",
        current_app_version: str | None = "cloud-1.19.0",
        runtime_image_tag: str | None = "1.20.0-python",
    ):
        monkeypatch.setattr(
            "update_openhands_charts.resolve_openhands_version",
            lambda token, cloud_tag: openhands_version,
        )
        monkeypatch.setattr(
            "update_openhands_charts.get_current_app_version",
            lambda path: current_app_version,
        )
        monkeypatch.setattr(
            "update_openhands_charts.get_runtime_image_tag_from_sandbox_spec",
            lambda token, repo, ref: runtime_image_tag,
        )
    return _stub


@pytest.fixture
def make_workflow_response():
    """Factory fixture for creating mock GitHub API responses with workflow content.

    Returns a function that creates a mock response object with base64-encoded
    YAML content. Use this to test get_deploy_config with various workflow
    configurations without repeating the mock setup boilerplate.

    Usage:
        def test_something(make_workflow_response, monkeypatch):
            response = make_workflow_response("env:\\n  RUNTIME_API_SHA: abc123")
            monkeypatch.setattr("update_openhands_charts.requests.get",
                               MagicMock(return_value=response))
            # ... test code ...
    """
    def _make_response(yaml_content: str) -> MagicMock:
        encoded = base64.b64encode(yaml_content.encode()).decode()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"content": encoded}
        return mock_response

    return _make_response


# =============================================================================
# Mock response helpers for get_deploy_config error path tests
# These plain functions (not fixtures) are used inside @pytest.mark.parametrize
# decorators, which are evaluated at class scope where fixtures cannot be
# injected. They centralize mock-response construction for error scenarios.
# =============================================================================

def make_http_error_response(status_code: int, message: str) -> Mock:
    """Create a mock requests.get that raises an exception on raise_for_status()."""
    mock_response = Mock()
    mock_response.status_code = status_code
    mock_response.raise_for_status.side_effect = Exception(f"HTTP {status_code}: {message}")
    return Mock(return_value=mock_response)


def make_json_error_response() -> Mock:
    """Create a mock requests.get whose .json() call raises an exception."""
    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.side_effect = Exception("Invalid JSON")
    return Mock(return_value=mock_response)


def make_missing_key_response(json_data: dict) -> Mock:
    """Create a mock requests.get returning JSON with the given (possibly incomplete) data."""
    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = json_data
    return Mock(return_value=mock_response)


def make_invalid_base64_response(invalid_content: str) -> Mock:
    """Create a mock requests.get returning JSON with malformed base64 content."""
    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = {"content": invalid_content}
    return Mock(return_value=mock_response)


def make_invalid_yaml_response(invalid_yaml: str) -> Mock:
    """Create a mock requests.get returning valid base64 but invalid YAML content."""
    encoded = base64.b64encode(invalid_yaml.encode()).decode()
    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = {"content": encoded}
    return Mock(return_value=mock_response)


@pytest.fixture
def mock_github_ref(monkeypatch):
    """Factory fixture for mocking GitHub API git ref lookups.

    Returns a function that sets up the GitHub mock for tag existence checks.

    Usage:
        def test_tag_exists(mock_github_ref):
            mock_github, mock_repo = mock_github_ref(tag_exists=True)
            # ... test code ...
            mock_repo.get_git_ref.assert_called_once_with("tags/cloud-1.0.0")
    """
    def _mock_github(
        tag_exists: bool = True,
        repo_error: Exception | None = None,
        ref_error: Exception | None = None,
    ):
        mock_github = MagicMock()

        if repo_error:
            mock_github.get_repo.side_effect = repo_error
        else:
            mock_repo = MagicMock()
            if ref_error or not tag_exists:
                mock_repo.get_git_ref.side_effect = ref_error or Exception("Not found")
            else:
                mock_repo.get_git_ref.return_value = MagicMock()
            mock_github.get_repo.return_value = mock_repo

        monkeypatch.setattr("update_openhands_charts.Github", lambda auth: mock_github)

        if repo_error:
            return mock_github, None
        return mock_github, mock_github.get_repo.return_value

    return _mock_github


@pytest.fixture
def openhands_workflow_mocks(monkeypatch):
    """Patch the four inner functions called by update_openhands_workflow.

    Returns a namespace exposing each MagicMock by name (`.values`,
    `.replicated`, `.replicated_config`, `.chart`) so each workflow-contract
    test asserts on the calls it focuses on without depending on a positional
    return shape. The values mock reports has_changes=True so the chart-bump
    path is exercised; the others return a no-change UpdateResult and exist to
    prevent writes to the real files.
    """
    mocks = SimpleNamespace(
        values=MagicMock(return_value=update_openhands_charts.UpdateResult(has_changes=True)),
        replicated=MagicMock(return_value=update_openhands_charts.UpdateResult()),
        replicated_config=MagicMock(return_value=update_openhands_charts.UpdateResult()),
        chart=MagicMock(return_value=update_openhands_charts.UpdateResult()),
    )
    monkeypatch.setattr("update_openhands_charts.update_openhands_values", mocks.values)
    monkeypatch.setattr("update_openhands_charts.update_replicated_openhands_values", mocks.replicated)
    monkeypatch.setattr("update_openhands_charts.update_replicated_config", mocks.replicated_config)
    monkeypatch.setattr("update_openhands_charts.update_openhands_chart", mocks.chart)
    return mocks
