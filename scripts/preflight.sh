#!/usr/bin/env bash
# Everything that must be true before recording, in one green/red table.
# Usage: scripts/preflight.sh <stack> <region>   (make preflight)
set -uo pipefail
STACK="${1:-beacon}"; REGION="${2:-us-east-1}"
PY="${PYTHON:-.venv/bin/python}"
cd "$(dirname "$0")/.."
[ -f .beacon.env ] && set -a && . ./.beacon.env && set +a

G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'
fails=0
ok()   { printf "  %s✓%s %-38s %s\n" "$G" "$N" "$1" "$2"; }
bad()  { printf "  %s✗%s %-38s %s\n" "$R" "$N" "$1" "$2"; fails=$((fails+1)); }
warn() { printf "  %s!%s %-38s %s\n" "$Y" "$N" "$1" "$2"; }

out() { aws cloudformation describe-stacks --stack-name "$1" --region "$REGION" --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text 2>/dev/null; }
stack_status() { aws cloudformation describe-stacks --stack-name "$1" --region "$REGION" --query 'Stacks[0].StackStatus' --output text 2>/dev/null; }

echo "Beacon preflight — stack $STACK, region $REGION, $(date)"

# --- account
if ACCT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null); then ok "AWS credentials" "account ${ACCT:0:4}********"; else bad "AWS credentials" "aws sts get-caller-identity failed"; fi

# --- Bedrock model access (invoke a tiny prompt; the only reliable check)
if aws bedrock-runtime converse --model-id us.amazon.nova-2-lite-v1:0 --region "$REGION" \
     --messages '[{"role":"user","content":[{"text":"ping"}]}]' --inference-config '{"maxTokens":5}' >/dev/null 2>&1; then
  ok "Bedrock: Nova 2 Lite" "converse OK"
else
  bad "Bedrock: Nova 2 Lite" "converse failed — model access not granted in $REGION?"
fi

# --- CloudTrail
TRAIL=$(aws cloudtrail describe-trails --region "$REGION" --query 'trailList[0].Name' --output text 2>/dev/null)
if [ -n "$TRAIL" ] && [ "$TRAIL" != "None" ] && [ "$(aws cloudtrail get-trail-status --name "$TRAIL" --region "$REGION" --query IsLogging --output text 2>/dev/null)" = "True" ]; then
  ok "CloudTrail trail logging" "$TRAIL"
else
  bad "CloudTrail trail logging" "no logging trail in $REGION (change ledger will be empty)"
fi

# --- stacks
for s in beacon-demo-infra "$STACK" "$STACK-remediation" "$STACK-console"; do
  st=$(stack_status "$s")
  case "$st" in
    CREATE_COMPLETE|UPDATE_COMPLETE) ok "stack $s" "$st" ;;
    *) bad "stack $s" "${st:-missing}" ;;
  esac
done

# --- image tags
SRC_TAG=$(bash scripts/image_tag.sh)
for var in IMAGE_URI AGENT_IMAGE_URI; do
  uri="${!var:-}"
  if [ -z "$uri" ]; then bad "$var" "not set (.beacon.env)"; continue; fi
  if [ "${uri##*:}" = "$SRC_TAG" ]; then ok "$var tag current" "$SRC_TAG"; else warn "$var tag" "${uri##*:} != source $SRC_TAG (rebuild, or ALLOW_STALE_IMAGE=1)"; fi
done

# --- deployed functions actually use those images
for fn in "beacon-$STACK:IMAGE_URI" "beacon-voice-turn-$STACK:AGENT_IMAGE_URI" "beacon-remediate-$STACK:AGENT_IMAGE_URI"; do
  name="${fn%%:*}"; var="${fn##*:}"
  deployed=$(aws lambda get-function --function-name "$name" --region "$REGION" --query 'Code.ImageUri' --output text 2>/dev/null)
  if [ -z "$deployed" ] || [ "$deployed" = "None" ]; then bad "$name deployed image" "function missing";
  elif [ "$deployed" = "${!var:-}" ]; then ok "$name deployed image" "${deployed##*:}";
  else warn "$name deployed image" "${deployed##*:} (deploy again to ship ${!var:-?})"; fi
done

# --- kill switch
for fn in "beacon-$STACK" "beacon-voice-turn-$STACK" "beacon-remediate-$STACK"; do
  v=$(aws lambda get-function-configuration --function-name "$fn" --region "$REGION" --query 'Environment.Variables.APPLY_ENABLED' --output text 2>/dev/null)
  if [ "$v" = "true" ]; then ok "APPLY_ENABLED on $fn" "true"; else bad "APPLY_ENABLED on $fn" "${v:-unset} (make apply-on)"; fi
done

# --- golden snapshot + dry run
if aws ssm get-parameter --name "/beacon/$STACK/golden-sg" --region "$REGION" >/dev/null 2>&1; then ok "golden snapshot" "/beacon/$STACK/golden-sg"; else bad "golden snapshot" "missing (make snapshot-sg on a healthy stack)"; fi
if PARAMS=$(REGION="$REGION" bash scripts/demo_params.sh 2>/dev/null); then
  PAYLOAD="{\"step\":\"dryrun\",\"action\":\"sg.restore_ingress\",\"params\":$PARAMS}"
  DR=$(aws lambda invoke --function-name "beacon-remediate-$STACK" --region "$REGION" --cli-binary-format raw-in-base64-out --payload "$PAYLOAD" /dev/stdout 2>/dev/null | head -1)
  if echo "$DR" | "$PY" -c 'import json,sys; d=json.loads(sys.stdin.read()); sys.exit(0 if d.get("ok") else 1)' 2>/dev/null; then
    ok "dry run under remediator role" "$(echo "$DR" | "$PY" -c 'import json,sys; print(json.loads(sys.stdin.read()).get("code"))')"
  else
    bad "dry run under remediator role" "$(echo "$DR" | cut -c1-90)"
  fi
else
  bad "demo params" "could not read the demo stack outputs"
fi

# --- alarm state
AL=$(out beacon-demo-infra DemoAlarmName)
ST=$(aws cloudwatch describe-alarms --alarm-names "$AL" --region "$REGION" --query 'MetricAlarms[0].StateValue' --output text 2>/dev/null)
if [ "$ST" = "OK" ]; then ok "alarm $AL" "OK (ready to break)"; else warn "alarm $AL" "${ST:-missing} (make demo-reset before the take)"; fi

# --- URLs
CONSOLE=$(out "$STACK-console" ConsoleUrl); VOICE=$(out "$STACK-console" VoiceTurnUrl); DASH=$(out "$STACK-console" DashboardUrl)
for pair in "console:$CONSOLE/" "voice:${VOICE}health" "dashboard:${DASH}health"; do
  label="${pair%%:*}"; url="${pair#*:}"
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$url" 2>/dev/null)
  if [ "$code" = "200" ]; then ok "$label URL" "$url"; else bad "$label URL" "HTTP ${code:-000} $url"; fi
done
CFG=$(curl -s --max-time 15 "$CONSOLE/config.json" 2>/dev/null)
if echo "$CFG" | grep -q "$DASH"; then ok "console config.json" "points at the dashboard URL"; else bad "console config.json" "stale or missing (make console-config)"; fi

# --- warm
for fn in "beacon-voice-turn-$STACK" "beacon-remediate-$STACK" "beacon-dashboard-$STACK"; do
  aws lambda invoke --function-name "$fn" --region "$REGION" --cli-binary-format raw-in-base64-out --payload '{"mode":"warm"}' /dev/null >/dev/null 2>&1 && ok "warm $fn" "" || warn "warm $fn" "invoke failed"
done

echo
if [ "$fails" -eq 0 ]; then echo "${G}PREFLIGHT GREEN — record.${N}"; else echo "${R}PREFLIGHT: $fails blocker(s) above.${N}"; exit 1; fi
