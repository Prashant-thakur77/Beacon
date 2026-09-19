.PHONY: deploy deploy-voice deploy-all teardown teardown-voice teardown-all \
       setup-image setup-agent-image deploy-demo teardown-demo break-demo fix-demo \
       test lint check-image-tags smoke-strands deploy-remediation teardown-remediation \
       snapshot-sg tag-remediable dry-run changes incidents lint-templates remediable-ecs break-demo-deploy fix-demo-deploy \
       deploy-console teardown-console web-build set-passcode console-config \
       check-reduction capture-run propose approve replay-approval demo-alarm demo-reset \
       demo-sleep demo-rehearse apply-on apply-off warm latest-incident local local-break local-fix preflight dashboard build-replay

# Deploy variables persisted by earlier runs (IMAGE_URI, EMAIL, ...). Gitignored.
-include .beacon.env

STACK_NAME ?= beacon
REGION     ?= us-east-1

# Host architecture drives the container platform, the Fargate CpuArchitecture
# and the Lambda Architectures value so an x86 laptop and an Apple Silicon
# laptop both produce images that actually start.
UNAME_M     := $(shell uname -m)
HOST_ARCH   := $(if $(filter aarch64 arm64,$(UNAME_M)),arm64,amd64)
CFN_ARCH    := $(if $(filter arm64,$(HOST_ARCH)),ARM64,X86_64)
LAMBDA_ARCH := $(if $(filter arm64,$(HOST_ARCH)),arm64,x86_64)

# Every image is tagged with the short SHA of the last commit that touched the
# image inputs (src/beacon, Dockerfiles, pyproject), so CloudFormation sees a
# new URI on every code change (a ':latest' URI never redeploys the Lambda)
# while docs-only commits do not invalidate a built image.
IMAGE_TAG ?= $(shell bash scripts/image_tag.sh)

# Persist KEY=VALUE into .beacon.env (upsert) so the next make run remembers it.
define save_env
	@touch .beacon.env && grep -v '^$(1)=' .beacon.env > .beacon.env.tmp || true; \
	echo '$(1)=$(2)' >> .beacon.env.tmp && mv .beacon.env.tmp .beacon.env
endef

# Required
EMAIL          ?=
LOG_GROUP_PATTERNS ?=

# Triggers (all default to template defaults if not set)
ENABLE_SCHEDULE     ?=
SCHEDULE_EXPRESSION ?=
ENABLE_ALARM        ?=
ALARM_NAME_PREFIX   ?=
ENABLE_SUBSCRIPTION ?=
SUBSCRIPTION_LOG_GROUP ?=
SUBSCRIPTION_FILTER ?=

# Analysis
LOOKBACK_MINUTES ?=
TOKEN_BUDGET     ?=

# Voice
ONCALL_PHONE         ?=
CONNECT_INSTANCE_ID  ?=

# Local image tags built by `make setup-image` / `make setup-agent-image`
LOCAL_IMAGE       ?= beacon:$(IMAGE_TAG)
LOCAL_AGENT_IMAGE ?= beacon-agent:$(IMAGE_TAG)

# ECR image URIs in your account (populated into .beacon.env by the setup targets)
IMAGE_URI       ?=
AGENT_IMAGE_URI ?=

# Container runtime (docker, podman, etc.)
CONTAINER_RT ?= $(shell command -v podman 2>/dev/null || command -v docker 2>/dev/null)

define check_param
$(if $($(1)),,$(error $(1) is required. Usage: make $(MAKECMDGOALS) $(1)=<value>))
endef

# Build the --parameter-overrides string, only including params that are set
OVERRIDES := ImageUri=$(IMAGE_URI)
ifneq ($(LOG_GROUP_PATTERNS),)
	OVERRIDES += LogGroupPatterns=$(LOG_GROUP_PATTERNS)
endif
ifneq ($(EMAIL),)
	OVERRIDES += NotificationEmail=$(EMAIL)
endif
ifneq ($(ENABLE_SCHEDULE),)
	OVERRIDES += EnableSchedule=$(ENABLE_SCHEDULE)
endif
ifneq ($(SCHEDULE_EXPRESSION),)
	OVERRIDES += ScheduleExpression="$(SCHEDULE_EXPRESSION)"
endif
ifneq ($(ENABLE_ALARM),)
	OVERRIDES += EnableAlarmTrigger=$(ENABLE_ALARM)
endif
ifneq ($(ALARM_NAME_PREFIX),)
	OVERRIDES += AlarmNamePrefix=$(ALARM_NAME_PREFIX)
endif
ifneq ($(ENABLE_SUBSCRIPTION),)
	OVERRIDES += EnableSubscription=$(ENABLE_SUBSCRIPTION)
endif
ifneq ($(SUBSCRIPTION_LOG_GROUP),)
	OVERRIDES += SubscriptionLogGroup=$(SUBSCRIPTION_LOG_GROUP)
endif
ifneq ($(SUBSCRIPTION_FILTER),)
	OVERRIDES += SubscriptionFilterPattern="$(SUBSCRIPTION_FILTER)"
endif
ifneq ($(LOOKBACK_MINUTES),)
	OVERRIDES += LookbackMinutes=$(LOOKBACK_MINUTES)
endif
ifneq ($(TOKEN_BUDGET),)
	OVERRIDES += TokenBudget=$(TOKEN_BUDGET)
endif
ifneq ($(INCIDENTS_ENABLED),)
	OVERRIDES += IncidentsEnabled=$(INCIDENTS_ENABLED)
endif
ifneq ($(APPLY_ENABLED),)
	OVERRIDES += ApplyEnabled=$(APPLY_ENABLED)
endif
ifneq ($(DASHBOARD_URL),)
	OVERRIDES += DashboardUrl=$(DASHBOARD_URL)
endif
ifneq ($(REMEDIABLE_ECS_SERVICES),)
	OVERRIDES += RemediableEcsServices=$(REMEDIABLE_ECS_SERVICES)
endif
REMEDIABLE_ECS_SERVICES ?=

# Console stack
CONSOLE_STACK   ?= $(STACK_NAME)-console
PASSCODE        ?=
POLLY_VOICE_ID  ?= Kajal
STT_LANGUAGE    ?= en-IN
VOICE_ENGINE    ?= strands
VOICE_BACKEND   ?= aws

# Remediation stack
REMEDIATION_STACK      ?= $(STACK_NAME)-remediation
CREATE_INCIDENTS_TABLE ?= true
APPLY_ENABLED          ?=
INCIDENTS_ENABLED      ?=
DASHBOARD_URL          ?=

# ---------- Image Setup ----------

setup-image:
	$(call check_param,REGION)
	@ACCOUNT_ID=$$(aws sts get-caller-identity --query Account --output text) && \
	ECR_REPO="$$ACCOUNT_ID.dkr.ecr.$(REGION).amazonaws.com/beacon" && \
	echo "==> Ensuring ECR repository exists..." && \
	aws ecr create-repository --repository-name beacon --region $(REGION) 2>/dev/null || true && \
	echo "==> Building $(LOCAL_IMAGE) for linux/$(HOST_ARCH) from Dockerfile..." && \
	$(CONTAINER_RT) build --platform linux/$(HOST_ARCH) -t $(LOCAL_IMAGE) . && \
	echo "==> Tagging for ECR..." && \
	$(CONTAINER_RT) tag $(LOCAL_IMAGE) "$$ECR_REPO:$(IMAGE_TAG)" && \
	echo "==> Logging in to ECR..." && \
	aws ecr get-login-password --region $(REGION) | $(CONTAINER_RT) login --username AWS --password-stdin "$$ECR_REPO" && \
	echo "==> Pushing to ECR (first push is multi-GB; later pushes move only the code layer)..." && \
	$(CONTAINER_RT) push "$$ECR_REPO:$(IMAGE_TAG)" && \
	echo "" && \
	echo "Done. IMAGE_URI saved to .beacon.env:" && \
	echo "  IMAGE_URI=$$ECR_REPO:$(IMAGE_TAG)" && \
	touch .beacon.env && grep -v '^IMAGE_URI=' .beacon.env > .beacon.env.tmp || true; \
	echo "IMAGE_URI=$$ECR_REPO:$(IMAGE_TAG)" >> .beacon.env.tmp && mv .beacon.env.tmp .beacon.env

# Slim image (no torch/cordon) for the voice, remediation, changes and dashboard Lambdas.
setup-agent-image:
	$(call check_param,REGION)
	@ACCOUNT_ID=$$(aws sts get-caller-identity --query Account --output text) && \
	ECR_REPO="$$ACCOUNT_ID.dkr.ecr.$(REGION).amazonaws.com/beacon-agent" && \
	echo "==> Ensuring ECR repository exists..." && \
	aws ecr create-repository --repository-name beacon-agent --region $(REGION) 2>/dev/null || true && \
	echo "==> Building $(LOCAL_AGENT_IMAGE) for linux/$(HOST_ARCH) from Dockerfile.agent..." && \
	$(CONTAINER_RT) build --platform linux/$(HOST_ARCH) -f Dockerfile.agent -t $(LOCAL_AGENT_IMAGE) . && \
	$(CONTAINER_RT) tag $(LOCAL_AGENT_IMAGE) "$$ECR_REPO:$(IMAGE_TAG)" && \
	echo "==> Logging in to ECR..." && \
	aws ecr get-login-password --region $(REGION) | $(CONTAINER_RT) login --username AWS --password-stdin "$$ECR_REPO" && \
	echo "==> Pushing to ECR..." && \
	$(CONTAINER_RT) push "$$ECR_REPO:$(IMAGE_TAG)" && \
	echo "" && \
	echo "Done. AGENT_IMAGE_URI saved to .beacon.env:" && \
	echo "  AGENT_IMAGE_URI=$$ECR_REPO:$(IMAGE_TAG)" && \
	touch .beacon.env && grep -v '^AGENT_IMAGE_URI=' .beacon.env > .beacon.env.tmp || true; \
	echo "AGENT_IMAGE_URI=$$ECR_REPO:$(IMAGE_TAG)" >> .beacon.env.tmp && mv .beacon.env.tmp .beacon.env

# Fails if an image URI is ':latest' or its tag is not the current git HEAD.
check-image-tags:
	@bash scripts/check_image_tag.sh IMAGE_URI "$(IMAGE_URI)"

# ---------- Deploy ----------

deploy: check-image-tags
	$(call check_param,IMAGE_URI)
	$(call check_param,EMAIL)
	$(call check_param,LOG_GROUP_PATTERNS)
	aws cloudformation deploy \
		--template-file template.yaml \
		--stack-name $(STACK_NAME) \
		--region $(REGION) \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides $(OVERRIDES) LambdaArchitecture=$(LAMBDA_ARCH)
	$(call save_env,EMAIL,$(EMAIL))
	$(call save_env,LOG_GROUP_PATTERNS,$(LOG_GROUP_PATTERNS))
	$(call save_env,REGION,$(REGION))
	$(if $(TOKEN_BUDGET),$(call save_env,TOKEN_BUDGET,$(TOKEN_BUDGET)))
	$(if $(ENABLE_ALARM),$(call save_env,ENABLE_ALARM,$(ENABLE_ALARM)))
	$(if $(ALARM_NAME_PREFIX),$(call save_env,ALARM_NAME_PREFIX,$(ALARM_NAME_PREFIX)))
	$(if $(INCIDENTS_ENABLED),$(call save_env,INCIDENTS_ENABLED,$(INCIDENTS_ENABLED)))
	$(if $(APPLY_ENABLED),$(call save_env,APPLY_ENABLED,$(APPLY_ENABLED)))
	@echo "Done. Check your email to confirm the SNS subscription."

deploy-voice:
	$(call check_param,IMAGE_URI)
	$(call check_param,ONCALL_PHONE)
	$(call check_param,LOG_GROUP_PATTERNS)
	$(eval VOICE_OVERRIDES := BaseStackName=$(STACK_NAME) OncallPhone=$(ONCALL_PHONE) LogGroupPatterns=$(LOG_GROUP_PATTERNS) ImageUri=$(IMAGE_URI))
ifneq ($(CONNECT_INSTANCE_ID),)
	$(eval VOICE_OVERRIDES += ConnectInstanceId=$(CONNECT_INSTANCE_ID))
endif
	aws cloudformation deploy \
		--template-file voice-template.yaml \
		--stack-name $(STACK_NAME)-voice \
		--region $(REGION) \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides $(VOICE_OVERRIDES)
	@echo "==> [1/7] Warming up voice handler Lambda..."
	@aws lambda invoke --function-name beacon-voice-$(STACK_NAME) --payload '{}' /dev/null --region $(REGION) 2>/dev/null || true
	@echo "==> [2/8] Reading stack outputs..."
	@INSTANCE_ARN=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME)-voice --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`BeaconConnectInstanceArn`].OutputValue' --output text) && \
	INSTANCE_ID=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME)-voice --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`BeaconConnectInstanceId`].OutputValue' --output text) && \
	BOT_ALIAS_ARN=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME)-voice --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`BeaconBotAliasArn`].OutputValue' --output text) && \
	BOT_ID=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME)-voice --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`BeaconBotId`].OutputValue' --output text) && \
	LAMBDA_ARN=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME)-voice --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`BeaconVoiceHandlerArn`].OutputValue' --output text) && \
	ALIAS_ID=$$(echo "$$BOT_ALIAS_ARN" | grep -o '[^/]*$$') && \
	echo "==> [3/8] Enabling Nova 2 Sonic S2S on bot locale..." && \
	aws lexv2-models update-bot-locale --bot-id "$$BOT_ID" --bot-version DRAFT --locale-id en_US \
		--nlu-intent-confidence-threshold 0.4 \
		--unified-speech-settings '{"speechFoundationModel":{"modelArn":"arn:aws:bedrock:$(REGION)::foundation-model/amazon.nova-2-sonic-v1:0"}}' \
		--region $(REGION) > /dev/null && \
	echo "==> [4/8] Building bot locale (this takes 30-90s)..." && \
	aws lexv2-models build-bot-locale --bot-id "$$BOT_ID" --bot-version DRAFT --locale-id en_US \
		--region $(REGION) > /dev/null && \
	for i in $$(seq 1 30); do \
		LSTATUS=$$(aws lexv2-models describe-bot-locale --bot-id "$$BOT_ID" --bot-version DRAFT --locale-id en_US \
			--region $(REGION) --query 'botLocaleStatus' --output text 2>/dev/null); \
		if [ "$$LSTATUS" = "Built" ] || [ "$$LSTATUS" = "ReadyExpressTesting" ]; then echo "    Locale build complete."; break; fi; \
		if [ "$$LSTATUS" = "Failed" ]; then echo "ERROR: Bot locale build failed." >&2; exit 1; fi; \
		printf "    Building... ($$LSTATUS)\n"; \
		sleep 10; \
	done && \
	echo "==> [5/8] Creating new bot version..." && \
	NEW_VER=$$(aws lexv2-models create-bot-version --bot-id "$$BOT_ID" \
		--bot-version-locale-specification '{"en_US":{"sourceBotVersion":"DRAFT"}}' \
		--region $(REGION) --query 'botVersion' --output text) && \
	echo "    Version $$NEW_VER created. Waiting for it to become available..." && \
	for i in $$(seq 1 30); do \
		VSTATUS=$$(aws lexv2-models describe-bot-version --bot-id "$$BOT_ID" --bot-version "$$NEW_VER" \
			--region $(REGION) --query 'botStatus' --output text 2>/dev/null); \
		if [ "$$VSTATUS" = "Available" ]; then echo "    Version $$NEW_VER is available."; break; fi; \
		if [ "$$VSTATUS" = "Failed" ]; then echo "ERROR: Bot version $$NEW_VER failed to build." >&2; exit 1; fi; \
		printf "    Waiting... ($$VSTATUS)\n"; \
		sleep 10; \
	done && \
	echo "==> [6/8] Updating bot alias to version $$NEW_VER and wiring Connect..." && \
	aws lexv2-models update-bot-alias --bot-id "$$BOT_ID" --bot-alias-id "$$ALIAS_ID" \
		--bot-alias-name live --bot-version "$$NEW_VER" \
		--bot-alias-locale-settings '{"en_US":{"enabled":true,"codeHookSpecification":{"lambdaCodeHook":{"lambdaARN":"'"$$LAMBDA_ARN"'","codeHookInterfaceVersion":"1.0"}}}}' \
		--region $(REGION) > /dev/null && \
	aws connect associate-bot --instance-id "$$INSTANCE_ARN" \
		--lex-v2-bot AliasArn="$$BOT_ALIAS_ARN" --region $(REGION) 2>/dev/null || true && \
	echo "==> [7/8] Writing Connect config to SSM..." && \
	CONTACT_FLOW_ID=$$(aws connect list-contact-flows --instance-id "$$INSTANCE_ID" --region $(REGION) \
		--query 'ContactFlowSummaryList[?contains(Name, `beacon-incident-commander`)].Id' --output text) && \
	PHONE_E164=$$(aws connect list-phone-numbers-v2 --target-arn "$$INSTANCE_ARN" --region $(REGION) \
		--query 'ListPhoneNumbersSummaryList[0].PhoneNumber' --output text) && \
	aws ssm put-parameter \
		--name "/beacon/$(STACK_NAME)/connect-config" \
		--type String \
		--value "{\"instance_id\":\"$$INSTANCE_ID\",\"contact_flow_arn\":\"$$CONTACT_FLOW_ID\",\"phone_number\":\"$$PHONE_E164\"}" \
		--overwrite \
		--region $(REGION) > /dev/null && \
	echo "    SSM parameter /beacon/$(STACK_NAME)/connect-config updated."
	@echo "==> [8/8] Updating base stack to enable voice..."
	@aws cloudformation deploy \
		--template-file template.yaml \
		--stack-name $(STACK_NAME) \
		--region $(REGION) \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides $(OVERRIDES) LambdaArchitecture=$(LAMBDA_ARCH) ConnectEnabled=true OncallPhone=$(ONCALL_PHONE)
	@echo "Voice pipeline active. Your phone will ring on incidents."

deploy-all:
	$(call check_param,IMAGE_URI)
	$(call check_param,EMAIL)
	$(call check_param,LOG_GROUP_PATTERNS)
	$(call check_param,ONCALL_PHONE)
	@$(MAKE) deploy IMAGE_URI=$(IMAGE_URI) EMAIL=$(EMAIL) LOG_GROUP_PATTERNS=$(LOG_GROUP_PATTERNS) \
		ENABLE_ALARM=$(ENABLE_ALARM) ALARM_NAME_PREFIX=$(ALARM_NAME_PREFIX) \
		ENABLE_SCHEDULE=$(ENABLE_SCHEDULE) ENABLE_SUBSCRIPTION=$(ENABLE_SUBSCRIPTION)
	@$(MAKE) deploy-voice IMAGE_URI=$(IMAGE_URI) ONCALL_PHONE=$(ONCALL_PHONE) \
		LOG_GROUP_PATTERNS=$(LOG_GROUP_PATTERNS)

# ---------- Teardown ----------

teardown-voice:
	aws cloudformation delete-stack --stack-name $(STACK_NAME)-voice --region $(REGION)
	@echo "Voice stack deletion initiated."

teardown:
	aws cloudformation delete-stack --stack-name $(STACK_NAME) --region $(REGION)
	@echo "Base stack deletion initiated."

teardown-all: teardown-voice
	@echo "Waiting for voice stack to delete before removing base stack..."
	aws cloudformation wait stack-delete-complete --stack-name $(STACK_NAME)-voice --region $(REGION) 2>/dev/null || true
	aws cloudformation delete-stack --stack-name $(STACK_NAME) --region $(REGION)
	@echo "All stacks deletion initiated."

# ---------- Demo (ECS + RDS) ----------

DEMO_INFRA_STACK := beacon-demo-infra
PG_VERSION       ?= 16.10

deploy-demo:
	@ACCOUNT_ID=$$(aws sts get-caller-identity --query Account --output text) && \
	DEMO_ECR_REPO="$$ACCOUNT_ID.dkr.ecr.$(REGION).amazonaws.com/beacon-demo" && \
	echo "==> Creating demo ECR repo (if needed)..." && \
	aws ecr create-repository --repository-name beacon-demo --region $(REGION) 2>/dev/null || true && \
	echo "==> Building demo app image for linux/$(HOST_ARCH)..." && \
	$(CONTAINER_RT) build --platform linux/$(HOST_ARCH) -t "$$DEMO_ECR_REPO:latest" -f demo/Dockerfile.demo demo/ && \
	echo "==> Logging in to ECR..." && \
	aws ecr get-login-password --region $(REGION) | $(CONTAINER_RT) login --username AWS --password-stdin "$$DEMO_ECR_REPO" && \
	echo "==> Pushing demo image..." && \
	$(CONTAINER_RT) push "$$DEMO_ECR_REPO:latest" && \
	echo "==> Deploying demo infrastructure (VPC, RDS, ECS, takes ~5 min)..." && \
	aws cloudformation deploy \
		--template-file demo/demo-infra-template.yaml \
		--stack-name $(DEMO_INFRA_STACK) \
		--region $(REGION) \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides DemoImageUri="$$DEMO_ECR_REPO:latest" CpuArchitecture=$(CFN_ARCH) PostgresVersion=$(PG_VERSION) && \
	echo "" && \
	echo "Demo infrastructure deployed. Verify healthy logs:" && \
	echo "  aws logs tail /ecs/beacon-demo --follow --region $(REGION)" && \
	echo "" && \
	echo "Trigger a network partition:" && \
	echo "  make break-demo"

teardown-demo:
	aws cloudformation delete-stack --stack-name $(DEMO_INFRA_STACK) --region $(REGION)
	@echo "Demo infra stack deletion initiated. Waiting..."
	aws cloudformation wait stack-delete-complete --stack-name $(DEMO_INFRA_STACK) --region $(REGION) 2>/dev/null || true
	@echo "Demo infrastructure torn down."

break-demo:
	@REGION=$(REGION) bash demo/trigger.sh break

fix-demo:
	@REGION=$(REGION) bash demo/trigger.sh fix

# ---------- Remediation stack ----------

lint-templates:
	$(CFN_LINT) template.yaml remediation-template.yaml console-template.yaml demo/demo-infra-template.yaml --ignore-checks W1011

deploy-remediation:
	@bash scripts/check_image_tag.sh AGENT_IMAGE_URI "$(AGENT_IMAGE_URI)"
	$(CFN_LINT) remediation-template.yaml
	aws cloudformation validate-template --template-body file://remediation-template.yaml --region $(REGION) > /dev/null
	@SNS_ARN=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`BeaconSNSTopicArn`].OutputValue' --output text) && \
	echo "==> Deploying $(REMEDIATION_STACK) (SNS: $$SNS_ARN)..." && \
	aws cloudformation deploy \
		--template-file remediation-template.yaml \
		--stack-name $(REMEDIATION_STACK) \
		--region $(REGION) \
		--capabilities CAPABILITY_NAMED_IAM \
		--parameter-overrides BaseStackName=$(STACK_NAME) AgentImageUri=$(AGENT_IMAGE_URI) \
			SnsTopicArn=$$SNS_ARN CreateIncidentsTable=$(CREATE_INCIDENTS_TABLE) \
			LambdaArchitecture=$(LAMBDA_ARCH) $(if $(APPLY_ENABLED),ApplyEnabled=$(APPLY_ENABLED),) \
			$(if $(REMEDIABLE_ECS_SERVICES),RemediableEcsServices=$(REMEDIABLE_ECS_SERVICES),)
	$(call save_env,AGENT_IMAGE_URI,$(AGENT_IMAGE_URI))
	@echo "Done. Next: make snapshot-sg && make tag-remediable && make dry-run"

teardown-remediation:
	aws cloudformation delete-stack --stack-name $(REMEDIATION_STACK) --region $(REGION)
	@echo "Remediation stack deletion initiated."

# Record the demo ECS service as remediable (cluster/service) in .beacon.env; deploys pass it to all stacks.
remediable-ecs:
	@CLUSTER=$$(aws cloudformation describe-stacks --stack-name $(DEMO_INFRA_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`DemoEcsCluster`].OutputValue' --output text) && \
	SERVICE=$$(aws cloudformation describe-stacks --stack-name $(DEMO_INFRA_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`DemoServiceName`].OutputValue' --output text) && \
	touch .beacon.env && grep -v '^REMEDIABLE_ECS_SERVICES=' .beacon.env > .beacon.env.tmp || true; \
	echo "REMEDIABLE_ECS_SERVICES=$$CLUSTER/$$SERVICE" >> .beacon.env.tmp && mv .beacon.env.tmp .beacon.env && \
	echo "REMEDIABLE_ECS_SERVICES=$$CLUSTER/$$SERVICE saved; redeploy (make deploy, deploy-remediation, deploy-console) to apply"

# Second failure mode: wedge the running task (restart-only). Beacon should propose ecs.force_redeploy.
break-demo-deploy:
	@REGION=$(REGION) bash demo/trigger.sh wedge

fix-demo-deploy:
	@REGION=$(REGION) bash demo/trigger.sh unwedge

# Golden snapshot of the demo security groups (run on a HEALTHY stack).
snapshot-sg:
	@RDS_SG=$$(aws cloudformation describe-stacks --stack-name $(DEMO_INFRA_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`RdsSecurityGroupId`].OutputValue' --output text) && \
	ECS_SG=$$(aws cloudformation describe-stacks --stack-name $(DEMO_INFRA_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`EcsSecurityGroupId`].OutputValue' --output text) && \
	$(PYTHON) scripts/snapshot_sg.py --param /beacon/$(STACK_NAME)/golden-sg --region $(REGION) $$RDS_SG $$ECS_SG

# Tag the demo resources so the remediator role's tag condition matches
# (idempotent; the demo template also sets the tags on create).
tag-remediable:
	@RDS_SG=$$(aws cloudformation describe-stacks --stack-name $(DEMO_INFRA_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`RdsSecurityGroupId`].OutputValue' --output text) && \
	ECS_SG=$$(aws cloudformation describe-stacks --stack-name $(DEMO_INFRA_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`EcsSecurityGroupId`].OutputValue' --output text) && \
	CLUSTER=$$(aws cloudformation describe-stacks --stack-name $(DEMO_INFRA_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`DemoEcsCluster`].OutputValue' --output text) && \
	SERVICE=$$(aws cloudformation describe-stacks --stack-name $(DEMO_INFRA_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`DemoServiceName`].OutputValue' --output text) && \
	aws ec2 create-tags --resources $$RDS_SG $$ECS_SG --tags Key=beacon:remediable,Value=true --region $(REGION) && \
	SERVICE_ARN=$$(aws ecs describe-services --cluster $$CLUSTER --services $$SERVICE --region $(REGION) --query 'services[0].serviceArn' --output text) && \
	aws ecs tag-resource --resource-arn $$SERVICE_ARN --tags key=beacon:remediable,value=true --region $(REGION) && \
	echo "Tagged $$RDS_SG $$ECS_SG $$SERVICE_ARN with beacon:remediable=true"

# Proposal-time dry-run of the demo fix under the remediator role.
# Must print DryRunOperation or InvalidPermission.Duplicate, never UnauthorizedOperation.
dry-run:
	@PARAMS=$$(REGION=$(REGION) bash scripts/demo_params.sh) && \
	PAYLOAD="{\"step\":\"dryrun\",\"action\":\"sg.restore_ingress\",\"params\":$$PARAMS}" && \
	echo "==> invoking beacon-remediate-$(STACK_NAME) with $$PAYLOAD" && \
	aws lambda invoke --function-name beacon-remediate-$(STACK_NAME) --region $(REGION) \
		--cli-binary-format raw-in-base64-out --payload "$$PAYLOAD" /dev/stdout | $(PYTHON) -c 'import json,sys; d=json.loads(sys.stdin.read().split("\n")[0]); print(json.dumps(d, indent=1)); sys.exit(0 if d.get("ok") else 1)' \
		&& echo "DRY RUN PASSED" || (echo "DRY RUN FAILED (see error above)"; exit 1)

# Proof that Cordon/Nova Embeddings ran on the last triage (greps the Lambda log).
check-reduction:
	@bash scripts/check_reduction.sh $(STACK_NAME) $(REGION)

# Save the latest real run (Lambda log, demo logs, incident item, ledger) under tests/fixtures/real/.
capture-run:
	@PYTHON=$(PYTHON) bash scripts/capture_run.sh $(STACK_NAME) $(REGION)

# Turn captured real runs (make capture-run) into the console's replay bundle, then publish it.
build-replay:
	$(PYTHON) scripts/build_replay.py
	@echo "Next: make console-config   (uploads web/dist incl. the new replay bundle)"

# Rows in the change ledger, newest first.
changes:
	@aws dynamodb scan --table-name beacon-changes-$(STACK_NAME) --region $(REGION) --output json | \
	$(PYTHON) -c 'import json,sys; rows=sorted(json.load(sys.stdin)["Items"], key=lambda r: r["sk"]["S"], reverse=True); [print(r["event_time"]["S"], r["event_name"]["S"], "by", r["actor_short"]["S"], ",".join(x["S"] for x in r.get("resource_ids",{}).get("L",[]))) for r in rows[:20]]; print(f"{len(rows)} row(s)")'

# Incidents, newest first.
incidents:
	@aws dynamodb scan --table-name beacon-incidents-$(STACK_NAME) --region $(REGION) --output json | \
	$(PYTHON) -c 'import json,sys; rows=sorted(json.load(sys.stdin)["Items"], key=lambda r: r["timestamp"]["S"], reverse=True); [print(r["timestamp"]["S"], r["incident_id"]["S"], r.get("status",{}).get("S","?"), r.get("alarm_name",{}).get("S","-")) for r in rows[:20]]; print(f"{len(rows)} row(s)")'

# ---------- Console stack (S3 + CloudFront + voice/dashboard Lambdas) ----------

set-passcode:
	$(call check_param,PASSCODE)
	$(call save_env,PASSCODE,$(PASSCODE))
	@echo "Passcode saved to .beacon.env"

# Builds web/dist if the Vite app exists; otherwise uses the placeholder page.
web-build:
	@if [ -f web/package.json ]; then cd web && npm ci --silent && npm run build --silent; else mkdir -p web/dist && cp web/placeholder/index.html web/dist/index.html; fi
	@echo "web/dist ready"

deploy-console: web-build
	$(call check_param,PASSCODE)
	@bash scripts/check_image_tag.sh AGENT_IMAGE_URI "$(AGENT_IMAGE_URI)"
	$(CFN_LINT) console-template.yaml
	aws cloudformation validate-template --template-body file://console-template.yaml --region $(REGION) > /dev/null
	@REMEDIATE_ARN=$$(aws cloudformation describe-stacks --stack-name $(REMEDIATION_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`RemediateFunctionArn`].OutputValue' --output text) && \
	SNS_ARN=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`BeaconSNSTopicArn`].OutputValue' --output text) && \
	echo "==> Deploying $(CONSOLE_STACK) (CloudFront creation takes 5-10 min the first time)..." && \
	aws cloudformation deploy \
		--template-file console-template.yaml \
		--stack-name $(CONSOLE_STACK) \
		--region $(REGION) \
		--capabilities CAPABILITY_NAMED_IAM \
		--parameter-overrides BaseStackName=$(STACK_NAME) AgentImageUri=$(AGENT_IMAGE_URI) \
			LambdaArchitecture=$(LAMBDA_ARCH) RemediateFunctionArn=$$REMEDIATE_ARN Passcode=$(PASSCODE) \
			SnsTopicArn=$$SNS_ARN \
			PollyVoiceId=$(POLLY_VOICE_ID) SttLanguage=$(STT_LANGUAGE) VoiceEngine=$(VOICE_ENGINE) \
			$(if $(APPLY_ENABLED),ApplyEnabled=$(APPLY_ENABLED),) \
			$(if $(REMEDIABLE_ECS_SERVICES),RemediableEcsServices=$(REMEDIABLE_ECS_SERVICES),)
	$(call save_env,PASSCODE,$(PASSCODE))
	@BUCKET=$$(aws cloudformation describe-stacks --stack-name $(CONSOLE_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' --output text) && \
	echo "==> Uploading web/dist to s3://$$BUCKET ..." && \
	aws s3 sync web/dist "s3://$$BUCKET" --delete --exclude config.json --region $(REGION)
	@ARCHIVED_INCIDENT_ID=$(ARCHIVED_INCIDENT_ID) bash scripts/console_config.sh $(CONSOLE_STACK) $(REGION) $(STT_LANGUAGE) $(VOICE_BACKEND)
	@CONSOLE_URL=$$(aws cloudformation describe-stacks --stack-name $(CONSOLE_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`ConsoleUrl`].OutputValue' --output text) && \
	touch .beacon.env && grep -v '^DASHBOARD_URL=' .beacon.env > .beacon.env.tmp || true; \
	echo "DASHBOARD_URL=$$CONSOLE_URL" >> .beacon.env.tmp && mv .beacon.env.tmp .beacon.env && \
	echo "Done. DASHBOARD_URL=$$CONSOLE_URL saved to .beacon.env (run 'make deploy' again so SNS emails link to it)."

# Re-upload the site + config without touching the stack.
console-config: web-build
	@BUCKET=$$(aws cloudformation describe-stacks --stack-name $(CONSOLE_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' --output text) && \
	aws s3 sync web/dist "s3://$$BUCKET" --delete --exclude config.json --region $(REGION)
	@ARCHIVED_INCIDENT_ID=$(ARCHIVED_INCIDENT_ID) bash scripts/console_config.sh $(CONSOLE_STACK) $(REGION) $(STT_LANGUAGE) $(VOICE_BACKEND)

teardown-console:
	@BUCKET=$$(aws cloudformation describe-stacks --stack-name $(CONSOLE_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' --output text 2>/dev/null) && \
	[ -n "$$BUCKET" ] && aws s3 rm "s3://$$BUCKET" --recursive --region $(REGION) || true
	aws cloudformation delete-stack --stack-name $(CONSOLE_STACK) --region $(REGION)
	@echo "Console stack deletion initiated (CloudFront disable + delete takes ~10 min)."

# ---------- Operator loop (Saturday morning proof, no UI needed) ----------

INCIDENT ?=
FIX      ?= 1
PHRASE   ?= approve fix $(FIX)
APPROVAL ?=

# Newest incident id.
latest-incident:
	@PYTHON=$(PYTHON) bash scripts/latest_incident.sh $(STACK_NAME) $(REGION)

# Ask Beacon to propose the fix for INCIDENT (defaults to the newest). Dry-run runs under the remediator role.
propose:
	@INC=$${INCIDENT:-$$(PYTHON=$(PYTHON) bash scripts/latest_incident.sh $(STACK_NAME) $(REGION))} && [ -n "$$INC" ] || { echo "no incident"; exit 1; }; \
	echo "==> propose_fix on $$INC" && \
	PYTHON=$(PYTHON) bash scripts/voice_tool.sh $(STACK_NAME) $(REGION) "$$INC" propose_fix '{}' "can you fix it" "$(PASSCODE)"

# Approve fix FIX on INCIDENT by "saying" PHRASE (the server checks the transcript).
approve:
	@INC=$${INCIDENT:-$$(PYTHON=$(PYTHON) bash scripts/latest_incident.sh $(STACK_NAME) $(REGION))} && [ -n "$$INC" ] || { echo "no incident"; exit 1; }; \
	echo "==> approve_fix $(FIX) on $$INC with transcript: '$(PHRASE)'" && \
	PYTHON=$(PYTHON) bash scripts/voice_tool.sh $(STACK_NAME) $(REGION) "$$INC" approve_fix '{"fix_id": $(FIX), "confirmation_phrase": "$(PHRASE)"}' "$(PHRASE)" "$(PASSCODE)"

# Re-run the Execute step for an approval: proves idempotency (returns the cached result, no second write).
replay-approval:
	$(call check_param,APPROVAL)
	@INC=$${INCIDENT:-$$(PYTHON=$(PYTHON) bash scripts/latest_incident.sh $(STACK_NAME) $(REGION))} && \
	PARAMS=$$(REGION=$(REGION) bash scripts/demo_params.sh) && \
	PAYLOAD="{\"step\":\"execute\",\"approval_id\":\"$(APPROVAL)\",\"incident_id\":\"$$INC\",\"action\":\"sg.restore_ingress\",\"params\":$$PARAMS}" && \
	aws lambda invoke --function-name beacon-remediate-$(STACK_NAME) --region $(REGION) \
		--cli-binary-format raw-in-base64-out --payload "$$PAYLOAD" /dev/stdout | head -1 | $(PYTHON) -m json.tool && \
	echo "(idempotent_replay: true means the stored result was returned and nothing was re-executed)"

# Force the alarm into ALARM without waiting for metric evaluation (retakes only; the recorded take uses the real alarm).
demo-alarm:
	@ALARM=$$(aws cloudformation describe-stacks --stack-name $(DEMO_INFRA_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`DemoAlarmName`].OutputValue' --output text) && \
	aws cloudwatch set-alarm-state --alarm-name "$$ALARM" --state-value ALARM --state-reason "beacon demo" --region $(REGION) && \
	echo "Alarm $$ALARM forced to ALARM. Beacon triage starts now."

# Back to a healthy, quiet baseline between takes: fix the SG, set the alarm OK, wait for 200s in the app log.
demo-reset:
	@REGION=$(REGION) bash demo/trigger.sh fix
	@ALARM=$$(aws cloudformation describe-stacks --stack-name $(DEMO_INFRA_STACK) --region $(REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`DemoAlarmName`].OutputValue' --output text) && \
	aws cloudwatch set-alarm-state --alarm-name "$$ALARM" --state-value OK --state-reason "beacon demo reset" --region $(REGION) && \
	echo "==> waiting for healthy 200 lines from the demo app (up to 2 min)..." && \
	for i in $$(seq 1 12); do \
		if aws logs tail /ecs/beacon-demo --since 20s --region $(REGION) --format short 2>/dev/null | grep -q " 200 "; then echo "Demo app healthy. Ready for the next take."; exit 0; fi; \
		sleep 10; \
	done; echo "WARNING: no 200 lines seen yet; check aws logs tail /ecs/beacon-demo"

# The second incident: a REAL re-break. With a Sleep Contract in place Beacon fixes it without paging.
demo-sleep:
	@REGION=$(REGION) bash demo/trigger.sh break
	@echo "==> Broken again. Waiting for the real alarm (2-3 min). Watch: make incidents / the Night Board."
	@echo "    (retake shortcut after ~50 s of errors: make demo-alarm)"

# Full unattended rehearsal of cycle 1 through the CLI: break -> alarm -> incident -> propose -> approve -> resolved.
demo-rehearse:
	@echo "==> [1/5] reset" && $(MAKE) demo-reset REGION=$(REGION)
	@echo "==> [2/5] break" && REGION=$(REGION) bash demo/trigger.sh break
	@echo "==> [3/5] waiting for a new incident (real alarm, up to 6 min)..." && \
	BEFORE=$$(PYTHON=$(PYTHON) bash scripts/latest_incident.sh $(STACK_NAME) $(REGION)) && \
	for i in $$(seq 1 36); do NOW=$$(PYTHON=$(PYTHON) bash scripts/latest_incident.sh $(STACK_NAME) $(REGION)); if [ -n "$$NOW" ] && [ "$$NOW" != "$$BEFORE" ]; then echo "    incident $$NOW"; break; fi; sleep 10; done && \
	INC=$$(PYTHON=$(PYTHON) bash scripts/latest_incident.sh $(STACK_NAME) $(REGION)) && \
	echo "==> [4/5] propose + approve" && \
	PYTHON=$(PYTHON) bash scripts/voice_tool.sh $(STACK_NAME) $(REGION) "$$INC" propose_fix '{}' "can you fix it" "$(PASSCODE)" && \
	PYTHON=$(PYTHON) bash scripts/voice_tool.sh $(STACK_NAME) $(REGION) "$$INC" approve_fix '{"fix_id": 1, "confirmation_phrase": "approve fix 1"}' "approve fix 1" "$(PASSCODE)" && \
	echo "==> [5/5] waiting for resolved (up to 5 min)..." && \
	for i in $$(seq 1 30); do \
		ST=$$(aws dynamodb get-item --table-name beacon-incidents-$(STACK_NAME) --region $(REGION) --key "{\"incident_id\":{\"S\":\"$$INC\"}}" --query 'Item.status.S' --output text); \
		echo "    status=$$ST"; if [ "$$ST" = "resolved" ]; then echo "REHEARSAL PASSED"; exit 0; fi; if [ "$$ST" = "escalated" ]; then echo "REHEARSAL FAILED: escalated"; exit 1; fi; sleep 10; \
	done; echo "REHEARSAL TIMED OUT"; exit 1

# Kill switch across all three write paths (merges env so nothing else is lost) and remember it for the next deploy.
apply-off:
	@for FN in beacon-$(STACK_NAME) beacon-voice-turn-$(STACK_NAME) beacon-remediate-$(STACK_NAME); do \
		$(PYTHON) scripts/set_env.py $$FN APPLY_ENABLED false --region $(REGION); \
	done
	$(call save_env,APPLY_ENABLED,false)
	@echo "APPLY_ENABLED=false on triage, voice and remediate. No write path can execute."

apply-on:
	@for FN in beacon-$(STACK_NAME) beacon-voice-turn-$(STACK_NAME) beacon-remediate-$(STACK_NAME); do \
		$(PYTHON) scripts/set_env.py $$FN APPLY_ENABLED true --region $(REGION); \
	done
	$(call save_env,APPLY_ENABLED,true)
	@echo "APPLY_ENABLED=true on triage, voice and remediate."

# Everything that must be true before recording, as one green/red table.
preflight:
	@PYTHON=$(PYTHON) bash scripts/preflight.sh $(STACK_NAME) $(REGION)

# CloudWatch dashboard from the EMF metrics (the "it is real" shot for the video).
dashboard:
	@$(PYTHON) scripts/make_dashboard.py --stack $(STACK_NAME) --region $(REGION)

# Warm the voice and remediate Lambdas before recording.
warm:
	@for FN in beacon-voice-turn-$(STACK_NAME) beacon-remediate-$(STACK_NAME) beacon-dashboard-$(STACK_NAME); do \
		aws lambda invoke --function-name $$FN --region $(REGION) --cli-binary-format raw-in-base64-out --payload '{"mode":"warm"}' /dev/null > /dev/null && echo "warm: $$FN"; \
	done

# ---------- Local / Build It mode (no AWS account) ----------

LOCAL_PORT     ?= 8000
LOCAL_PASSCODE ?= local

# The whole product on localhost against an in-process moto AWS: real tools,
# real safety checks, scripted agent instead of Bedrock, browser TTS.
local: web-build
	@echo "==> http://localhost:$(LOCAL_PORT)  (passcode: $(LOCAL_PASSCODE)). Ctrl-C to stop."
	@echo "    http://localhost:$(LOCAL_PORT)/?night=1 plays the whole night unattended."
	BEACON_LOCAL_PASSCODE=$(LOCAL_PASSCODE) PORT=$(LOCAL_PORT) $(PYTHON) scripts/local_server.py

local-break:
	@curl -s -X POST -H "x-beacon-passcode: $(LOCAL_PASSCODE)" http://localhost:$(LOCAL_PORT)/local/break && echo

local-fix:
	@curl -s -X POST -H "x-beacon-passcode: $(LOCAL_PASSCODE)" http://localhost:$(LOCAL_PORT)/local/fix && echo

# ---------- Development ----------

PYTHON   ?= .venv/bin/python
CFN_LINT ?= .venv/bin/cfn-lint

# One Strands turn with one tool on Nova 2 Lite against your real account.
# Needs AWS credentials + Bedrock model access. Decides VOICE_ENGINE (see PLAN.md).
smoke-strands:
	$(PYTHON) scripts/smoke_strands.py

test:
	pytest -v

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/
	mypy src/beacon/
