#!/usr/bin/env bash
# Prints the GitHub Release notes for promoting an openhands release candidate
# to stable: the notes of every rc since the previous stable release, newest
# first, under one header for the stable version.
#
# Usage: scripts/promotion_notes.sh openhands/0.94.0-rc.3
# Needs full git history with tags, and `gh` authenticated for the repo
# (set GH_REPO to target a repo other than the current one).
set -euo pipefail

rc_tag=${1:?usage: $0 openhands/X.Y.Z-rc.N}
if [[ ! "$rc_tag" =~ ^openhands/([0-9]+\.[0-9]+\.[0-9]+)-rc\.[0-9]+$ ]]; then
  echo "not an openhands rc tag: ${rc_tag}" >&2
  exit 1
fi
version="${BASH_REMATCH[1]}"
stable_tag="openhands/${version}"
repo_url="https://github.com/${GH_REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"

# The previous stable release is the newest stable tag in the rc's history.
# Stable tags carry no prerelease suffix, so they contain no "-".
prev_stable=$(git tag --merged "$rc_tag" --list 'openhands/*' --sort=-v:refname \
  | grep -v -- '-' | sed -n 1p || true)

# Every rc in the rc's history that the previous stable does not already have.
rc_list_args=(--merged "$rc_tag" --list 'openhands/*-rc.*' --sort=-v:refname)
[ -n "$prev_stable" ] && rc_list_args+=(--no-merged "$prev_stable")
rcs=()
while IFS= read -r tag; do rcs+=("$tag"); done < <(git tag "${rc_list_args[@]}")
if [ "${#rcs[@]}" -eq 0 ]; then
  echo "no release candidates found for ${rc_tag}" >&2
  exit 1
fi

echo "## ${version} ($(date -u +%Y-%m-%d))"
echo ""
echo "Promoted from \`${rc_tag}\`. Changes since ${prev_stable:-the first release}, by release candidate:"
for tag in "${rcs[@]}"; do
  echo ""
  echo "### ${tag#openhands/}"
  echo ""
  # Each rc body is release-please's github-scoped notes: a "## <version>"
  # header, "## What's Changed", "### <section>" lists, and a Full Changelog
  # link. Keep the lists and nest their sections under this rc's heading.
  gh release view "$tag" --json body --jq .body \
    | grep -vE '^## |^\*\*Full Changelog\*\*' \
    | sed -E 's/^### /#### /' \
    | sed -e '/./,$!d' -e :a -e '/^\n*$/{$d;N;ba' -e '}'
done
if [ -n "$prev_stable" ]; then
  echo ""
  echo "**Full Changelog**: ${repo_url}/compare/${prev_stable}...${stable_tag}"
fi
