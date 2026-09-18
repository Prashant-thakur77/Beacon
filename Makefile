.PHONY: deploy deploy-voice deploy-all teardown teardown-voice teardown-all \
       setup-image deploy-demo teardown-demo break-demo fix-demo test lint

STACK_NAME := beacon
REGION     ?= us-east-1

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

# Local image tag built from the Dockerfile by `make setup-image`
LOCAL_IMAGE ?= beacon:latest

# ECR image URI in your account (populated by `make setup-image`)
IMAGE_URI ?=

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

# ---------- Image Setup ----------

setup-image:
	$(call check_param,REGION)
	@ACCOUNT_ID=$$(aws sts get-caller-identity --query Account --output text) && \
	ECR_REPO="$$ACCOUNT_ID.dkr.ecr.$(REGION).amazonaws.com/beacon" && \
	echo "==> Ensuring ECR repository exists..." && \
	aws ecr create-repository --repository-name beacon --region $(REGION) 2>/dev/null || true && \
	echo "==> Building $(LOCAL_IMAGE) from Dockerfile..." && \
	$(CONTAINER_RT) build -t $(LOCAL_IMAGE) . && \
	echo "==> Tagging for ECR..." && \
	$(CONTAINER_RT) tag $(LOCAL_IMAGE) "$$ECR_REPO:latest" && \
	echo "==> Logging in to ECR..." && \
	aws ecr get-login-password --region $(REGION) | $(CONTAINER_RT) login --username AWS --password-stdin "$$ECR_REPO" && \
	echo "==> Pushing to ECR..." && \
	$(CONTAINER_RT) push "$$ECR_REPO:latest" && \
	echo "" && \
	echo "Done. Use this IMAGE_URI for deploy commands:" && \
	echo "  IMAGE_URI=$$ECR_REPO:latest"

# ---------- Deploy ----------

deploy:
	$(call check_param,IMAGE_URI)
	$(call check_param,EMAIL)
	$(call check_param,LOG_GROUP_PATTERNS)
	aws cloudformation deploy \
		--template-file template.yaml \
		--stack-name $(STACK_NAME) \
		--region $(REGION) \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides $(OVERRIDES)
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
		--parameter-overrides $(OVERRIDES) ConnectEnabled=true OncallPhone=$(ONCALL_PHONE)
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

deploy-demo:
	@ACCOUNT_ID=$$(aws sts get-caller-identity --query Account --output text) && \
	DEMO_ECR_REPO="$$ACCOUNT_ID.dkr.ecr.$(REGION).amazonaws.com/beacon-demo" && \
	echo "==> Creating demo ECR repo (if needed)..." && \
	aws ecr create-repository --repository-name beacon-demo --region $(REGION) 2>/dev/null || true && \
	echo "==> Building demo app image..." && \
	$(CONTAINER_RT) build -t "$$DEMO_ECR_REPO:latest" -f demo/Dockerfile.demo demo/ && \
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
		--parameter-overrides DemoImageUri="$$DEMO_ECR_REPO:latest" && \
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
	@bash demo/trigger.sh break

fix-demo:
	@bash demo/trigger.sh fix

# ---------- Development ----------

test:
	pytest -v

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/
	mypy src/beacon/
