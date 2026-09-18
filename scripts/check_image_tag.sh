#!/usr/bin/env bash
# Refuse to deploy a container image whose tag is ':latest' or does not match
# the current git HEAD. Lambda does not redeploy when the image URI string is
# unchanged, so a ':latest' tag silently ships stale code. Override with
# ALLOW_STALE_IMAGE=1 (prints a warning).
#
# Usage: scripts/check_image_tag.sh <VAR_NAME> <image-uri>
set -euo pipefail

name="${1:-IMAGE_URI}"
uri="${2:-}"
red=$'\033[31m'; yellow=$'\033[33m'; reset=$'\033[0m'

if [ -z "$uri" ]; then
    echo "${red}ERROR: $name is empty. Run 'make setup-image' (or 'make setup-agent-image') first; it writes $name into .beacon.env.${reset}" >&2
    exit 1
fi

tag="${uri##*:}"
head_sha="$(bash "$(dirname "$0")/image_tag.sh")"

if [ "$tag" = "latest" ] || [ "$tag" = "$uri" ]; then
    if [ "${ALLOW_STALE_IMAGE:-0}" = "1" ]; then
        echo "${yellow}WARNING: $name uses ':latest' ($uri). Lambda will NOT pick up code changes. Continuing because ALLOW_STALE_IMAGE=1.${reset}" >&2
        exit 0
    fi
    echo "${red}ERROR: $name must carry a git-SHA tag, not ':latest' ($uri). Run 'make setup-image' / 'make setup-agent-image' to build and push a tagged image, or set ALLOW_STALE_IMAGE=1 to override.${reset}" >&2
    exit 1
fi

if [ "$tag" != "$head_sha" ]; then
    if [ "${ALLOW_STALE_IMAGE:-0}" = "1" ]; then
        echo "${yellow}WARNING: $name tag '$tag' != source tag '$head_sha'. The deployed code will not match the tree. Continuing because ALLOW_STALE_IMAGE=1.${reset}" >&2
        exit 0
    fi
    echo "${red}ERROR: $name tag '$tag' does not match the current source tag '$head_sha' (last commit touching src/beacon, Dockerfiles or pyproject; '-dirty' = uncommitted changes there). Rebuild with 'make setup-image' / 'make setup-agent-image' (only the code layer is pushed, a few minutes), or set ALLOW_STALE_IMAGE=1 to deploy anyway.${reset}" >&2
    exit 1
fi

echo "image tag ok: $name=$uri"
