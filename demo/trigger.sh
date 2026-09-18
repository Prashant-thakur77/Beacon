#!/usr/bin/env bash
set -euo pipefail

REGION="${REGION:-${AWS_REGION:-us-east-1}}"
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

if [ "${1:-}" = "wedge" ]; then
    echo "=== Wedging the running demo task (sticky; only a new task recovers) ==="
    aws ssm put-parameter --name /beacon/demo/wedge --type String --value true --overwrite --region "$REGION" > /dev/null
    echo "Flag set. The task will notice within ~5 s and start failing; clearing the flag in 15 s so a redeploy starts clean..."
    sleep 15
    aws ssm put-parameter --name /beacon/demo/wedge --type String --value false --overwrite --region "$REGION" > /dev/null
    echo "Flag cleared. The current task stays wedged. Only ecs.force_redeploy (or make fix-demo-deploy) recovers it."
    exit 0
fi

if [ "${1:-}" = "unwedge" ]; then
    CLUSTER=$(aws cloudformation describe-stacks --stack-name "$INFRA_STACK" --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`DemoEcsCluster`].OutputValue' --output text)
    SERVICE=$(aws cloudformation describe-stacks --stack-name "$INFRA_STACK" --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`DemoServiceName`].OutputValue' --output text)
    aws ssm put-parameter --name /beacon/demo/wedge --type String --value false --overwrite --region "$REGION" > /dev/null
    aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --force-new-deployment --region "$REGION" > /dev/null
    echo "Forced a new deployment of $CLUSTER/$SERVICE (manual fix)."
    exit 0
fi

echo "Usage: $0 break|fix|wedge|unwedge"
echo "  break  Revoke RDS security group ingress to simulate a network partition"
echo "  fix    Restore RDS security group ingress"
echo "  wedge  Wedge the running task (a restart-only failure; the ecs.force_redeploy demo)"
echo "  unwedge  Force a new deployment by hand"
exit 1
