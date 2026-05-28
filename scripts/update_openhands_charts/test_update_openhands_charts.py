#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyGithub", "ruamel.yaml", "requests", "pytest"]
# ///
"""Unit tests for update_openhands_charts.py."""

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

# Add the script's directory to sys.path so we can import it directly
sys.path.insert(0, str(Path(__file__).parent))

import update_openhands_charts
from conftest import (
    assert_file_contains,
    assert_file_contains_all,
    assert_version_bumped,
    get_chart_value,
    get_dependency_version,
    # Mock response helpers for get_deploy_config error path tests
    make_http_error_response,
    make_invalid_base64_response,
    make_invalid_yaml_response,
    make_json_error_response,
    make_missing_key_response,
    # Fixture baseline constants for self-documenting assertions
    OPENHANDS_CHART_VERSION,
    OPENHANDS_CHART_APP_VERSION,
    OPENHANDS_CHART_RUNTIME_API_VERSION,
    OPENHANDS_CHART_WITH_DEPS_OTHER_DEP_VERSION,
    RUNTIME_API_CHART_FULL_VERSION,
    RUNTIME_API_CHART_FULL_APP_VERSION,
    RUNTIME_API_CHART_MINIMAL_VERSION,
    # Test input constants for update operations
    NEW_APP_VERSION,
    NEW_RUNTIME_API_VERSION,
    NEW_RUNTIME_IMAGE_TAG,
    RUNTIME_IMAGE_TAG,
)
from update_openhands_charts import (
    DeployConfig,
    bump_patch_version,
    cloud_tag_exists,
    extract_version_from_cloud_tag,
    format_sha_tag,
    get_current_app_version,
    get_deploy_config,
    get_latest_cloud_tag,
    get_runtime_image_tag_from_sandbox_spec,
    get_short_sha,
    main,
    process_updates,
    resolve_openhands_version,
    update_openhands_chart,
    update_openhands_values,
    update_openhands_workflow,
    update_replicated_openhands_values,
    update_runtime_api_chart,
    update_runtime_api_values,
    update_runtime_api_workflow,
)


# =============================================================================
# PURE FUNCTION TESTS
# Tests for stateless utility functions with no external dependencies.
# These tests are fast, deterministic, and test behavior through public APIs.
# =============================================================================


class TestExtractVersionFromCloudTag:
    """Tests for extract_version_from_cloud_tag function.

    OpenHands uses 'cloud-X.Y.Z' tags to identify production releases.
    These tests verify cloud tag parsing through the public interface rather
    than testing internal regex patterns directly. This approach is more
    maintainable as it tests behavior, not implementation.

    TDD Rationale: Tests were designed to drive a simple regex pattern that
    accepts only the strict 'cloud-X.Y.Z' format. Edge cases ensure the
    implementation rejects common variations (v-prefix, pre-release suffixes)
    that could cause version comparison bugs in production.
    """

    @pytest.mark.parametrize("cloud_tag,expected", [
        # Happy path: typical production versions
        ("cloud-1.1.0", "1.1.0"),
        ("cloud-2.0.0", "2.0.0"),
        # Boundary: minimum valid version (all zeros) - ensures 0.0.0 is valid
        ("cloud-0.0.0", "0.0.0"),
        # Boundary: multi-digit components - regex must use \d+ not \d
        ("cloud-10.20.30", "10.20.30"),
        # Stress test: very large versions - ensures no arbitrary numeric limits
        ("cloud-123.456.789", "123.456.789"),
    ])
    def test_extracts_version_from_valid_cloud_tags(self, cloud_tag, expected):
        """Verify semver is correctly extracted from 'cloud-X.Y.Z' format tags."""
        assert extract_version_from_cloud_tag(cloud_tag) == expected

    @pytest.mark.parametrize("invalid_tag", [
        # Prefix validation: must be exactly "cloud-" (case-sensitive, with hyphen)
        pytest.param("1.1.0", id="missing cloud- prefix"),
        pytest.param("v1.1.0", id="wrong prefix (v instead of cloud-)"),
        pytest.param("Cloud-1.2.3", id="wrong case"),
        pytest.param("cloud1.2.3", id="missing hyphen"),
        # Semver structure: must be exactly X.Y.Z (three numeric parts)
        pytest.param("cloud-1.2", id="missing patch"),
        pytest.param("cloud-1.2.3.4", id="extra part"),
        # Semver extensions: pre-release/build metadata breaks version comparison
        pytest.param("cloud-1.2.3-beta", id="pre-release suffix"),
        pytest.param("cloud-1.2.3+build", id="build metadata suffix"),
        # Edge cases: defensive handling of malformed input
        pytest.param("", id="empty string"),
        pytest.param("latest", id="non-version tag"),
        pytest.param("cloud-", id="missing version"),
    ])
    def test_returns_none_for_invalid_cloud_tag_formats(self, invalid_tag):
        """Verify invalid formats return None rather than raising exceptions.

        TDD Rationale: Returning None (instead of raising) allows callers to
        safely filter cloud tags from mixed tag lists without try/except blocks.
        """
        assert extract_version_from_cloud_tag(invalid_tag) is None


class TestGetShortSha:
    """Tests for get_short_sha function.

    Git short SHAs are conventionally 7 characters for readability while
    maintaining uniqueness in most repositories.

    TDD Rationale: Tests drive a simple slice operation. Boundary cases
    (exactly 7 chars, shorter than 7) ensure the implementation handles
    edge cases gracefully without raising IndexError.
    """

    @pytest.mark.parametrize("sha,expected", [
        # Happy path: typical input longer than 7 chars
        ("abcdefghijklmnop", "abcdefg"),
        # Real-world: full 40-character git SHA (most common input)
        ("6ccd42bb2975866f1abc21e635c01d2afbdd1acf", "6ccd42b"),
        # Boundary: input exactly 7 chars (no truncation needed)
        ("a1b2c3d", "a1b2c3d"),
        # Boundary: input shorter than 7 chars (returns full input)
        pytest.param("abc", "abc", id="input shorter than 7 chars"),
    ])
    def test_short_sha_is_first_seven_characters_of_full_sha(self, sha, expected):
        """Verify short SHA extraction returns exactly 7 characters or full input if shorter."""
        assert get_short_sha(sha) == expected


class TestFormatShaTag:
    """Tests for format_sha_tag function.

    Container registries use 'sha-<hash>' tags to identify images built from
    specific commits. Note: Truncation behavior is tested in TestGetShortSha.
    These tests focus on the sha- prefix formatting.
    """

    @pytest.mark.parametrize("sha,expected", [
        # Happy path: verifies "sha-" prefix is prepended
        ("abcdefghijklmnop", "sha-abcdefg"),
        # Real-world: actual GitHub Actions workflow SHA (ensures production compatibility)
        ("743f6256a690efc388af6e960ad8009f5952e721", "sha-743f625"),
    ])
    def test_sha_tag_format_is_sha_prefix_followed_by_short_sha(self, sha, expected):
        """Verify SHA tag format follows the 'sha-<7-char-hash>' convention used in container registries."""
        assert format_sha_tag(sha) == expected


class TestGetCurrentAppVersion:
    """Tests for get_current_app_version function.

    Reads the appVersion field from Helm Chart.yaml files to determine
    the currently deployed OpenHands version.
    """

    def test_reads_app_version_from_chart_yaml(self, make_temp_yaml_file):
        """Verify appVersion is correctly extracted from a valid Chart.yaml file."""
        chart_content = """\
apiVersion: v2
appVersion: cloud-1.1.0
version: 0.3.11
name: openhands
"""
        temp_chart_file = make_temp_yaml_file(chart_content)
        result = get_current_app_version(temp_chart_file)
        assert result == "cloud-1.1.0"

    def test_missing_chart_file_returns_none(self):
        """Verify graceful handling when Chart.yaml does not exist."""
        result = get_current_app_version(Path("/nonexistent/Chart.yaml"))
        assert result is None


class TestBumpPatchVersion:
    """Tests for bump_patch_version function.

    Semantic versioning (semver) uses MAJOR.MINOR.PATCH format where
    patch bumps indicate backwards-compatible bug fixes.

    TDD Rationale: Tests drive a split-increment-join implementation.
    Invalid format tests ensure ValueError is raised early with clear
    messages, preventing silent corruption of chart versions.
    """

    @pytest.mark.parametrize("version,expected", [
        # Happy path: typical version increment
        ("1.2.3", "1.2.4"),
        # Boundary: patch starts at zero (common for new minor releases)
        ("1.0.0", "1.0.1"),
        # Boundary: 99→100 rollover - implementation must use int() not string ops
        ("1.2.99", "1.2.100"),
        # Verification: major/minor preserved during patch bump
        ("5.10.15", "5.10.16"),
    ])
    def test_patch_version_increments_by_one_preserving_major_minor(self, version, expected):
        """Verify patch bump increments only the patch component while preserving major.minor."""
        assert bump_patch_version(version) == expected

    @pytest.mark.parametrize("invalid_version", [
        # Structure: must have exactly 3 parts - fail fast on malformed input
        pytest.param("1.2", id="missing patch"),
        pytest.param("1.2.3.4", id="too many parts"),
        # Format: no prefixes allowed - caller must strip prefix first
        pytest.param("v1.2.3", id="has prefix"),
        # Edge cases: defensive handling prevents int() conversion errors
        pytest.param("", id="empty string"),
        pytest.param("1.2.abc", id="non-numeric patch"),
        pytest.param("a.b.c", id="all non-numeric"),
    ])
    def test_invalid_semver_format_raises_value_error(self, invalid_version):
        """Verify non-semver strings are rejected with clear error message."""
        with pytest.raises(ValueError, match="Invalid semver format"):
            bump_patch_version(invalid_version)


# =============================================================================
# CHART AND VALUES UPDATE TESTS
# Tests for functions that modify Chart.yaml and values.yaml files.
# These use temporary file fixtures and verify file content changes.
# =============================================================================


class TestUpdateChartAcrossVariants:
    """Tests for update_chart that verify behavior across both chart variants.

    Uses the parameterized openhands_chart_variant fixture to ensure core
    functionality works with both rich (with_deps) and minimal chart structures.

    Test Structure:
    - test_chart_app_version_updates: Core update behavior
    - test_chart_version_bumps: Version increment on change
    - test_runtime_api_dependency: Dependency update
    - test_version_unchanged_when_already_current: Consolidated idempotency checks

    TDD Rationale: Tests drive the update_openhands_chart function to handle
    both minimal and full Chart.yaml structures. Parameterized variants ensure
    the implementation doesn't accidentally depend on optional fields (like
    maintainers or extra dependencies) that may not exist in all chart files.
    """

    @pytest.fixture
    def temp_chart_file(self, make_temp_yaml_file, openhands_chart_variant):
        """Create a temporary Chart.yaml from the parameterized variant."""
        return make_temp_yaml_file(openhands_chart_variant["content"])

    def test_chart_app_version_updates_to_new_cloud_tag(self, temp_chart_file):
        """Verify appVersion field is updated to the new OpenHands cloud tag."""
        update_openhands_chart(temp_chart_file, NEW_APP_VERSION, None)

        assert get_chart_value(temp_chart_file, "appVersion") == NEW_APP_VERSION

    def test_chart_version_bumps_patch_on_update(self, temp_chart_file):
        """Verify chart version patch is incremented when changes are made."""
        update_openhands_chart(temp_chart_file, NEW_APP_VERSION, None)

        assert_version_bumped(temp_chart_file, OPENHANDS_CHART_VERSION)

    def test_runtime_api_dependency_version_updates(self, temp_chart_file):
        """Verify runtime-api dependency version is updated in Chart.yaml."""
        update_openhands_chart(temp_chart_file, NEW_APP_VERSION, NEW_RUNTIME_API_VERSION)

        assert get_dependency_version(temp_chart_file, "runtime-api") == NEW_RUNTIME_API_VERSION

    @pytest.mark.parametrize("app_version,runtime_api_version,unchanged_key", [
        # When appVersion already matches target, it should be reported as unchanged
        pytest.param(
            OPENHANDS_CHART_APP_VERSION, NEW_RUNTIME_API_VERSION, "appVersion",
            id="appVersion unchanged when already current"
        ),
        # When runtime-api version already matches target, it should be reported as unchanged
        pytest.param(
            NEW_APP_VERSION, OPENHANDS_CHART_RUNTIME_API_VERSION, "runtime-api version",
            id="runtime-api version unchanged when already current"
        ),
    ])
    def test_version_unchanged_when_already_current(
        self, temp_chart_file, app_version, runtime_api_version, unchanged_key
    ):
        """Verify no change is recorded when a version already matches target.

        Idempotency verification: Ensures the update function correctly identifies
        when values are already at their target state, preventing spurious version
        bumps and unnecessary commits in CI/CD pipelines.
        """
        result = update_openhands_chart(temp_chart_file, app_version, runtime_api_version)

        assert result.is_unchanged(unchanged_key)


class TestUpdateChart:
    """Tests for update_chart function with specific fixture requirements.

    These tests require the with_deps fixture specifically because they test
    features only present in that variant (e.g., multiple dependencies, maintainers).

    TDD Rationale: Tests drive selective dependency updates - only runtime-api
    should be modified while other dependencies remain untouched. This prevents
    accidental side effects when updating charts with multiple dependencies.
    """

    @pytest.fixture
    def temp_chart_file(self, make_temp_yaml_file, sample_openhands_chart_with_deps):
        """Create a temporary Chart.yaml file using shared fixtures."""
        return make_temp_yaml_file(sample_openhands_chart_with_deps)

    def test_non_runtime_api_dependencies_remain_unchanged(self, temp_chart_file):
        """Verify only runtime-api dependency is modified; other deps are preserved."""
        update_openhands_chart(temp_chart_file, NEW_APP_VERSION, NEW_RUNTIME_API_VERSION)

        assert get_dependency_version(temp_chart_file, "other-dep") == OPENHANDS_CHART_WITH_DEPS_OTHER_DEP_VERSION

    @pytest.mark.parametrize("key,expected", [
        ("apiVersion", "v2"),
        ("description", "Test chart"),
        ("name", "test-chart"),
    ])
    def test_scalar_field_preserved_after_update(self, temp_chart_file, key, expected):
        """Non-targeted scalar fields are not modified by chart update."""
        update_openhands_chart(temp_chart_file, NEW_APP_VERSION, NEW_RUNTIME_API_VERSION)

        assert get_chart_value(temp_chart_file, key) == expected

    @pytest.mark.parametrize("list_key", ["maintainers", "dependencies"])
    def test_list_length_preserved_after_update(self, temp_chart_file, list_key):
        """Lists (maintainers, dependencies) keep their original length — no entries added/removed."""
        original_count = len(get_chart_value(temp_chart_file, list_key))

        update_openhands_chart(temp_chart_file, NEW_APP_VERSION, NEW_RUNTIME_API_VERSION)

        assert len(get_chart_value(temp_chart_file, list_key)) == original_count



# =============================================================================
# TEST HELPER FUNCTION TESTS
# Tests for helper functions defined in conftest.py that are used by other tests.
# These verify the test infrastructure itself works correctly.
# =============================================================================


class TestUpdateResultHelpers:
    """Tests for UpdateResult helper methods.

    These helpers provide a cleaner API for checking if specific keys
    were changed or unchanged, reducing coupling to internal data structures.
    """

    @pytest.mark.parametrize("key,expected", [
        # Happy path: first key in list should be found
        ("appVersion", True),
        # Happy path: key with special chars (hyphen, space) should be found
        ("runtime-api version", True),
        # Boundary: key not in list returns False (not None or error)
        ("nonexistent-key", False),
    ])
    def test_is_unchanged_finds_keys_in_unchanged_list(self, key, expected):
        """Verify is_unchanged correctly identifies presence/absence of keys."""
        result = update_openhands_charts.UpdateResult(
            unchanged=[("appVersion", "1.0.0"), ("runtime-api version", "0.2.6")]
        )
        assert result.is_unchanged(key) is expected

    @pytest.mark.parametrize("key,expected", [
        # Happy path: first key in changes list should be found
        ("appVersion", True),
        # Happy path: additional keys in list should also be found
        ("version", True),
        # Boundary: key not in list returns False (not None or error)
        ("nonexistent-key", False),
    ])
    def test_has_change_for_finds_keys_in_changes_list(self, key, expected):
        """Verify has_change_for correctly identifies presence/absence of keys."""
        result = update_openhands_charts.UpdateResult(
            has_changes=True,
            changes=[("appVersion", "1.0.0", "2.0.0"), ("version", "0.1.0", "0.1.1")]
        )
        assert result.has_change_for(key) is expected

    @pytest.mark.parametrize("substring,expected", [
        # Happy path: exact substring match
        ("enterprise-server", True),
        # Happy path: partial match within longer message
        ("image tag", True),
        # Boundary: substring not in any error returns False
        ("nonexistent-error", False),
    ])
    def test_has_error_containing_finds_substrings_in_errors(self, substring, expected):
        """Verify has_error_containing correctly identifies substrings in error messages."""
        result = update_openhands_charts.UpdateResult(
            errors=[
                "Could not find enterprise-server image tag",
                "Could not find runtime image tag",
            ]
        )
        assert result.has_error_containing(substring) is expected

    @pytest.mark.parametrize("method_name,query", [
        # Edge case: empty UpdateResult returns False for all lookup methods
        pytest.param("is_unchanged", "any-key", id="is_unchanged on empty"),
        pytest.param("has_change_for", "any-key", id="has_change_for on empty"),
        pytest.param("has_error_containing", "any-error", id="has_error_containing on empty"),
    ])
    def test_lookup_methods_return_false_for_empty_result(self, method_name, query):
        """Verify all lookup methods return False when their list is empty.

        Consolidates edge case tests for is_unchanged, has_change_for, and
        has_error_containing to verify consistent behavior on empty UpdateResult.
        """
        result = update_openhands_charts.UpdateResult()
        method = getattr(result, method_name)
        assert method(query) is False

    @pytest.mark.parametrize("field,count_property,data,expected_count", [
        # error_count: multiple, single, empty
        pytest.param("errors", "error_count", ["error1", "error2", "error3"], 3, id="error_count: multiple"),
        pytest.param("errors", "error_count", ["only one error"], 1, id="error_count: single"),
        pytest.param("errors", "error_count", [], 0, id="error_count: empty"),
        # change_count: multiple, single, empty
        pytest.param("changes", "change_count", [("k1", "old1", "new1"), ("k2", "old2", "new2")], 2, id="change_count: multiple"),
        pytest.param("changes", "change_count", [("key", "old", "new")], 1, id="change_count: single"),
        pytest.param("changes", "change_count", [], 0, id="change_count: empty"),
        # unchanged_count: multiple, single, empty
        pytest.param("unchanged", "unchanged_count", [("k1", "v1"), ("k2", "v2"), ("k3", "v3")], 3, id="unchanged_count: multiple"),
        pytest.param("unchanged", "unchanged_count", [("key", "value")], 1, id="unchanged_count: single"),
        pytest.param("unchanged", "unchanged_count", [], 0, id="unchanged_count: empty"),
    ])
    def test_count_properties_return_correct_counts(self, field, count_property, data, expected_count):
        """Verify count properties (error_count, change_count, unchanged_count) return correct values.

        Consolidates count property tests into a single parameterized test that
        covers all three properties with their boundary conditions (multiple,
        single, empty).
        """
        result = update_openhands_charts.UpdateResult(**{field: data})
        assert getattr(result, count_property) == expected_count


# =============================================================================
# GITHUB API INTEGRATION TESTS
# Tests for functions that interact with GitHub API (mocked).
# These verify correct API usage, error handling, and response parsing.
# =============================================================================


class TestGetRuntimeImageTagFromSandboxSpec:
    """Tests for get_runtime_image_tag_from_sandbox_spec function.

    Fetches sandbox_spec_service.py from the OpenHands repo at a specific cloud
    tag and extracts the AGENT_SERVER_IMAGE tag.
    """

    VALID_SANDBOX_SPEC_CONTENT = """\
AGENT_SERVER_IMAGE = 'ghcr.io/openhands/agent-server:1.19.1-python'

def get_agent_server_image():
    return AGENT_SERVER_IMAGE
"""

    def test_returns_image_tag_from_sandbox_spec(self, monkeypatch, make_workflow_response):
        """Test that a valid sandbox spec returns the agent-server image tag."""
        response = make_workflow_response(self.VALID_SANDBOX_SPEC_CONTENT)
        monkeypatch.setattr(
            "update_openhands_charts.requests.get",
            Mock(return_value=response)
        )

        result = get_runtime_image_tag_from_sandbox_spec("token", "owner/repo", ref="cloud-1.26.1")

        assert result == "1.19.1-python"

    def test_constructs_correct_url_with_ref(self, monkeypatch, make_workflow_response):
        """Test URL includes sandbox_spec_service.py path and ref parameter."""
        mock_get = Mock(return_value=make_workflow_response(self.VALID_SANDBOX_SPEC_CONTENT))
        monkeypatch.setattr("update_openhands_charts.requests.get", mock_get)

        get_runtime_image_tag_from_sandbox_spec("token", "owner/repo", ref="cloud-1.26.1")

        called_url = mock_get.call_args[0][0]
        assert "openhands/app_server/sandbox/sandbox_spec_service.py" in called_url
        assert "?ref=cloud-1.26.1" in called_url

    def test_includes_authorization_header(self, monkeypatch, make_workflow_response):
        """Test that the Authorization header carries the bearer token."""
        mock_get = Mock(return_value=make_workflow_response(self.VALID_SANDBOX_SPEC_CONTENT))
        monkeypatch.setattr("update_openhands_charts.requests.get", mock_get)

        get_runtime_image_tag_from_sandbox_spec("my-secret-token", "owner/repo", ref="cloud-1.26.1")

        called_headers = mock_get.call_args[1]["headers"]
        assert called_headers["Authorization"] == "Bearer my-secret-token"

    def test_returns_none_and_prints_error_on_http_failure(self, monkeypatch, capsys):
        """Test graceful handling when the GitHub API request fails."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = Exception("HTTP 404: Not Found")
        monkeypatch.setattr(
            "update_openhands_charts.requests.get",
            Mock(return_value=mock_response)
        )

        result = get_runtime_image_tag_from_sandbox_spec("token", "owner/repo", ref="cloud-1.26.1")

        assert result is None
        assert "Error fetching sandbox spec" in capsys.readouterr().out

    def test_returns_none_and_prints_error_when_image_constant_missing(
        self, monkeypatch, make_workflow_response, capsys
    ):
        """Test graceful handling when AGENT_SERVER_IMAGE constant is absent."""
        response = make_workflow_response("# No image constant here\n")
        monkeypatch.setattr(
            "update_openhands_charts.requests.get",
            Mock(return_value=response)
        )

        result = get_runtime_image_tag_from_sandbox_spec("token", "owner/repo", ref="cloud-1.26.1")

        assert result is None
        out = capsys.readouterr().out
        assert "Error fetching sandbox spec" in out
        assert "AGENT_SERVER_IMAGE" in out


class TestGetDeployConfig:
    """Tests for get_deploy_config function.

    Uses parameterized tests for comprehensive error path coverage.
    All error scenarios should return None and print an error message.
    """

    # Valid workflow YAML for success case tests
    VALID_WORKFLOW_YAML = """\
env:
  RUNTIME_API_SHA: abc123def456
  OTHER_VAR: value
"""

    def test_returns_deploy_config_instance_on_success(self, monkeypatch, make_workflow_response):
        """Test that a valid response yields a non-None DeployConfig instance.

        Type-level contract: callers rely on the returned object being a
        DeployConfig (so they can access typed fields like runtime_api_sha).
        Kept separate from the value-extraction test below so a failure here
        unambiguously signals "wrong return type or None" rather than a parsing
        regression.
        """
        monkeypatch.setattr(
            "update_openhands_charts.requests.get",
            Mock(return_value=make_workflow_response(self.VALID_WORKFLOW_YAML))
        )

        result = get_deploy_config("fake-token", "owner/repo", ref="1.0.0")

        assert isinstance(result, DeployConfig)

    def test_runtime_api_sha_parsed_from_workflow_env(self, monkeypatch, make_workflow_response):
        """Test that runtime_api_sha is correctly extracted from the workflow env section.

        Value-extraction contract: the RUNTIME_API_SHA key in the workflow's
        env section must surface as DeployConfig.runtime_api_sha. A failure
        here unambiguously signals a regression in the env-parsing logic.
        """
        monkeypatch.setattr(
            "update_openhands_charts.requests.get",
            Mock(return_value=make_workflow_response(self.VALID_WORKFLOW_YAML))
        )

        result = get_deploy_config("fake-token", "owner/repo", ref="1.0.0")

        assert result.runtime_api_sha == "abc123def456"

    def test_constructs_correct_url_without_ref(self, monkeypatch, make_workflow_response):
        """Test that URL is constructed correctly without ref parameter."""
        mock_get = Mock(return_value=make_workflow_response(self.VALID_WORKFLOW_YAML))
        monkeypatch.setattr("update_openhands_charts.requests.get", mock_get)

        get_deploy_config("fake-token", "owner/repo")

        called_url = mock_get.call_args[0][0]
        assert called_url == "https://api.github.com/repos/owner/repo/contents/.github/workflows/deploy.yaml"

    def test_constructs_correct_url_with_ref(self, monkeypatch, make_workflow_response):
        """Test that URL includes ref parameter when provided."""
        mock_get = Mock(return_value=make_workflow_response(self.VALID_WORKFLOW_YAML))
        monkeypatch.setattr("update_openhands_charts.requests.get", mock_get)

        get_deploy_config("fake-token", "owner/repo", ref="v1.2.3")

        called_url = mock_get.call_args[0][0]
        assert "?ref=v1.2.3" in called_url

    def test_includes_authorization_header(self, monkeypatch, make_workflow_response):
        """Test that Authorization header is included with token."""
        mock_get = Mock(return_value=make_workflow_response(self.VALID_WORKFLOW_YAML))
        monkeypatch.setattr("update_openhands_charts.requests.get", mock_get)

        get_deploy_config("my-secret-token", "owner/repo")

        called_headers = mock_get.call_args[1]["headers"]
        assert called_headers["Authorization"] == "Bearer my-secret-token"

    def test_returns_empty_string_when_env_key_missing(self, monkeypatch, make_workflow_response):
        """Test that missing env keys return empty string (not None).

        Edge case: Workflow has env section but lacks expected keys.
        This tests graceful handling via dict.get() default behavior.
        """
        # Workflow without expected keys - simulates incomplete workflow config
        response = make_workflow_response("env:\n  OTHER_VAR: value\n")
        monkeypatch.setattr(
            "update_openhands_charts.requests.get",
            Mock(return_value=response)
        )

        result = get_deploy_config("token", "owner/repo")

        assert result is not None
        assert result.runtime_api_sha == ""

    def test_returns_empty_string_when_env_section_missing(self, monkeypatch, make_workflow_response):
        """Test that missing env section returns empty string.

        Edge case: Valid workflow YAML but no env section at all.
        This tests defensive handling when expected structure is absent.
        """
        # Workflow without env section - simulates minimal workflow file
        response = make_workflow_response("name: deploy\njobs: {}\n")
        monkeypatch.setattr(
            "update_openhands_charts.requests.get",
            Mock(return_value=response)
        )

        result = get_deploy_config("token", "owner/repo")

        assert result is not None
        assert result.runtime_api_sha == ""

    # =========================================================================
    # Parameterized error path tests
    #
    # Recovery behavior: All errors return None rather than raising exceptions.
    # This design allows the caller (main()) to gracefully skip the update when
    # deploy config is unavailable, rather than failing the entire CI/CD run.
    # The printed error message enables operators to diagnose issues from logs.
    # =========================================================================

    @pytest.mark.parametrize("error_name,setup_mock", [
        # =====================================================================
        # Network-level errors (transient, typically retryable)
        # Recovery: Caller should retry with exponential backoff or skip update
        # =====================================================================
        (
            "connection_timeout",
            lambda: Mock(side_effect=Exception("Connection timed out")),
        ),
        (
            "connection_refused",
            lambda: Mock(side_effect=Exception("Connection refused")),
        ),
        (
            "dns_resolution_failed",
            lambda: Mock(side_effect=Exception("Name resolution failed")),
        ),
        # =====================================================================
        # HTTP error responses (4xx client errors vs 5xx server errors)
        # Recovery: 4xx errors indicate config issues (check token/repo path);
        #           5xx errors are transient (retry or wait for GitHub recovery)
        # =====================================================================
        (
            "http_401_unauthorized",
            lambda: make_http_error_response(401, "Unauthorized"),
        ),
        (
            "http_403_forbidden",
            lambda: make_http_error_response(403, "Forbidden"),
        ),
        (
            "http_404_not_found",
            lambda: make_http_error_response(404, "Not Found"),
        ),
        (
            "http_500_server_error",
            lambda: make_http_error_response(500, "Internal Server Error"),
        ),
        (
            "http_502_bad_gateway",
            lambda: make_http_error_response(502, "Bad Gateway"),
        ),
        (
            "http_503_unavailable",
            lambda: make_http_error_response(503, "Service Unavailable"),
        ),
        # =====================================================================
        # Response parsing errors (data corruption or API contract violations)
        # Recovery: These indicate unexpected API behavior; check GitHub status
        #           or report bug if persistent. Update should be skipped.
        # =====================================================================
        (
            "invalid_json_response",
            lambda: make_json_error_response(),
        ),
        (
            "missing_content_key",
            lambda: make_missing_key_response({}),
        ),
        (
            "null_content_value",
            lambda: make_missing_key_response({"content": None}),
        ),
        # =====================================================================
        # Base64 decoding errors (corrupted file content in repository)
        # Recovery: Check the workflow file in the repository for corruption;
        #           these errors indicate the file content itself is invalid.
        # =====================================================================
        (
            "invalid_base64_content",
            lambda: make_invalid_base64_response("not-valid-base64!!!"),
        ),
        (
            "corrupted_base64_content",
            lambda: make_invalid_base64_response("YWJj==="),  # Invalid padding
        ),
        # =====================================================================
        # YAML parsing errors (malformed workflow file syntax)
        # Recovery: Fix the workflow YAML syntax in the source repository.
        #           These errors indicate the deploy workflow file is invalid.
        # =====================================================================
        (
            "invalid_yaml_syntax",
            lambda: make_invalid_yaml_response("{{invalid: yaml: ::"),
        ),
        (
            "yaml_with_tabs",
            lambda: make_invalid_yaml_response("env:\n\t\tinvalid_indent: true"),
        ),
    ])
    def test_returns_none_and_prints_error(self, error_name, setup_mock, monkeypatch, capsys):
        """Test that error scenarios return None and print an error message.

        All error paths in get_deploy_config should:
        1. Return None (not raise an exception) - enables graceful degradation
        2. Print an error message containing "Error fetching deploy config" - enables debugging

        This fail-safe design ensures CI/CD pipelines can continue even when
        deploy config is temporarily unavailable, while providing clear diagnostic
        output for operators to investigate and resolve the underlying issue.
        """
        mock_get = setup_mock()
        monkeypatch.setattr("update_openhands_charts.requests.get", mock_get)

        result = get_deploy_config("fake-token", "owner/repo")

        assert result is None, f"Expected None for {error_name}, got {result}"
        captured = capsys.readouterr()
        assert "Error fetching deploy config" in captured.out, (
            f"Expected error message for {error_name}, got: {captured.out}"
        )


class TestResolveOpenhandsVersion:
    """Tests for resolve_openhands_version function.

    Determines which cloud tag to use for updates — either a caller-specified
    tag or the latest tag fetched from GitHub.
    """

    def test_returns_specified_tag_when_it_exists(self, stub_cloud_tag_exists):
        """When cloud_tag is provided and exists in the repo, return it."""
        stub_cloud_tag_exists(True)

        result = resolve_openhands_version("token", "cloud-1.20.0")

        assert result == "cloud-1.20.0"

    def test_returns_none_when_specified_tag_does_not_exist(self, stub_cloud_tag_exists, capsys):
        """When cloud_tag is provided but not found in the repo, return None and print error."""
        stub_cloud_tag_exists(False)

        result = resolve_openhands_version("token", "cloud-99.0.0")

        assert result is None
        assert "does not exist" in capsys.readouterr().out

    def test_fetches_latest_tag_when_no_tag_specified(self, stub_latest_cloud_tag):
        """When no cloud_tag is given, returns the latest cloud tag from GitHub."""
        stub_latest_cloud_tag("cloud-1.20.0")

        result = resolve_openhands_version("token", None)

        assert result == "cloud-1.20.0"

    def test_returns_none_and_prints_when_no_tags_found(self, stub_latest_cloud_tag, capsys):
        """When no cloud_tag is given and none found in repo, return None and print message."""
        stub_latest_cloud_tag(None)

        result = resolve_openhands_version("token", None)

        assert result is None
        assert "No cloud tag found" in capsys.readouterr().out


class TestUpdateValues:
    """Tests for update_openhands_values function.

    Test Structure:
    - test_update_*_tag: Image tag update behavior for each component
    - test_unchanged_when_same_values: Idempotency verification
    - test_preserves_other_content: Non-destructive update check
    - test_returns_*: Return value behavior
    - test_reports_error_*: Error handling for missing patterns

    TDD Rationale: Tests drive regex-based image tag replacement that must
    handle three distinct tag locations (enterprise-server, runtime, warmRuntimes).
    Error tests ensure graceful handling when expected patterns are missing,
    preventing silent failures in CI/CD pipelines.
    """

    @pytest.fixture
    def temp_values_file(self, make_temp_yaml_file, sample_openhands_values_full):
        """Create a temporary values.yaml file using shared fixtures."""
        return make_temp_yaml_file(sample_openhands_values_full)

    @pytest.mark.parametrize("expected_content", [
        pytest.param("tag: cloud-1.1.0", id="enterprise-server tag"),
        pytest.param(f"tag: {NEW_RUNTIME_IMAGE_TAG}", id="runtime tag"),
        pytest.param(f'image: "ghcr.io/openhands/agent-server:{NEW_RUNTIME_IMAGE_TAG}"', id="warmRuntimes tag"),
    ])
    def test_each_image_tag_is_updated(self, temp_values_file, expected_content):
        """Test that enterprise-server, runtime, and warmRuntimes image tags are each updated.

        All three tags are written by one call; each parametrize case verifies
        one tag location in the output file.
        """
        update_openhands_values(
            temp_values_file,
            openhands_version="cloud-1.1.0",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
        )

        assert_file_contains(temp_values_file, expected_content)

    @pytest.fixture
    def reapplied_values_result(self, temp_values_file):
        """Apply identical values twice and return the second-call UpdateResult.

        Idempotency pattern: The two-step (apply → reapply) structure verifies
        that calling the function with identical values produces an UpdateResult
        with no changes. This ensures update functions are deterministic and
        don't cause spurious version bumps when no actual changes occur.
        """
        update_openhands_values(
            temp_values_file,
            openhands_version="cloud-1.0.0",
            runtime_image_tag=RUNTIME_IMAGE_TAG,
        )
        return update_openhands_values(
            temp_values_file,
            openhands_version="cloud-1.0.0",
            runtime_image_tag=RUNTIME_IMAGE_TAG,
        )

    def test_reapplying_same_values_reports_no_changes(self, reapplied_values_result):
        """Reapplying identical values sets has_changes=False on the result."""
        assert reapplied_values_result.has_changes is False

    @pytest.mark.parametrize("unchanged_key", [
        "enterprise-server image tag",
        "runtime image tag",
        "warmRuntimes image tag",
    ])
    def test_reapplying_same_values_marks_key_unchanged(self, reapplied_values_result, unchanged_key):
        """Each image-tag key is reported as unchanged when reapplied with the same value."""
        assert reapplied_values_result.is_unchanged(unchanged_key)

    def test_preserves_other_content(self, temp_values_file):
        """Test that other content in values.yaml is preserved."""
        update_openhands_values(
            temp_values_file,
            openhands_version="cloud-1.1.0",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
        )

        assert_file_contains_all(temp_values_file, [
            "allowedUsers: null",
            "runAsRoot: true",
            "replicaCount: 1",
            'working_dir: "/openhands/code/"',
        ])

    def test_returns_true_when_changes_made(self, temp_values_file):
        """Test that function returns True when changes are made."""
        result = update_openhands_values(
            temp_values_file,
            openhands_version="cloud-1.1.0",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
        )

        assert result.has_changes is True

    def test_reports_error_when_enterprise_server_tag_missing(self, make_temp_yaml_file):
        """Test that error is reported when enterprise-server image tag pattern not found.

        Edge case rationale: The enterprise-server image is the main OpenHands backend.
        If this pattern is missing, the chart update would silently skip updating the
        core application version, leading to version drift between Chart.yaml appVersion
        and the actual deployed container. Early error reporting prevents silent failures.
        """
        # YAML without enterprise-server image section - simulates misconfigured values.yaml
        values_content = """\
image:
  repository: ghcr.io/other/image
  tag: v1.0.0

runtime:
  image:
    repository: ghcr.io/openhands/agent-server
    tag: 1.0.0-python

runtime-api:
  warmRuntimes:
    configs:
      - name: default
        image: "ghcr.io/openhands/agent-server:1.0.0-python"
"""
        temp_file = make_temp_yaml_file(values_content)

        result = update_openhands_values(
            temp_file,
            openhands_version="cloud-1.1.0",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
        )

        assert result.has_error_containing("Could not find enterprise-server image tag")

    def test_reports_error_when_runtime_tag_missing(self, make_temp_yaml_file):
        """Test that error is reported when runtime image tag pattern not found.

        Edge case rationale: The runtime image runs user code in sandboxed containers.
        Version mismatch between enterprise-server and runtime can cause compatibility
        issues (API changes, protocol mismatches). Detecting missing runtime patterns
        ensures both images stay synchronized during updates.
        """
        # YAML without runtime image section - enterprise-server present but runtime missing
        values_content = """\
image:
  repository: ghcr.io/openhands/enterprise-server
  tag: cloud-1.0.0

runtime-api:
  warmRuntimes:
    configs:
      - name: default
        image: "ghcr.io/openhands/agent-server:1.0.0-python"
"""
        temp_file = make_temp_yaml_file(values_content)

        result = update_openhands_values(
            temp_file,
            openhands_version="cloud-1.1.0",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
        )

        assert result.has_error_containing("Could not find runtime image tag")

    def test_reports_error_when_warm_runtimes_tag_missing(self, make_temp_yaml_file):
        """Test that error is reported when warmRuntimes image tag pattern not found.

        Edge case rationale: warmRuntimes pre-provisions runtime containers for faster
        cold starts. If this image isn't updated but runtime is, pre-warmed containers
        would run stale versions until recycled. This creates inconsistent behavior
        where some requests use new runtime and others use old pre-warmed instances.
        """
        # YAML with warmRuntimes disabled - pattern missing but section exists
        values_content = """\
image:
  repository: ghcr.io/openhands/enterprise-server
  tag: cloud-1.0.0

runtime:
  image:
    repository: ghcr.io/openhands/agent-server
    tag: 1.0.0-python

runtime-api:
  warmRuntimes:
    enabled: false
"""
        temp_file = make_temp_yaml_file(values_content)

        result = update_openhands_values(
            temp_file,
            openhands_version="cloud-1.1.0",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
        )

        assert result.has_error_containing("Could not find warmRuntimes image tag")

    def test_collects_multiple_errors_when_multiple_patterns_missing(self, make_temp_yaml_file):
        """Test that all missing patterns are reported as errors.

        Edge case rationale: When values.yaml is severely malformed or from an
        incompatible chart version, multiple patterns will be missing. Collecting
        ALL errors (not just the first) allows operators to fix all issues in one
        pass rather than discovering them one-by-one through repeated runs.
        """
        # Minimal YAML with none of the expected patterns - completely wrong structure
        values_content = """\
replicaCount: 1
serviceAccount:
  create: true
"""
        temp_file = make_temp_yaml_file(values_content)

        result = update_openhands_values(
            temp_file,
            openhands_version="cloud-1.1.0",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
        )

        assert result.error_count == 3
        assert result.has_error_containing("enterprise-server")
        assert result.has_error_containing("runtime image tag")
        assert result.has_error_containing("warmRuntimes")



class TestUpdateReplicatedOpenhandsValues:
    """Tests for replicated/openhands.yaml agent-server image updates."""

    @pytest.fixture
    def temp_replicated_wrapper_file(self, make_temp_yaml_file, sample_replicated_openhands_wrapper_values):
        """Create a temporary replicated wrapper YAML file."""
        return make_temp_yaml_file(sample_replicated_openhands_wrapper_values)

    @pytest.mark.parametrize("expected_content", [
        pytest.param("tag: '1.19.1-python'", id="proxy runtime tag"),
        pytest.param(
            "image: 'images.r9.all-hands.dev/proxy/{{repl LicenseFieldValue \"appSlug\"}}/ghcr.io/openhands/agent-server:1.19.1-python'",
            id="proxy warmRuntimes image",
        ),
        pytest.param(
            "image: '{{repl LocalRegistryHost }}/{{repl LocalRegistryNamespace }}/agent-server:1.19.1-python'",
            id="local registry image",
        ),
    ])
    def test_replicated_wrapper_file_content_updated(self, temp_replicated_wrapper_file, expected_content):
        """Test that each agent-server tag location in the replicated wrapper file is updated."""
        update_replicated_openhands_values(
            temp_replicated_wrapper_file,
            runtime_image_tag="1.19.1-python",
        )

        assert_file_contains(temp_replicated_wrapper_file, expected_content)

    @pytest.mark.parametrize("change_key", [
        "replicated runtime image tag",
        "replicated warmRuntimes image tag",
        "replicated local registry runtime image tag",
        "replicated local registry warmRuntimes image tag",
    ])
    def test_result_records_replicated_wrapper_change(self, temp_replicated_wrapper_file, change_key):
        """Test that each replicated wrapper tag key is recorded as changed in the result."""
        result = update_replicated_openhands_values(
            temp_replicated_wrapper_file,
            runtime_image_tag="1.19.1-python",
        )

        assert result.has_change_for(change_key)

    @pytest.fixture
    def reapplied_replicated_wrapper_result(self, temp_replicated_wrapper_file):
        """Apply identical replicated wrapper values twice and return the second-call UpdateResult."""
        update_replicated_openhands_values(
            temp_replicated_wrapper_file,
            runtime_image_tag="1.19.1-python",
        )
        return update_replicated_openhands_values(
            temp_replicated_wrapper_file,
            runtime_image_tag="1.19.1-python",
        )

    def test_reapplying_same_replicated_wrapper_values_reports_no_changes(self, reapplied_replicated_wrapper_result):
        """Reapplying identical replicated wrapper values sets has_changes=False."""
        assert reapplied_replicated_wrapper_result.has_changes is False

    @pytest.mark.parametrize("unchanged_key", [
        "replicated runtime image tag",
        "replicated warmRuntimes image tag",
        "replicated local registry runtime image tag",
        "replicated local registry warmRuntimes image tag",
    ])
    def test_reapplying_same_replicated_wrapper_values_marks_key_unchanged(self, reapplied_replicated_wrapper_result, unchanged_key):
        """Each replicated wrapper tag key is reported as unchanged when reapplied."""
        assert reapplied_replicated_wrapper_result.is_unchanged(unchanged_key)


class TestConditionalChartVersionBump:
    """Tests for conditional chart version bumping across both chart types.

    Both openhands and runtime-api charts use the same pattern: only bump
    the chart version when has_changes=True. This consolidates testing of
    that behavior to reduce redundancy (Necessary property).

    TDD Rationale: These tests drive the has_changes flag behavior that
    prevents unnecessary version bumps when only checking for updates.
    """

    @pytest.fixture
    def temp_openhands_chart_file(self, make_temp_yaml_file, sample_openhands_chart_minimal):
        """Create a temporary openhands Chart.yaml file."""
        return make_temp_yaml_file(sample_openhands_chart_minimal)

    @pytest.fixture
    def temp_runtime_api_chart_file(self, make_temp_yaml_file, sample_runtime_api_chart_minimal):
        """Create a temporary runtime-api Chart.yaml file."""
        return make_temp_yaml_file(sample_runtime_api_chart_minimal)

    # --- Openhands chart tests ---

    def test_openhands_no_version_bump_when_no_changes(self, temp_openhands_chart_file):
        """Test that openhands chart version is not bumped when has_changes is False."""
        result = update_openhands_chart(
            temp_openhands_chart_file,
            new_app_version=OPENHANDS_CHART_APP_VERSION,
            new_runtime_api_version=OPENHANDS_CHART_RUNTIME_API_VERSION,
            has_changes=False,
        )

        assert get_chart_value(temp_openhands_chart_file, "version") == OPENHANDS_CHART_VERSION
        assert get_chart_value(temp_openhands_chart_file, "appVersion") == OPENHANDS_CHART_APP_VERSION
        assert result.is_unchanged("openhands chart version")

    def test_openhands_chart_file_updated_when_has_changes(self, temp_openhands_chart_file):
        """File content: version bumped and appVersion replaced when has_changes is True."""
        update_openhands_chart(
            temp_openhands_chart_file,
            new_app_version="cloud-1.1.0",
            new_runtime_api_version="0.2.7",
            has_changes=True,
        )

        assert get_chart_value(temp_openhands_chart_file, "version") == "0.1.1"  # Bumped from 0.1.0
        assert get_chart_value(temp_openhands_chart_file, "appVersion") == "cloud-1.1.0"

    def test_openhands_update_result_records_changes_when_has_changes(self, temp_openhands_chart_file):
        """Result object: records both appVersion and version as changed when has_changes is True."""
        result = update_openhands_chart(
            temp_openhands_chart_file,
            new_app_version="cloud-1.1.0",
            new_runtime_api_version="0.2.7",
            has_changes=True,
        )

        assert result.has_change_for("appVersion")
        assert result.has_change_for("version")

    # --- Runtime-api chart tests ---

    def test_runtime_api_no_version_bump_when_no_changes(self, temp_runtime_api_chart_file):
        """Test that runtime-api chart version is not bumped when has_changes is False."""
        new_version, result = update_runtime_api_chart(temp_runtime_api_chart_file, has_changes=False)

        assert new_version == RUNTIME_API_CHART_MINIMAL_VERSION  # Version unchanged
        assert result.is_unchanged("runtime-api chart version")

    def test_runtime_api_version_bump_when_has_changes(self, temp_runtime_api_chart_file):
        """Test that runtime-api chart version is bumped when has_changes is True."""
        new_version, result = update_runtime_api_chart(temp_runtime_api_chart_file, has_changes=True)

        expected_version = bump_patch_version(RUNTIME_API_CHART_MINIMAL_VERSION)
        assert new_version == expected_version  # Version bumped
        assert result.has_change_for("runtime-api chart version")


class TestDryRun:
    """Tests for dry-run functionality.

    Dry-run mode allows users to preview changes without modifying files.
    These tests verify that:
    1. Files remain unchanged when dry_run=True
    2. Return values still reflect what *would* change
    3. Files are modified when dry_run=False (control tests)

    Test Structure:
    - test_*_dry_run_no_file_changes: File content unchanged
    - test_*_dry_run_prints_changes: Return value reflects changes
    - test_*_without_dry_run_modifies_file: Control to verify normal behavior

    TDD Rationale: Tests drive the dry_run parameter behavior, ensuring
    separation between change detection (always happens) and file writing
    (only when dry_run=False). Control tests verify the default behavior
    hasn't regressed.
    """

    @pytest.fixture
    def temp_chart_file(self, make_temp_yaml_file, sample_openhands_chart_with_deps):
        """Create a temporary Chart.yaml file using shared fixture."""
        return make_temp_yaml_file(sample_openhands_chart_with_deps)

    @pytest.fixture
    def temp_values_file(self, make_temp_yaml_file, sample_openhands_values_minimal):
        """Create a temporary values.yaml file using shared fixtures."""
        return make_temp_yaml_file(sample_openhands_values_minimal)

    def test_update_chart_dry_run_no_file_changes(self, temp_chart_file):
        """Test that dry-run doesn't modify Chart.yaml."""
        # Arrange: capture original state
        original_content = temp_chart_file.read_text()

        # Act: run update with dry_run=True
        update_openhands_chart(temp_chart_file, NEW_APP_VERSION, NEW_RUNTIME_API_VERSION, dry_run=True)

        # Assert: file unchanged
        assert temp_chart_file.read_text() == original_content

    def test_update_chart_dry_run_prints_changes(self, temp_chart_file):
        """Test that dry-run still records what would be changed."""
        # Act
        result = update_openhands_chart(temp_chart_file, NEW_APP_VERSION, NEW_RUNTIME_API_VERSION, dry_run=True)

        # Assert: changes are tracked even though file wasn't modified
        assert result.has_change_for("appVersion")
        assert result.has_change_for("version")
        assert result.has_change_for("runtime-api version")

    def test_update_values_dry_run_no_file_changes(self, temp_values_file):
        """Test that dry-run doesn't modify values.yaml."""
        # Arrange: capture original state
        original_content = temp_values_file.read_text()

        # Act: run update with dry_run=True
        update_openhands_values(
            temp_values_file,
            openhands_version="cloud-1.1.0",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
            dry_run=True,
        )

        # Assert: file unchanged
        assert temp_values_file.read_text() == original_content

    def test_update_values_dry_run_prints_changes(self, temp_values_file):
        """Test that dry-run still records what would be changed."""
        # Act
        result = update_openhands_values(
            temp_values_file,
            openhands_version="cloud-1.1.0",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
            dry_run=True,
        )

        # Assert: changes are tracked even though file wasn't modified
        assert result.has_change_for("enterprise-server image tag")
        assert result.has_change_for("runtime image tag")
        assert result.has_change_for("warmRuntimes image tag")

    def test_update_chart_without_dry_run_modifies_file(self, temp_chart_file):
        """Test that without dry-run, Chart.yaml is modified."""
        # Arrange: capture original state
        original_content = temp_chart_file.read_text()

        # Act: run update with dry_run=False (default behavior)
        update_openhands_chart(temp_chart_file, NEW_APP_VERSION, NEW_RUNTIME_API_VERSION, dry_run=False)

        # Assert: file was modified
        assert temp_chart_file.read_text() != original_content

    def test_update_values_without_dry_run_modifies_file(self, temp_values_file):
        """Test that without dry-run, values.yaml is modified."""
        # Arrange: capture original state
        original_content = temp_values_file.read_text()

        # Act: run update with dry_run=False (default behavior)
        update_openhands_values(
            temp_values_file,
            openhands_version="cloud-1.1.0",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
            dry_run=False,
        )

        # Assert: file was modified
        assert temp_values_file.read_text() != original_content


class TestUpdateRuntimeApiChart:
    """Tests for update_runtime_api_chart function."""

    @pytest.fixture
    def temp_runtime_api_chart_file(self, make_temp_yaml_file, sample_runtime_api_chart_full):
        """Create a temporary runtime-api Chart.yaml file using shared fixtures."""
        return make_temp_yaml_file(sample_runtime_api_chart_full)

    def test_bump_runtime_api_version_writes_bumped_version_to_file(self, temp_runtime_api_chart_file):
        """File content: runtime-api chart file is updated to the bumped version."""
        expected_version = bump_patch_version(RUNTIME_API_CHART_FULL_VERSION)
        update_runtime_api_chart(temp_runtime_api_chart_file)

        assert get_chart_value(temp_runtime_api_chart_file, "version") == expected_version

    def test_bump_runtime_api_version_returns_bumped_version(self, temp_runtime_api_chart_file):
        """Return value: bumped version is returned to the caller."""
        expected_version = bump_patch_version(RUNTIME_API_CHART_FULL_VERSION)
        new_version, result = update_runtime_api_chart(temp_runtime_api_chart_file)

        assert new_version == expected_version

    @pytest.mark.parametrize("key,expected", [
        ("apiVersion", "v2"),
        ("name", "runtime-api"),
        ("appVersion", "1.0.0"),
    ])
    def test_scalar_fields_preserved_after_version_bump(self, temp_runtime_api_chart_file, key, expected):
        """Verify scalar fields are not modified by runtime-api chart version bump."""
        update_runtime_api_chart(temp_runtime_api_chart_file)

        assert get_chart_value(temp_runtime_api_chart_file, key) == expected

    def test_dependencies_count_preserved_after_version_bump(self, temp_runtime_api_chart_file):
        """Verify dependencies list length is not modified by runtime-api chart version bump."""
        original_count = len(get_chart_value(temp_runtime_api_chart_file, "dependencies"))

        update_runtime_api_chart(temp_runtime_api_chart_file)

        assert len(get_chart_value(temp_runtime_api_chart_file, "dependencies")) == original_count

    def test_dry_run_no_file_changes(self, temp_runtime_api_chart_file):
        """Test that dry-run doesn't modify the file."""
        original_content = temp_runtime_api_chart_file.read_text()

        update_runtime_api_chart(temp_runtime_api_chart_file, dry_run=True)

        assert temp_runtime_api_chart_file.read_text() == original_content

    def test_dry_run_returns_new_version(self, temp_runtime_api_chart_file):
        """Test that dry-run still returns the new version."""
        expected_version = bump_patch_version(RUNTIME_API_CHART_FULL_VERSION)
        new_version, result = update_runtime_api_chart(temp_runtime_api_chart_file, dry_run=True)
        assert new_version == expected_version


class TestUpdateRuntimeApiValues:
    """Tests for update_runtime_api_values function."""

    @pytest.fixture
    def temp_runtime_api_values_file(self, make_temp_yaml_file, sample_runtime_api_values):
        """Create a temporary runtime-api values.yaml file using shared fixtures."""
        return make_temp_yaml_file(sample_runtime_api_values)

    def test_update_image_tag(self, temp_runtime_api_values_file):
        """Test that runtime-api image tag is updated correctly."""
        update_runtime_api_values(
            temp_runtime_api_values_file,
            runtime_api_sha="abc1234567890def",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
        )

        assert_file_contains(temp_runtime_api_values_file, "tag: sha-abc1234")

    def test_update_warm_runtimes_image_uses_runtime_image_tag(self, temp_runtime_api_values_file):
        """Test that warmRuntimes image tag uses value from deploy config."""
        update_runtime_api_values(
            temp_runtime_api_values_file,
            runtime_api_sha="abc1234567890def",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
        )

        # Should use runtime_image_tag from deploy config
        assert_file_contains(temp_runtime_api_values_file, f'image: "ghcr.io/openhands/agent-server:{NEW_RUNTIME_IMAGE_TAG}"')

    @pytest.fixture
    def reapplied_runtime_api_values_result(self, temp_runtime_api_values_file):
        """Apply identical runtime-api values twice and return the second-call UpdateResult.

        Idempotency pattern: Verifies runtime-api update function is deterministic.
        See TestUpdateValues.reapplied_values_result for pattern rationale.
        """
        update_runtime_api_values(
            temp_runtime_api_values_file,
            runtime_api_sha="abc1234567890def",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
        )
        return update_runtime_api_values(
            temp_runtime_api_values_file,
            runtime_api_sha="abc1234567890def",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
        )

    def test_reapplying_same_runtime_api_values_reports_no_changes(self, reapplied_runtime_api_values_result):
        """Reapplying identical runtime-api values sets has_changes=False."""
        assert reapplied_runtime_api_values_result.has_changes is False

    @pytest.mark.parametrize("unchanged_key", [
        "runtime-api image tag",
        "runtime-api warmRuntimes image tag",
    ])
    def test_reapplying_same_runtime_api_values_marks_key_unchanged(self, reapplied_runtime_api_values_result, unchanged_key):
        """Each runtime-api image-tag key is reported as unchanged when reapplied."""
        assert reapplied_runtime_api_values_result.is_unchanged(unchanged_key)

    def test_preserves_other_content(self, temp_runtime_api_values_file):
        """Test that other content is preserved."""
        update_runtime_api_values(
            temp_runtime_api_values_file,
            runtime_api_sha="abc1234567890def",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
        )

        assert_file_contains_all(temp_runtime_api_values_file, [
            "replicaCount: 1",
            'working_dir: "/openhands/code/"',
        ])

    def test_dry_run_no_file_changes(self, temp_runtime_api_values_file):
        """Test that dry-run doesn't modify the file."""
        original_content = temp_runtime_api_values_file.read_text()

        update_runtime_api_values(
            temp_runtime_api_values_file,
            runtime_api_sha="abc1234567890def",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
            dry_run=True,
        )

        assert temp_runtime_api_values_file.read_text() == original_content

    def test_returns_true_when_changes_made(self, temp_runtime_api_values_file):
        """Test that function returns True when changes are made."""
        result = update_runtime_api_values(
            temp_runtime_api_values_file,
            runtime_api_sha="abc1234567890def",
            runtime_image_tag=NEW_RUNTIME_IMAGE_TAG,
        )

        assert result.has_changes is True


class TestSkipVersionCheck:
    """Tests for --skip-version-check flag behavior.

    Without the flag, the script exits early when the chart version already
    matches the latest cloud tag. With the flag, it continues past that check.
    """

    MOCK_CLOUD_TAG = "cloud-1.20.0"

    def test_exits_early_when_versions_match_without_flag(self, mock_main_early_exit, capsys):
        """Without --skip-version-check, exits with 'already up to date' message."""
        mock_main_early_exit(self.MOCK_CLOUD_TAG)

        main(dry_run=True)

        assert "Charts are already up to date" in capsys.readouterr().out

    def test_skips_up_to_date_check_when_flag_set(self, mock_main_early_exit, capsys):
        """With --skip-version-check, continues past version check even when versions match."""
        mock_main_early_exit(self.MOCK_CLOUD_TAG)

        main(dry_run=True, skip_version_check=True)

        assert "Charts are already up to date" not in capsys.readouterr().out


class TestProcessUpdates:
    """Tests for process_updates function.

    Orchestrates version fetching, config retrieval, and file updates.
    These tests cover the guard clauses that prevent partial updates when
    upstream data is unavailable.
    """

    def test_returns_early_when_version_resolution_fails(self, monkeypatch, stub_process_updates_chain):
        """When resolve_openhands_version returns None, no deploy config fetch is attempted."""
        stub_process_updates_chain(openhands_version=None)
        mock_get_deploy_config = MagicMock()
        monkeypatch.setattr("update_openhands_charts.get_deploy_config", mock_get_deploy_config)

        process_updates("token")

        mock_get_deploy_config.assert_not_called()

    def test_returns_early_when_runtime_image_tag_unavailable(self, monkeypatch, stub_process_updates_chain, capsys):
        """When runtime image tag fetch fails, no deploy config fetch is attempted."""
        stub_process_updates_chain(runtime_image_tag=None)
        mock_get_deploy_config = MagicMock()
        monkeypatch.setattr("update_openhands_charts.get_deploy_config", mock_get_deploy_config)

        process_updates("token")

        mock_get_deploy_config.assert_not_called()
        assert "Could not fetch runtime image tag" in capsys.readouterr().out

    def test_returns_early_when_deploy_config_unavailable(self, monkeypatch, stub_process_updates_chain, capsys):
        """When deploy config fetch fails, no file updates are attempted."""
        stub_process_updates_chain()
        monkeypatch.setattr(
            "update_openhands_charts.get_deploy_config",
            lambda token, repo, ref: None,
        )
        mock_update_runtime_api = MagicMock()
        monkeypatch.setattr(
            "update_openhands_charts.update_runtime_api_workflow",
            mock_update_runtime_api,
        )

        process_updates("token")

        mock_update_runtime_api.assert_not_called()
        assert "Could not fetch deploy config" in capsys.readouterr().out


class TestUpdateRuntimeApiWorkflow:
    """Tests for update_runtime_api_workflow orchestration.

    The inner functions update_runtime_api_values and update_runtime_api_chart
    are already covered by ~30 tests; these focus on the workflow's distinct
    contract: how it wires arguments between the two calls, threads dry_run,
    and propagates has_changes from values into the chart bump decision.
    """

    @pytest.fixture
    def patched_inner_calls(self, monkeypatch):
        """Mock both inner update functions and return their MagicMocks for assertion."""
        mock_values = MagicMock(return_value=update_openhands_charts.UpdateResult(has_changes=True))
        mock_chart = MagicMock(return_value=("0.1.21", update_openhands_charts.UpdateResult()))
        monkeypatch.setattr("update_openhands_charts.update_runtime_api_values", mock_values)
        monkeypatch.setattr("update_openhands_charts.update_runtime_api_chart", mock_chart)
        return mock_values, mock_chart

    def test_returns_chart_version_from_inner_call(self, patched_inner_calls):
        """The returned value is the new chart version produced by update_runtime_api_chart."""
        _, mock_chart = patched_inner_calls
        mock_chart.return_value = ("0.9.99", update_openhands_charts.UpdateResult())

        result = update_runtime_api_workflow(DeployConfig(runtime_api_sha="abc"), "tag", dry_run=False)

        assert result == "0.9.99"

    def test_chart_call_receives_has_changes_true_when_values_changed(self, monkeypatch):
        """When values has changes, the chart is invoked with has_changes=True so version bumps."""
        monkeypatch.setattr(
            "update_openhands_charts.update_runtime_api_values",
            MagicMock(return_value=update_openhands_charts.UpdateResult(has_changes=True)),
        )
        mock_chart = MagicMock(return_value=("0.1.21", update_openhands_charts.UpdateResult()))
        monkeypatch.setattr("update_openhands_charts.update_runtime_api_chart", mock_chart)

        update_runtime_api_workflow(DeployConfig(runtime_api_sha="abc"), "tag", dry_run=False)

        assert mock_chart.call_args.kwargs["has_changes"] is True

    def test_chart_call_receives_has_changes_false_when_values_unchanged(self, monkeypatch):
        """When values has no changes, the chart is invoked with has_changes=False so no bump occurs."""
        monkeypatch.setattr(
            "update_openhands_charts.update_runtime_api_values",
            MagicMock(return_value=update_openhands_charts.UpdateResult(has_changes=False)),
        )
        mock_chart = MagicMock(return_value=("0.1.20", update_openhands_charts.UpdateResult()))
        monkeypatch.setattr("update_openhands_charts.update_runtime_api_chart", mock_chart)

        update_runtime_api_workflow(DeployConfig(runtime_api_sha="abc"), "tag", dry_run=False)

        assert mock_chart.call_args.kwargs["has_changes"] is False

    def test_values_call_receives_runtime_api_sha_from_deploy_config(self, patched_inner_calls):
        """The runtime_api_sha is extracted from deploy_config and passed positionally to values."""
        mock_values, _ = patched_inner_calls

        update_runtime_api_workflow(DeployConfig(runtime_api_sha="cafef00d"), "tag", dry_run=False)

        # update_runtime_api_values(path, sha, image_tag, dry_run=...) — sha is the 2nd positional arg
        assert mock_values.call_args.args[1] == "cafef00d"

    def test_values_call_receives_runtime_image_tag(self, patched_inner_calls):
        """The runtime_image_tag is passed through to values as the 3rd positional argument."""
        mock_values, _ = patched_inner_calls

        update_runtime_api_workflow(DeployConfig(runtime_api_sha="abc"), "image-tag-v9", dry_run=False)

        assert mock_values.call_args.args[2] == "image-tag-v9"

    @pytest.mark.parametrize("dry_run", [True, False])
    def test_dry_run_is_propagated_to_both_inner_calls(self, patched_inner_calls, dry_run):
        """The dry_run flag is forwarded to both update_runtime_api_values and update_runtime_api_chart."""
        mock_values, mock_chart = patched_inner_calls

        update_runtime_api_workflow(DeployConfig(runtime_api_sha="abc"), "tag", dry_run=dry_run)

        assert mock_values.call_args.kwargs["dry_run"] is dry_run
        assert mock_chart.call_args.kwargs["dry_run"] is dry_run


class TestUpdateOpenhandsWorkflow:
    """Tests for update_openhands_workflow orchestration.

    Focuses on the wiring contract: openhands_version flows to both inner calls,
    runtime_api_version only to chart, runtime_image_tag only to values, and
    has_changes propagates from values into the chart's bump decision.
    """

    @pytest.fixture
    def patched_inner_calls(self, monkeypatch):
        """Mock all three inner update functions and return their MagicMocks.

        Mocking update_replicated_openhands_values is mandatory: without it,
        the workflow writes to the real replicated/openhands.yaml on disk.
        """
        mock_values = MagicMock(return_value=update_openhands_charts.UpdateResult(has_changes=True))
        mock_replicated = MagicMock(return_value=update_openhands_charts.UpdateResult())
        mock_chart = MagicMock(return_value=update_openhands_charts.UpdateResult())
        monkeypatch.setattr("update_openhands_charts.update_openhands_values", mock_values)
        monkeypatch.setattr("update_openhands_charts.update_replicated_openhands_values", mock_replicated)
        monkeypatch.setattr("update_openhands_charts.update_openhands_chart", mock_chart)
        return mock_values, mock_chart

    def test_chart_call_receives_has_changes_true_when_values_changed(self, monkeypatch):
        """When values has changes, the chart is invoked with has_changes=True so version bumps."""
        monkeypatch.setattr(
            "update_openhands_charts.update_openhands_values",
            MagicMock(return_value=update_openhands_charts.UpdateResult(has_changes=True)),
        )
        monkeypatch.setattr(
            "update_openhands_charts.update_replicated_openhands_values",
            MagicMock(return_value=update_openhands_charts.UpdateResult()),
        )
        mock_chart = MagicMock(return_value=update_openhands_charts.UpdateResult())
        monkeypatch.setattr("update_openhands_charts.update_openhands_chart", mock_chart)

        update_openhands_workflow(
            DeployConfig(runtime_api_sha="abc"),
            openhands_version="cloud-1.0.0",
            runtime_api_version="0.1.0",
            runtime_image_tag="tag",
            dry_run=False,
        )

        assert mock_chart.call_args.kwargs["has_changes"] is True

    def test_chart_call_receives_has_changes_false_when_values_unchanged(self, monkeypatch):
        """When values has no changes, the chart is invoked with has_changes=False so no bump."""
        monkeypatch.setattr(
            "update_openhands_charts.update_openhands_values",
            MagicMock(return_value=update_openhands_charts.UpdateResult(has_changes=False)),
        )
        monkeypatch.setattr(
            "update_openhands_charts.update_replicated_openhands_values",
            MagicMock(return_value=update_openhands_charts.UpdateResult()),
        )
        mock_chart = MagicMock(return_value=update_openhands_charts.UpdateResult())
        monkeypatch.setattr("update_openhands_charts.update_openhands_chart", mock_chart)

        update_openhands_workflow(
            DeployConfig(runtime_api_sha="abc"),
            openhands_version="cloud-1.0.0",
            runtime_api_version="0.1.0",
            runtime_image_tag="tag",
            dry_run=False,
        )

        assert mock_chart.call_args.kwargs["has_changes"] is False

    def test_values_call_receives_openhands_version(self, patched_inner_calls):
        """openhands_version is passed positionally to update_openhands_values as the 2nd argument."""
        mock_values, _ = patched_inner_calls

        update_openhands_workflow(
            DeployConfig(runtime_api_sha="abc"),
            openhands_version="cloud-9.9.9",
            runtime_api_version="0.1.0",
            runtime_image_tag="tag",
            dry_run=False,
        )

        assert mock_values.call_args.args[1] == "cloud-9.9.9"

    def test_values_call_receives_runtime_image_tag(self, patched_inner_calls):
        """runtime_image_tag is passed positionally to update_openhands_values as the 3rd argument."""
        mock_values, _ = patched_inner_calls

        update_openhands_workflow(
            DeployConfig(runtime_api_sha="abc"),
            openhands_version="cloud-1.0.0",
            runtime_api_version="0.1.0",
            runtime_image_tag="image-tag-v9",
            dry_run=False,
        )

        assert mock_values.call_args.args[2] == "image-tag-v9"

    def test_chart_call_receives_openhands_version(self, patched_inner_calls):
        """openhands_version is passed positionally to update_openhands_chart as the 2nd argument."""
        _, mock_chart = patched_inner_calls

        update_openhands_workflow(
            DeployConfig(runtime_api_sha="abc"),
            openhands_version="cloud-9.9.9",
            runtime_api_version="0.1.0",
            runtime_image_tag="tag",
            dry_run=False,
        )

        assert mock_chart.call_args.args[1] == "cloud-9.9.9"

    def test_chart_call_receives_runtime_api_version(self, patched_inner_calls):
        """runtime_api_version is passed positionally to update_openhands_chart as the 3rd argument."""
        _, mock_chart = patched_inner_calls

        update_openhands_workflow(
            DeployConfig(runtime_api_sha="abc"),
            openhands_version="cloud-1.0.0",
            runtime_api_version="0.9.99",
            runtime_image_tag="tag",
            dry_run=False,
        )

        assert mock_chart.call_args.args[2] == "0.9.99"

    @pytest.mark.parametrize("dry_run", [True, False])
    def test_dry_run_is_propagated_to_both_inner_calls(self, patched_inner_calls, dry_run):
        """The dry_run flag is forwarded to both update_openhands_values and update_openhands_chart."""
        mock_values, mock_chart = patched_inner_calls

        update_openhands_workflow(
            DeployConfig(runtime_api_sha="abc"),
            openhands_version="cloud-1.0.0",
            runtime_api_version="0.1.0",
            runtime_image_tag="tag",
            dry_run=dry_run,
        )

        assert mock_values.call_args.kwargs["dry_run"] is dry_run
        assert mock_chart.call_args.kwargs["dry_run"] is dry_run


class TestUpdateOpenhandsWorkflowReplicated:
    """Tests that update_openhands_workflow also updates replicated/openhands.yaml.

    The replicated KOTS wrapper embeds its own copy of the agent-server image tag
    (proxy + LocalRegistry variants) that the chart-values updater cannot reach.
    The workflow must update that file too, or Replicated installs ship a stale tag.
    """

    @pytest.fixture
    def patched_inner_calls(self, monkeypatch):
        """Mock all three inner update functions and return their MagicMocks."""
        mock_values = MagicMock(return_value=update_openhands_charts.UpdateResult(has_changes=True))
        mock_replicated = MagicMock(return_value=update_openhands_charts.UpdateResult(has_changes=True))
        mock_chart = MagicMock(return_value=update_openhands_charts.UpdateResult())
        monkeypatch.setattr("update_openhands_charts.update_openhands_values", mock_values)
        monkeypatch.setattr("update_openhands_charts.update_replicated_openhands_values", mock_replicated)
        monkeypatch.setattr("update_openhands_charts.update_openhands_chart", mock_chart)
        return mock_values, mock_replicated, mock_chart

    def test_replicated_updater_invoked_with_replicated_openhands_path(self, patched_inner_calls):
        """The workflow points the replicated updater at replicated/openhands.yaml."""
        _, mock_replicated, _ = patched_inner_calls

        update_openhands_workflow(
            DeployConfig(runtime_api_sha="abc"),
            openhands_version="cloud-1.0.0",
            runtime_api_version="0.1.0",
            runtime_image_tag="tag",
            dry_run=False,
        )

        assert mock_replicated.call_args.args[0] == update_openhands_charts.REPLICATED_OPENHANDS_PATH

    def test_replicated_updater_receives_runtime_image_tag(self, patched_inner_calls):
        """runtime_image_tag is forwarded to the replicated updater."""
        _, mock_replicated, _ = patched_inner_calls

        update_openhands_workflow(
            DeployConfig(runtime_api_sha="abc"),
            openhands_version="cloud-1.0.0",
            runtime_api_version="0.1.0",
            runtime_image_tag="9.9.9-python",
            dry_run=False,
        )

        assert mock_replicated.call_args.args[1] == "9.9.9-python"

    @pytest.mark.parametrize("dry_run", [True, False])
    def test_replicated_updater_receives_dry_run(self, patched_inner_calls, dry_run):
        """The dry_run flag is forwarded to the replicated updater."""
        _, mock_replicated, _ = patched_inner_calls

        update_openhands_workflow(
            DeployConfig(runtime_api_sha="abc"),
            openhands_version="cloud-1.0.0",
            runtime_api_version="0.1.0",
            runtime_image_tag="tag",
            dry_run=dry_run,
        )

        assert mock_replicated.call_args.kwargs["dry_run"] is dry_run


class TestMainOutputMessages:
    """Tests for main() output message formatting."""

    # Use a test constant to avoid magic strings scattered throughout tests
    MOCK_CLOUD_TAG = "cloud-1.20.0"

    @pytest.mark.parametrize("message_prefix", [
        pytest.param("OpenHands cloud tag", id="latest cloud tag line"),
        pytest.param("OpenHands-Cloud openhands chart appVersion", id="current appVersion line"),
    ])
    def test_message_format(self, capsys, mock_main_early_exit, message_prefix):
        """Verify each main() output line uses the '<prefix>: <cloud_tag>' format."""
        mock_main_early_exit(self.MOCK_CLOUD_TAG)

        main(dry_run=True)

        captured = capsys.readouterr()
        assert f"{message_prefix}: {self.MOCK_CLOUD_TAG}" in captured.out


class TestGetLatestCloudTag:
    """Tests for get_latest_cloud_tag function.

    Uses mocked GitHub API responses for fast, deterministic tests.
    """

    def test_returns_first_matching_cloud_tag(self, mock_github_tags):
        """Test that function returns the first cloud-X.Y.Z formatted tag."""
        mock_github_tags(["latest", "cloud-1.20.0", "cloud-1.19.0"])

        result = get_latest_cloud_tag("fake-token", "All-Hands-AI/OpenHands")

        assert result == "cloud-1.20.0"

    def test_skips_non_cloud_tags(self, mock_github_tags):
        """Test that non-cloud tags are skipped."""
        mock_github_tags(["v1.0.0", "release-2.0", "cloud-1.5.0"])

        result = get_latest_cloud_tag("fake-token", "owner/repo")

        assert result == "cloud-1.5.0"

    def test_returns_none_when_no_cloud_tags(self, mock_github_tags):
        """Test that None is returned when no cloud tags exist."""
        mock_github_tags(["v1.0.0", "latest"])

        result = get_latest_cloud_tag("fake-token", "owner/repo")

        assert result is None

    def test_returns_none_for_invalid_repo(self, mock_github_tags, capsys):
        """Test that None is returned and error is printed for invalid repository."""
        mock_github_tags(repo_error=Exception("Repository not found"))

        result = get_latest_cloud_tag("fake-token", "nonexistent/repo")

        assert result is None
        captured = capsys.readouterr()
        assert "Error fetching tags" in captured.out

    def test_pygithub_logger_is_set_to_warning_or_higher(self):
        """The script raises PyGithub's log level to suppress noisy INFO redirect messages.

        Asserting the suppression mechanism (logger level) is robust across PyGithub
        versions — the previous form asserted on the absence of library log strings,
        which would silently break if PyGithub renamed its messages.
        """
        # Importing update_openhands_charts configures the github logger at module load.
        assert logging.getLogger("github").level >= logging.WARNING


class TestCloudTagExists:
    """Tests for cloud_tag_exists function.

    Uses mocked GitHub API responses for fast, deterministic tests.
    """

    def test_returns_true_when_tag_exists(self, mock_github_ref):
        """Test that function returns True when the tag reference is found."""
        _, mock_repo = mock_github_ref(tag_exists=True)

        result = cloud_tag_exists("fake-token", "All-Hands-AI/OpenHands", "cloud-1.20.0")

        assert result is True
        mock_repo.get_git_ref.assert_called_once_with("tags/cloud-1.20.0")

    def test_returns_false_when_tag_not_found(self, mock_github_ref):
        """Test that function returns False when get_git_ref raises exception."""
        mock_github_ref(tag_exists=False)

        result = cloud_tag_exists("fake-token", "All-Hands-AI/OpenHands", "cloud-99999.0.0")

        assert result is False

    def test_returns_false_for_invalid_repo(self, mock_github_ref):
        """Test that function returns False when repository doesn't exist."""
        mock_github_ref(repo_error=Exception("Repository not found"))

        result = cloud_tag_exists("fake-token", "nonexistent/repo", "cloud-1.0.0")

        assert result is False

    @pytest.mark.parametrize("tag,expected_ref", [
        pytest.param("cloud-1.0.0", "tags/cloud-1.0.0", id="single-digit version"),
        pytest.param("cloud-10.20.30", "tags/cloud-10.20.30", id="multi-digit version"),
    ])
    def test_constructs_correct_ref_format(self, mock_github_ref, tag, expected_ref):
        """Verify ref format is 'tags/<tag>' for various cloud tag versions."""
        _, mock_repo = mock_github_ref(tag_exists=True)

        cloud_tag_exists("fake-token", "owner/repo", tag)

        mock_repo.get_git_ref.assert_called_once_with(expected_ref)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
