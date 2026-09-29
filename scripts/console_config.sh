#!/usr/bin/env bash
# Write web/config.json from the console stack outputs, upload it with
# Cache-Control: no-cache (CloudFront serves /config.json with CachingDisabled),
# then invalidate everything so a fresh deploy is visible immediately.
#
# Usage: scripts/console_config.sh <console-stack> <region> [stt-language] [voice-backend]
set -euo pipefail
STACK="${1:?console stack name}"
REGION="${2:?region}"
STT_LANGUAGE="${3:-en-IN}"
# Which voice backend the browser uses is an operator's choice, not a property of
# the build, so an empty argument means "keep whatever is deployed" rather than
# "use the default". Re-deploying without naming it once silently moved the live
# console off the AssemblyAI Voice Agent API and onto the AWS cascade, which is
# the one thing this console must not do quietly.
VOICE_BACKEND="${4:-}"

out() {
    aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

VOICE_URL="$(out VoiceTurnUrl)"
DASHBOARD_URL="$(out DashboardUrl)"
BUCKET="$(out BucketName)"
DIST_ID="$(out DistributionId)"
CONSOLE_URL="$(out ConsoleUrl)"

if [ -z "$VOICE_BACKEND" ]; then
    LIVE="$(aws s3 cp "s3://${BUCKET}/config.json" - --region "$REGION" 2>/dev/null \
        | sed -n 's/.*"voiceBackend"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
    VOICE_BACKEND="${LIVE:-aws}"
    echo "==> voiceBackend: ${VOICE_BACKEND} (kept from the deployed console)"
else
    echo "==> voiceBackend: ${VOICE_BACKEND} (from VOICE_BACKEND)"
fi

mkdir -p web/dist
cat > web/dist/config.json <<JSON
{
  "voiceUrl": "${VOICE_URL}",
  "dashboardUrl": "${DASHBOARD_URL}",
  "region": "${REGION}",
  "sttLanguage": "${STT_LANGUAGE}",
  "voiceBackend": "${VOICE_BACKEND}",
  "cascadeEnabled": ${CASCADE_ENABLED:-true},
  "archivedIncidentId": "${ARCHIVED_INCIDENT_ID:-}"
}
JSON

aws s3 cp web/dist/config.json "s3://${BUCKET}/config.json" \
    --cache-control "no-cache, no-store, must-revalidate" --content-type application/json --region "$REGION"

if [ "$DIST_ID" = "none" ]; then echo "No CloudFront distribution (S3 website mode); nothing to invalidate."; exit 0; fi
INV_ID="$(aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths '/*' --query 'Invalidation.Id' --output text)"
echo "==> Invalidation $INV_ID created; waiting (1-3 min)..."
aws cloudfront wait invalidation-completed --distribution-id "$DIST_ID" --id "$INV_ID"

echo ""
echo "Console:   ${CONSOLE_URL}"
echo "Voice API: ${VOICE_URL}"
echo "Dashboard: ${DASHBOARD_URL}"
