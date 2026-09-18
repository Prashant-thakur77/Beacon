#!/usr/bin/env bash
# Print the sg.restore_ingress params for the demo stack as JSON, read from the
# beacon-demo-infra CloudFormation outputs. Used by `make dry-run`, `make propose`.
#
# Usage: scripts/demo_params.sh [region] [demo-stack-name]
set -euo pipefail
REGION="${1:-${REGION:-us-east-1}}"
STACK="${2:-beacon-demo-infra}"

out() {
    aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

RDS_SG="$(out RdsSecurityGroupId)"
ECS_SG="$(out EcsSecurityGroupId)"
if [ -z "$RDS_SG" ] || [ "$RDS_SG" = "None" ] || [ -z "$ECS_SG" ] || [ "$ECS_SG" = "None" ]; then
    echo "ERROR: could not read RdsSecurityGroupId/EcsSecurityGroupId from stack $STACK in $REGION" >&2
    exit 1
fi

printf '{"group_id":"%s","ip_protocol":"tcp","from_port":5432,"to_port":5432,"source_group_id":"%s"}\n' "$RDS_SG" "$ECS_SG"
