#!/bin/bash
set -euo pipefail
IFS=$'\n\t'

# Go into docs folder if we're not already there.
cd docs || true

which d2 || (echo "d2 command not found, see: https://github.com/terrastruct/d2" ; exit 1)
export D2_LAYOUT=elk

d2 assets/fig1.d2 assets/fig1.svg

which mmdc || (echo "mmdc command not found, install via: npm install -g @mermaid-js/mermaid-cli" ; exit 1)

mmdc -i assets/github-oauth-flow.mmd -o assets/github-oauth-flow.png
mmdc -i assets/bitbucket-dc-oauth-flow.mmd -o assets/bitbucket-dc-oauth-flow.png
