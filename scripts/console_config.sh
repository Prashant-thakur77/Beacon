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
VOICE_BACKEND="${4:-aws}"

out() {
    aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

VOICE_URL="$(out VoiceTurnUrl)"
DASHBOARD_URL="$(out DashboardUrl)"
BUCKET="$(out BucketName)"
DIST_ID="$(out DistributionId)"
CONSOLE_URL="$(out ConsoleUrl)"

mkdir -p web/dist
cat > web/dist/config.json <<JSON
{
  "voiceUrl": "${VOICE_URL}",
  "dashboardUrl": "${DASHBOARD_URL}",
  "region": "${REGION}",
  "sttLanguage": "${STT_LANGUAGE}",
  "voiceBackend": "${VOICE_BACKEND}",
  "archivedIncidentId": "${ARCHIVED_INCIDENT_ID:-}"
}
JSON

aws s3 cp web/dist/config.json "s3://${BUCKET}/config.json" \
    --cache-control "no-cache, no-store, must-revalidate" --content-type application/json --region "$REGION"

INV_ID="$(aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths '/*' --query 'Invalidation.Id' --output text)"
echo "==> Invalidation $INV_ID created; waiting (1-3 min)..."
aws cloudfront wait invalidation-completed --distribution-id "$DIST_ID" --id "$INV_ID"

echo ""
echo "Console:   ${CONSOLE_URL}"
echo "Voice API: ${VOICE_URL}"
echo "Dashboard: ${DASHBOARD_URL}"
