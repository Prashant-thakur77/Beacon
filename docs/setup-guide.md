# Beacon Setup Guide

This guide walks through deploying Beacon and configuring triggers for common AWS monitoring scenarios.

## Prerequisites

- An AWS account with [Amazon Bedrock access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html) enabled for Amazon Nova models in your region
- AWS CLI installed and configured (`aws configure`)
- Docker or Podman (for `make setup-image`)
- The CloudWatch Log Groups you want to monitor must already exist (Beacon reads from them, it does not create them)

## Step 1: Copy the Image to ECR

Lambda requires container images to be stored in ECR. Pull the public image from GHCR and push it to your account:

```bash
make setup-image REGION=us-east-1
```

This creates an ECR repository named `beacon`, pulls the image from GHCR, and pushes it to your account. It prints the `IMAGE_URI` to use in subsequent deploy commands.

## Step 2: Deploy Beacon

Deploy with at least one trigger enabled. This example enables the alarm trigger for alarms prefixed with `prod-`:

```bash
make deploy \
  IMAGE_URI=<your-ecr-uri> \
  EMAIL=you@example.com \
  LOG_GROUP_PATTERNS="/aws/lambda/*" \
  ENABLE_ALARM=true \
  ALARM_NAME_PREFIX=prod-
```

Or directly with CloudFormation:

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name beacon \
  --region us-east-1 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    ImageUri=<your-ecr-uri> \
    LogGroupPatterns="/aws/lambda/*" \
    NotificationEmail=you@example.com \
    EnableAlarmTrigger=true \
    AlarmNamePrefix=prod-
```

After deploying, check your email and **confirm the SNS subscription**. You will not receive any notifications until you do.

**Important:** No triggers are enabled by default. You must explicitly enable at least one, otherwise Beacon deploys but never runs. This is intentional so you control exactly when Beacon analyzes your logs.

## Step 3: Choose Your Triggers

Beacon supports three trigger modes. You can enable any combination.

### CloudWatch Alarm Trigger

Beacon analyzes logs when specific CloudWatch Alarms fire. Use `AlarmNamePrefix` to scope which alarms Beacon reacts to.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `EnableAlarmTrigger` | `false` | Turn alarm-driven analysis on/off |
| `AlarmNamePrefix` | `""` | Only react to alarms whose name starts with this prefix |

**Example: react to production alarms only:**

```bash
make deploy \
  IMAGE_URI=<your-ecr-uri> \
  EMAIL=you@example.com \
  LOG_GROUP_PATTERNS="/aws/lambda/*,/aws/ecs/*" \
  ENABLE_ALARM=true \
  ALARM_NAME_PREFIX=prod-
```

When a matching alarm transitions to ALARM state, Beacon pulls logs from all configured log groups and analyzes them. The notification includes the alarm name and details.

**How it works:** Beacon does not create alarms. You create them separately through CloudWatch for whatever metrics matter to you (CPU utilization, error count, latency, etc.). Name them with a consistent prefix (e.g., `prod-cpu-high`, `prod-error-rate`) so Beacon can filter to just the alarms you care about.

If `AlarmNamePrefix` is empty, Beacon reacts to *all* alarms in your account, which is usually too broad.

### Scheduled Scans

Beacon runs on a timer and scans all log groups matching your patterns. Useful for routine monitoring.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `EnableSchedule` | `false` | Turn scheduled scans on/off |
| `ScheduleExpression` | `rate(1 hour)` | How often to scan |
| `LookbackMinutes` | `30` | How far back to pull logs per scan |

**Example: scan every 30 minutes, looking back 15 minutes each time:**

```bash
make deploy \
  IMAGE_URI=<your-ecr-uri> \
  EMAIL=you@example.com \
  LOG_GROUP_PATTERNS="/aws/lambda/*" \
  ENABLE_SCHEDULE=true \
  SCHEDULE_EXPRESSION="rate(30 minutes)" \
  LOOKBACK_MINUTES=15
```

Schedule expressions follow [EventBridge syntax](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-schedule-expressions.html):
- `rate(1 hour)` every hour
- `rate(30 minutes)` every 30 minutes
- `cron(0 9 * * ? *)` daily at 9 AM UTC

**Note:** On scheduled scans, if Beacon determines the logs are healthy, it skips the notification. You only get emailed when something is worth investigating.

### Subscription Filter Trigger (Real-Time)

Beacon can react immediately when specific patterns appear in a log group. CloudWatch Logs streams matching events directly to Beacon with no delay.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `EnableSubscription` | `false` | Turn subscription filter on/off |
| `SubscriptionLogGroup` | first in `LogGroupPatterns` | Which log group to attach the filter to |
| `SubscriptionFilterPattern` | `?ERROR ?FATAL ?CRITICAL` | What log patterns to match |

**Example: trigger immediately when ERROR or FATAL appears in your app's logs:**

```bash
make deploy \
  IMAGE_URI=<your-ecr-uri> \
  EMAIL=you@example.com \
  LOG_GROUP_PATTERNS="/aws/lambda/*" \
  ENABLE_SUBSCRIPTION=true \
  SUBSCRIPTION_LOG_GROUP=/aws/lambda/my-critical-app \
  SUBSCRIPTION_FILTER="?ERROR ?FATAL ?CRITICAL"
```

Filter patterns follow [CloudWatch Logs filter syntax](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/FilterAndPatternSyntax.html):
- `?ERROR ?FATAL` matches lines containing ERROR *or* FATAL
- `"OutOfMemoryError"` matches the exact string
- `{ $.level = "ERROR" }` JSON pattern matching (for structured logs)
- `""` (empty string) matches everything (not recommended, very noisy)

**Limitation:** CloudFormation supports one subscription filter per stack. If you need filters on multiple log groups, deploy multiple stacks with different names:

```bash
# Stack for API logs
aws cloudformation deploy --stack-name beacon-api \
  --template-file template.yaml --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    ImageUri=<your-ecr-uri> \
    LogGroupPatterns="/aws/lambda/api-*" \
    NotificationEmail=api-team@example.com \
    EnableSubscription=true \
    SubscriptionLogGroup=/aws/lambda/api-gateway

# Stack for worker logs
aws cloudformation deploy --stack-name beacon-workers \
  --template-file template.yaml --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    ImageUri=<your-ecr-uri> \
    LogGroupPatterns="/aws/ecs/workers/*" \
    NotificationEmail=platform-team@example.com \
    EnableSubscription=true \
    SubscriptionLogGroup=/aws/ecs/workers/processor
```

## Step 4: Configure Log Group Patterns

The `LogGroupPatterns` parameter accepts exact names and prefix patterns (trailing `*`):

| Pattern | What it matches |
|---------|----------------|
| `/aws/lambda/*` | All Lambda function log groups |
| `/aws/ecs/my-cluster/*` | All ECS tasks in a specific cluster |
| `/aws/rds/*` | All RDS instance log groups |
| `/my-app/api` | One specific log group (exact match) |
| `/aws/lambda/*,/aws/ecs/*` | All Lambda and ECS log groups |

Patterns are resolved at invocation time, so new log groups that match are automatically picked up without redeploying.

**Common AWS log group naming conventions:**

| Service | Log group pattern |
|---------|-------------------|
| Lambda | `/aws/lambda/<function-name>` |
| ECS | `/aws/ecs/<cluster-name>/<service-name>` |
| RDS | `/aws/rds/instance/<instance-id>/<log-type>` |
| API Gateway | `/aws/apigateway/<api-id>/<stage>` |
| CloudTrail | `aws-cloudtrail-logs-<account-id>-<id>` |
| VPC Flow Logs | `/aws/vpc/flowlogs` |
| AppRunner | `/aws/apprunner/<service-name>/<service-id>` |

## Step 5: Notifications

Beacon publishes analysis results to an SNS topic. By default it creates an email subscription, but SNS supports many delivery methods.

### Email (default)

Set `NotificationEmail` in your deploy command. Confirm the subscription via the email you receive.

### Slack

After deploying, add an SNS subscription for your Slack webhook:

```bash
TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name beacon \
  --query 'Stacks[0].Outputs[?OutputKey==`BeaconSNSTopicArn`].OutputValue' \
  --output text)

aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol https \
  --notification-endpoint https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK
```

### PagerDuty

Use PagerDuty's [SNS integration](https://support.pagerduty.com/main/docs/aws-cloudwatch-integration-guide):

```bash
aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol https \
  --notification-endpoint https://events.pagerduty.com/integration/YOUR_KEY/enqueue
```

### Multiple Channels

You can add as many subscriptions to the SNS topic as you want. For example, email for the on-call engineer and Slack for the team channel.

## Tuning

### Token Budget

By default, Beacon uses the full model context window. If you want to reduce Bedrock costs, set a token budget:

```bash
--parameter-overrides TokenBudget=100000  # 100K tokens max
```

When logs exceed the budget, Cordon automatically reduces them to the most anomalous sections. When they fit, Cordon is skipped entirely (faster and cheaper).

### Cordon Parameters

| Parameter | Default | When to change |
|-----------|---------|----------------|
| `CordonWindowSize` | `4` | Increase to 8-10 for verbose logs with long stack traces |
| `CordonKNeighbors` | `5` | Increase for very large log files (10K+ lines) for better anomaly detection |

### Healthy Scan Suppression

On scheduled scans, if Beacon determines the logs are healthy, it skips the notification. You only get emailed when something is worth investigating. Alarm and subscription triggers always notify, since those fired for a reason.

## Updating

To change configuration, re-run the deploy command with new parameters. CloudFormation updates the stack in place:

```bash
make deploy \
  IMAGE_URI=<your-ecr-uri> \
  EMAIL=you@example.com \
  LOG_GROUP_PATTERNS="/aws/lambda/*,/aws/ecs/*" \
  ENABLE_ALARM=true
```

## Step 6: Voice Pipeline (Optional)

Beacon can call the on-call engineer by phone when an incident is detected, deliver the RCA briefing via Nova 2 Sonic speech-to-speech, and support interactive voice investigation.

The voice pipeline is deployed as a separate CloudFormation stack. First deploy the base stack (Step 2), then deploy the voice stack:

```bash
make deploy-voice \
  IMAGE_URI=<your-ecr-uri> \
  ONCALL_PHONE="+15551234567" \
  LOG_GROUP_PATTERNS="/aws/lambda/*"
```

Or deploy both at once:

```bash
make deploy-all \
  IMAGE_URI=<your-ecr-uri> \
  EMAIL=oncall@example.com \
  LOG_GROUP_PATTERNS="/aws/lambda/*" \
  ONCALL_PHONE="+15551234567"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ONCALL_PHONE` | *required* | On-call engineer's phone number (E.164 format, e.g., `+15551234567`) |
| `LOG_GROUP_PATTERNS` | *required* | Must match the base stack's log group patterns |
| `IMAGE_URI` | *required* | ECR container image URI (should match the base stack) |
| `CONNECT_INSTANCE_ID` | `""` | Reuse an existing Connect instance (leave empty to create one) |

The only prerequisite beyond the base stack is enabling Nova 2 Sonic model access in Bedrock. See the [Voice Setup Guide](voice-setup-guide.md) for details.

The SNS notification is always sent regardless of whether the voice pipeline is enabled.

## Teardown

```bash
make teardown-all    # tears down voice stack first, then base stack
```

Or individually:

```bash
make teardown-voice  # voice stack only (Connect, Lex, DynamoDB)
make teardown        # base stack only (Lambda, SNS, EventBridge rules)
```

This removes all Beacon resources including the Connect instance, phone number, Lex bot, Lambda functions, DynamoDB table, and IAM roles. It does not affect your log groups or alarms.
