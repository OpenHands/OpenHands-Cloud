#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["ruamel.yaml"]
# ///
"""Set a single scalar (an image tag) in a YAML file, changing only that line.

This is the edit half of the ``bump-image-tag`` reusable workflow. Component
repos (runtime-api, automation, ...) call the workflow when they cut a release;
the workflow runs this script to point a chart's ``image.tag`` at the freshly
built image, then opens a PR.

Why not ``yq -i`` or a ruamel round-trip dump?

  * ``yq -i`` (mikefarah v4) strips every blank line from values.yaml, producing
    an enormous, unreviewable diff.
  * A full ruamel ``load`` -> ``dump`` round-trip normalises unrelated scalars
    (e.g. ``dryRun: False`` -> ``dryRun: false``), touching lines we never meant
    to change.
  * A blanket ``sed 's/tag: .*/.../'`` is path-blind: runtime-api/values.yaml has
    three ``tag:`` keys (image, kvm.image, kvm.initImage) and only one should move.

So we use ruamel purely to *locate* the target scalar (its line/column and current
value), then splice exactly that one value in the raw text. Indentation, quoting
style, trailing comments, blank lines, and every other byte are preserved, and the
PR diff is a single line. After writing, we re-parse and assert the path now holds
the new value, so a bad splice fails loudly instead of committing broken YAML.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from ruamel.yaml import YAML

# A path segment is either a bare mapping key (foo) or a list index ([0]).
_SEGMENT_RE = re.compile(r"\[(\d+)\]|([^.\[\]]+)")


class BumpError(Exception):
    """A user-facing failure: bad path, missing key, unsupported scalar, etc."""


def parse_path(path: str) -> list[str | int]:
    """Turn a yq-style path into a list of mapping keys and list indices.

    Accepts a leading dot (yq style) or not: ".image.tag" and "image.tag" are
    equivalent. List indices use bracket notation, e.g. "warmRuntimes.configs[0].image".
    """
    raw = path.strip()
    if raw.startswith("."):
        raw = raw[1:]
    if not raw:
        raise BumpError("Path is empty")

    segments: list[str | int] = []
    pos = 0
    while pos < len(raw):
        if raw[pos] == ".":
            pos += 1
            continue
        match = _SEGMENT_RE.match(raw, pos)
        if not match or match.start() != pos:
            raise BumpError(f"Could not parse path near {raw[pos:]!r} in {path!r}")
        index, key = match.groups()
        segments.append(int(index) if index is not None else key)
        pos = match.end()

    if not segments:
        raise BumpError(f"Path {path!r} resolved to no segments")
    return segments


def locate(data, segments: list[str | int]):
    """Walk ``segments`` and return (parent_container, final_key, current_value)."""
    parent = None
    key: str | int | None = None
    current = data
    traversed: list[str | int] = []

    for segment in segments:
        parent = current
        key = segment
        try:
            current = parent[segment]
        except (KeyError, IndexError):
            shown = _render_path(traversed)
            raise BumpError(
                f"Path not found: no {segment!r} under {shown or '<root>'}"
            ) from None
        except TypeError:
            shown = _render_path(traversed)
            raise BumpError(
                f"Path not navigable: {shown or '<root>'} is a scalar, "
                f"cannot descend into {segment!r}"
            ) from None
        traversed.append(segment)

    return parent, key, current


def _render_path(segments: list[str | int]) -> str:
    out = ""
    for segment in segments:
        out += f"[{segment}]" if isinstance(segment, int) else f".{segment}"
    return out


def _value_position(parent, key: str | int) -> tuple[int, int]:
    """Return the (line, column) where the value for ``key`` begins.

    ruamel stores this in the container's ``.lc.data`` line-column map: mapping
    entries as [key_line, key_col, value_line, value_col]; sequence items as
    [item_line, item_col].
    """
    lc = getattr(parent, "lc", None)
    if lc is None or getattr(lc, "data", None) is None or key not in lc.data:
        raise BumpError(
            "Could not determine source position for the target value "
            "(the YAML structure may be more complex than this tool supports)"
        )
    info = lc.data[key]
    if isinstance(key, int):
        return info[0], info[1]
    return info[2], info[3]


def set_scalar(text: str, segments: list[str | int], new_value: str) -> tuple[str, str | None]:
    """Return (new_text, old_value). ``old_value`` is None when already current.

    Only the single line holding the target scalar is rewritten; every other byte
    of ``text`` is preserved exactly.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(text)
    if data is None:
        raise BumpError("File is empty or not a YAML document")

    parent, key, current = locate(data, segments)

    if isinstance(current, (dict, list)):
        raise BumpError(
            f"Target {_render_path(segments)} is a "
            f"{'mapping' if isinstance(current, dict) else 'sequence'}, not a scalar"
        )

    old_value = "" if current is None else str(current)
    if old_value == new_value:
        return text, None

    line_no, col = _value_position(parent, key)
    lines = text.splitlines(keepends=True)
    if line_no >= len(lines):
        raise BumpError("Computed line number is out of range for the file")
    line = lines[line_no]

    # Figure out the exact span of the existing scalar on this line.
    quote = line[col] if col < len(line) else ""
    if quote in ('"', "'"):
        old_token = f"{quote}{old_value}{quote}"
        new_token = f"{quote}{new_value}{quote}"
    elif quote in ("|", ">"):
        raise BumpError("Target uses a block scalar (| or >), which is unsupported")
    else:
        old_token = old_value
        new_token = new_value

    if not line[col:].startswith(old_token):
        raise BumpError(
            "Internal consistency check failed: the value at the computed "
            f"position is not {old_token!r} (line was: {line.rstrip(chr(10))!r})"
        )

    lines[line_no] = line[:col] + new_token + line[col + len(old_token):]
    new_text = "".join(lines)

    # Re-parse and confirm the path now holds exactly the new string. This catches
    # a mis-splice, and also a tag that YAML would coerce to a non-string (e.g. a
    # purely numeric tag written unquoted) instead of silently writing bad YAML.
    verify = YAML()
    verify.preserve_quotes = True
    _, _, round_tripped = locate(verify.load(new_text), segments)
    if not isinstance(round_tripped, str) or round_tripped != new_value:
        raise BumpError(
            f"Post-edit verification failed: path now holds {round_tripped!r} "
            f"(type {type(round_tripped).__name__}), expected the string {new_value!r}. "
            "If the tag is purely numeric it may need to be quoted in the values file."
        )

    return new_text, old_value


def run(file: Path, path: str, tag: str, dry_run: bool) -> bool:
    """Apply the edit. Returns True when a change was written (or would be)."""
    if not file.is_file():
        raise BumpError(f"File not found: {file}")

    segments = parse_path(path)
    original = file.read_text()
    new_text, old_value = set_scalar(original, segments, tag)

    pretty_path = _render_path(segments)
    if old_value is None:
        print(f"{file}:{pretty_path} already {tag!r}; nothing to do.")
        return False

    if dry_run:
        print(f"[dry-run] {file}:{pretty_path} {old_value!r} -> {tag!r}")
        return True

    file.write_text(new_text)
    print(f"Updated {file}:{pretty_path} {old_value!r} -> {tag!r}")
    return True


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", required=True, type=Path,
                        help="Path to the YAML file to edit (e.g. charts/runtime-api/values.yaml)")
    parser.add_argument("--path", required=True,
                        help="yq-style path to the scalar (e.g. .image.tag)")
    parser.add_argument("--tag", required=True,
                        help="New tag value to set")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        run(args.file, args.path, args.tag, args.dry_run)
    except BumpError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
