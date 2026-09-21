# Architecture

Three CloudFormation stacks plus the demo workload. Every arrow below is a real integration in the account; the names are the resources' names.

## The system

```mermaid
flowchart LR
  subgraph patient["Demo workload (beacon-demo-infra)"]
    APP["Fargate app<br/>beacon-demo-webapp"] --> RDS[("RDS PostgreSQL")]
    APP --> LOGS["CloudWatch Logs<br/>/ecs/beacon-demo"]
    LOGS --> ALARM["Alarm<br/>beacon-demo-infra-errors"]
  end

  subgraph base["Base stack (beacon)"]
    ALARM -->|EventBridge| TRIAGE["Lambda · triage<br/>Cordon + Nova 2 Lite"]
    TRIAGE --> BR["Bedrock<br/>Nova 2 Lite · Nova 2 Embeddings"]
    TRIAGE --> SNS["SNS<br/>page / not-woken email"]
  end

  subgraph rem["Remediation stack (beacon-remediation)"]
    CT["CloudTrail → EventBridge"] --> LEDGER["Lambda · changes"] --> CHG[("DynamoDB<br/>changes")]
    TRIAGE --> INC[("DynamoDB<br/>incidents")]
    TRIAGE -.contract match.-> SFN["Step Functions<br/>beacon-remediate"]
    SFN --> REMED["Lambda · remediate<br/>write-only role"]
    REMED --> APPR[("approvals")]
    REMED --> CONTR[("contracts")]
    REMED -->|"AuthorizeSecurityGroupIngress<br/>UpdateService (tag-scoped)"| APP
    REMED -->|verify| ALARM
  end

  subgraph console["Console stack (beacon-console)"]
    S3["S3 bucket<br/>Night Board build"] --> EDGE["CloudFront, or the<br/>static_site Lambda URL (HTTPS)"] --> YOU(("you, 3 AM"))
    YOU <-->|"full duplex · tools on one socket"| AAI["AssemblyAI Voice Agent API<br/>Universal-3 Pro · turn detection · TTS"]
    YOU -->|"POST /tools/name (transcript)"| VOICE["Lambda · voice-turn<br/>tools + consent · Strands cascade"]
    VOICE --> BR
    VOICE -->|dry run via| REMED
    VOICE -->|approval record + attestation| APPR
    VOICE --> SFN
    VOICE --> POLLY["Polly + Transcribe<br/>(AWS cascade)"]
    VOICE -->|"GET /recordings/id"| AAI
    YOU --> DASH["Lambda · dashboard<br/>read-only, redacted"] --> INC
  end

  subgraph phone["Where the engineer actually is"]
    TG(("Telegram<br/>page · voice note")) -->|"webhook (secret + allowlist)"| VOICE
    VOICE -->|"pre-recorded STT · word confidence"| AAIP["AssemblyAI /v2/transcript"]
    TRIAGE -->|"page with Talk · Fix 1 · Ack"| TG
    VOICE -->|"open_fix_pr: branch + PR"| GH["GitHub<br/>infrastructure repo"]
  end
```

## One incident, end to end

```mermaid
sequenceDiagram
  autonumber
  participant CW as CloudWatch
  participant T as triage Lambda
  participant B as Bedrock (Nova 2)
  participant D as DynamoDB
  participant U as Engineer (browser)
  participant V as voice-turn Lambda
  participant R as remediate Lambda (write-only role)
  participant S as Step Functions

  CW->>T: alarm ALARM (EventBridge)
  T->>T: SG drift vs golden snapshot · CloudTrail ledger
  T->>B: reduced logs → RCA (STATUS, SUMMARY, EVIDENCE, BEACON_JSON)
  T->>D: incident (awaiting_engineer) + page via SNS
  U->>V: "can you fix it?"
  V->>R: dryrun(sg.restore_ingress, exact params)
  R-->>V: DryRunOperation · blast radius · role
  V-->>U: proposal read aloud + "say: approve fix 1"
  U->>V: "approve fix 1" (raw transcript)
  V->>D: approval record (quotes the transcript)
  V->>S: start execution
  S->>R: DryRun → RequireApproval → Execute (consume record, once)
  R->>CW: wait 30 s · Verify ×6: alarm OK after fix · metric 0 · rule present
  S-->>D: resolved (or escalated → SNS page)
  V-->>U: "handle this myself next time?" → read-back → "grant contract for 7 days"
  V->>D: Sleep Contract (alarm + action + resources + TTL + uses + quote)
  Note over CW,S: Next time the same alarm fires, triage matches the contract and runs the same loop with source=contract. Nobody is woken.
```

## The verified remediation loop

```mermaid
stateDiagram-v2
  [*] --> DryRun
  DryRun --> RequireApproval: ok
  DryRun --> Escalate: refused
  RequireApproval --> Execute: approval record matches action + params hash
  RequireApproval --> Escalate: no record
  Execute --> Wait30
  Wait30 --> Verify
  Verify --> Resolve: alarm OK after execute ∧ metric 0 ∧ post-condition
  Verify --> Wait30: attempt < 6
  Verify --> Escalate: attempt = 6
  Resolve --> [*]
  Escalate --> [*]: page a human (SNS)
```

## Trust boundaries

| Role | May | May not |
|---|---|---|
| triage (`beacon-*`) | read logs/metrics/alarms, Bedrock, write incidents, start the state machine | any EC2/ECS/RDS write |
| voice-turn | everything the engineer can see, invoke `remediate` for dry runs, write approval records, start the state machine, STS for the mic | any EC2/ECS/RDS write |
| dashboard | read tables (redacted), revoke a contract | anything else |
| remediator | `ec2:AuthorizeSecurityGroupIngress` and `ecs:UpdateService` on resources tagged `beacon:remediable=true` (+ the untaggable `security-group-rule/*`), verify reads | anything else |

`tests/test_template_safety.py` parses the templates and fails if this table stops being true.
