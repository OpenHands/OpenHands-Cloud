#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["ruamel.yaml", "pytest"]
# ///
"""Unit tests for bump_image_tag.py."""

import sys
from pathlib import Path

import pytest

# Import the script directly, mirroring the sibling update_openhands_charts tests.
sys.path.insert(0, str(Path(__file__).parent))

import bump_image_tag
from bump_image_tag import BumpError, parse_path, run, set_scalar

# A fixture document that exercises the things that make naive editors fail:
# blank lines, comments (standalone + trailing), several `tag:` keys at different
# depths, quoted and unquoted scalars, and a list of mappings.
SAMPLE = """\
# top comment
image:
  repository: ghcr.io/openhands/runtime-api
  tag: sha-old0000
  pullPolicy: Always

kvm:
  image:
    repository: ghcr.io/smarter-project/smarter-device-manager
    tag: v1.20.11
  initImage:
    tag: "1.36"  # trailing comment stays

flag: False

warmRuntimes:
  configs:
    - name: default
      image: "ghcr.io/openhands/agent-server:1.28.0-python"
"""


def _lines(text):
    return text.splitlines()


def test_parse_path_variants():
    assert parse_path(".image.tag") == ["image", "tag"]
    assert parse_path("image.tag") == ["image", "tag"]
    assert parse_path(".warmRuntimes.configs[0].image") == ["warmRuntimes", "configs", 0, "image"]
    assert parse_path("a[10][2].b") == ["a", 10, 2, "b"]


@pytest.mark.parametrize("bad", ["", ".", "   "])
def test_parse_path_rejects_empty(bad):
    with pytest.raises(BumpError):
        parse_path(bad)


def test_changes_only_the_target_line():
    new_text, old = set_scalar(SAMPLE, parse_path(".image.tag"), "sha-new1111")
    assert old == "sha-old0000"

    before, after = _lines(SAMPLE), _lines(new_text)
    assert len(before) == len(after)
    differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert differing == [3]  # only `  tag: sha-old0000`
    assert after[3] == "  tag: sha-new1111"


def test_picks_correct_tag_among_many():
    # The nested kvm tags and the warmRuntimes image must be untouched.
    new_text, _ = set_scalar(SAMPLE, parse_path(".image.tag"), "sha-new1111")
    assert "tag: v1.20.11" in new_text
    assert 'tag: "1.36"  # trailing comment stays' in new_text
    assert 'image: "ghcr.io/openhands/agent-server:1.28.0-python"' in new_text


def test_preserves_blank_lines_and_comments():
    new_text, _ = set_scalar(SAMPLE, parse_path(".image.tag"), "sha-new1111")
    assert new_text.count("\n\n") == SAMPLE.count("\n\n")
    assert "# top comment" in new_text
    assert "# trailing comment stays" in new_text


def test_does_not_normalize_unrelated_scalars():
    # A full ruamel round-trip would rewrite `False` -> `false`; we must not.
    new_text, _ = set_scalar(SAMPLE, parse_path(".image.tag"), "sha-new1111")
    assert "flag: False" in new_text


def test_preserves_double_quotes():
    new_text, old = set_scalar(SAMPLE, parse_path(".kvm.initImage.tag"), "1.40")
    assert old == "1.36"
    assert 'tag: "1.40"  # trailing comment stays' in new_text


def test_updates_nested_tag_keeps_siblings():
    new_text, old = set_scalar(SAMPLE, parse_path(".kvm.image.tag"), "v2.0.0")
    assert old == "v1.20.11"
    assert "tag: v2.0.0" in new_text
    assert "  tag: sha-old0000" in new_text  # top-level image.tag untouched


def test_list_index_with_quotes():
    new_text, old = set_scalar(
        SAMPLE, parse_path(".warmRuntimes.configs[0].image"), "ghcr.io/x/y:9.9.9"
    )
    assert old == "ghcr.io/openhands/agent-server:1.28.0-python"
    assert 'image: "ghcr.io/x/y:9.9.9"' in new_text


def test_idempotent_returns_none_and_unchanged_text():
    new_text, old = set_scalar(SAMPLE, parse_path(".image.tag"), "sha-old0000")
    assert old is None
    assert new_text == SAMPLE


def test_missing_path_raises():
    with pytest.raises(BumpError, match="Path not found"):
        set_scalar(SAMPLE, parse_path(".image.nope"), "x")


def test_descend_into_scalar_raises():
    with pytest.raises(BumpError, match="not navigable"):
        set_scalar(SAMPLE, parse_path(".image.tag.deeper"), "x")


def test_target_is_mapping_raises():
    with pytest.raises(BumpError, match="not a scalar"):
        set_scalar(SAMPLE, parse_path(".image"), "x")


def test_numeric_tag_that_would_coerce_raises():
    # Original is unquoted; writing a bare number would change the YAML type.
    # The post-edit verification must catch it rather than write bad YAML.
    with pytest.raises(BumpError, match="verification failed|numeric"):
        set_scalar(SAMPLE, parse_path(".image.tag"), "123")


def test_run_writes_file_and_is_idempotent(tmp_path):
    f = tmp_path / "values.yaml"
    f.write_text(SAMPLE)

    assert run(f, ".image.tag", "sha-new1111", dry_run=False) is True
    assert "tag: sha-new1111" in f.read_text()

    # Second run with the same value is a no-op.
    assert run(f, ".image.tag", "sha-new1111", dry_run=False) is False


def test_run_dry_run_does_not_write(tmp_path):
    f = tmp_path / "values.yaml"
    f.write_text(SAMPLE)
    assert run(f, ".image.tag", "sha-new1111", dry_run=True) is True
    assert f.read_text() == SAMPLE


def test_run_missing_file_raises(tmp_path):
    with pytest.raises(BumpError, match="File not found"):
        run(tmp_path / "nope.yaml", ".image.tag", "x", dry_run=False)


def test_main_returns_nonzero_on_error(tmp_path, capsys):
    f = tmp_path / "values.yaml"
    f.write_text(SAMPLE)
    rc = bump_image_tag.main(["--file", str(f), "--path", ".image.nope", "--tag", "x"])
    assert rc == 1
    assert "Error:" in capsys.readouterr().err


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
