#!/usr/bin/env bash
# The image tag: short SHA of the last commit that touched the image inputs
# (source, Dockerfiles, pyproject), plus "-dirty" if any of them has uncommitted
# changes. Docs-only commits therefore do NOT invalidate a built image.
set -euo pipefail
cd "$(dirname "$0")/.."
INPUTS=(src/beacon Dockerfile Dockerfile.agent pyproject.toml requirements)
sha="$(git log -1 --format=%h -- "${INPUTS[@]}" 2>/dev/null || true)"
if [ -z "$sha" ]; then date +%s; exit 0; fi
if [ -n "$(git status --porcelain -- "${INPUTS[@]}" 2>/dev/null)" ]; then sha="${sha}-dirty"; fi
echo "$sha"
