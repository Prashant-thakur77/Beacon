#!/usr/bin/env bash
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
INFRA_STACK="beacon-demo-infra"

# ---------------------------------------------------------------------------
# break / fix — real infrastructure demo (ECS + RDS network partition)
# ---------------------------------------------------------------------------

if [ "${1:-}" = "break" ] || [ "${1:-}" = "fix" ]; then
    RDS_SG=$(aws cloudformation describe-stacks \
        --stack-name "$INFRA_STACK" --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`RdsSecurityGroupId`].OutputValue' \
        --output text)
    ECS_SG=$(aws cloudformation describe-stacks \
        --stack-name "$INFRA_STACK" --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`EcsSecurityGroupId`].OutputValue' \
        --output text)

    if [ "$1" = "break" ]; then
        echo "=== Revoking RDS security group ingress (network partition) ==="
        aws ec2 revoke-security-group-ingress \
            --group-id "$RDS_SG" \
            --protocol tcp --port 5432 \
            --source-group "$ECS_SG" \
            --region "$REGION" 2>/dev/null && \
            echo "Done. ECS can no longer reach the database." || \
            echo "Rule already revoked (partition already active)."
        echo "Watch logs: aws logs tail /ecs/beacon-demo --follow --region $REGION"
    else
        echo "=== Restoring RDS security group ingress ==="
        aws ec2 authorize-security-group-ingress \
            --group-id "$RDS_SG" \
            --protocol tcp --port 5432 \
            --source-group "$ECS_SG" \
            --region "$REGION" 2>/dev/null && \
            echo "Done. Database connectivity restored." || \
            echo "Rule already present (connectivity already restored)."
    fi
    exit 0
fi

echo "Usage: $0 break|fix"
echo "  break  Revoke RDS security group ingress to simulate a network partition"
echo "  fix    Restore RDS security group ingress"
exit 1
