# Submission day — Sun 20 Sep 2026, deadline 20:00 IST

State at 11:10: nothing deployed (credits untouched: $100), repo has no GitHub remote, no video, no blog. Everything buildable is built and green (`main` 244 tests, `?night=1` plays a full night locally). Today is execution only. Paste any error verbatim into chat.

**Rule for the day: the Build It take is recorded first, before any AWS step can fail. Ship It is the upgrade, not the plan.**

## Timeline (IST)

| When | Do | Done when |
|---|---|---|
| 11:10–11:25 | **Non-negotiables first.** (1) Builder Center: verify student profile ("required to compete"). (2) Create the GitHub repo, then `git remote add origin <url> && git push -u origin main && git push origin assemblyai`. | Repo public on GitHub; profile verified. |
| 11:25–11:45 | **Record the Build It take now.** `make local` → `http://localhost:8000/?night=1` at 1440×900, screen-record ~2 min (OBS or `ffmpeg -f x11grab`). Also record 20 s each of: `bash scripts/gate.sh` finishing green, `tests/test_template_safety.py` in the editor, the Safety tab. | A raw clip that could be submitted as-is. |
| 11:45–12:15 | Runbook **§0** steps 1–7: AWS CLI, credentials (us-east-1), Bedrock model access for Nova 2 Lite + Nova 2 Multimodal Embeddings, a CloudTrail trail, ECR login. Then §0 step 8 in two terminals: `make deploy-demo` and `make setup-image` (20–40 min, unattended). | Both terminals running. |
| 12:15–12:45 | While they run: publish the blog on Builder Center from `docs/blog.md` (add the Night Board screenshot `docs/assets/night-board.png`). Fill the submission form draft from `docs/submission.md`. | Blog URL in hand. |
| 12:45–13:30 | **§1**: `make deploy …`, confirm the SNS email, `make break-demo`, wait for the RCA email; `make smoke-strands`. | RCA email received; Strands smoke passed (else `VOICE_ENGINE=litellm`). |
| 13:30–14:30 | **§2**: `make setup-agent-image` ‖ `make deploy-remediation` → `make snapshot-sg && make tag-remediable && make remediable-ecs && make dry-run` (must print `DRY RUN PASSED`) → `make set-passcode PASSCODE=<word> && make deploy-console`. | Console URL opens; `/health` on both Function URLs. |
| 14:30–15:30 | **§3**: one full cycle in the real UI: break → talk → approve → resolved → contract → `make demo-sleep` (second incident, not woken). Then `make capture-run && make build-replay && make console-config` so the replay is real. | Night Board shows 2 incidents, 1 human woken, on the live URL. |
| **15:30** | **Hard cutoff.** Live cycle green → record the Ship It take (script: `docs/demo-script.md`). Not green → submit Build It with the 11:25 clip and the AWS console as B-roll; do not debug past this line. | Decision made. |
| 15:30–17:30 | Record and cut the 3-minute video; upload unlisted to YouTube. Update `config.json` passcode note for judges. | Video URL. |
| 17:30–18:30 | Final form: live URL + passcode (Ship It) or repo + `make local` instructions (Build It), video, blog, repo. Re-read `docs/submission.md` against the five criteria. | Submitted. |
| 18:30–20:00 | Buffer. `make apply-off` if you leave the stack up; leave it up until judging if credits allow (they do: the demo stack is ~$1/day). | — |

## If AWS fights back

- Any CloudFormation failure: `aws cloudformation describe-stack-events --stack-name <stack> --max-items 25 --region us-east-1 --query 'StackEvents[?ResourceStatus==\`CREATE_FAILED\` || ResourceStatus==\`UPDATE_FAILED\`].[LogicalResourceId,ResourceStatusReason]' --output table` → paste it.
- Image push too slow on your link: keep going with §1 only after `make setup-image` finishes; nothing else depends on it until then.
- Bedrock access pending past 12:30: proceed with the deploy anyway (the triage Lambda fails loudly, everything else deploys); the Build It take is already recorded.
- Console works but the mic does not: `?stt=webspeech`, then typed input — Polly still speaks; say so in the video, it is honest.

## Form fields, ready to paste

- **Title:** Beacon Night Shift — the on-call agent that fixes the 3 AM page with your voice, and does not wake you the second time
- **Track:** decided by the judges from what is submitted; the same entry counts for Ship It, Build It and Best UI.
- **What I learned:** the bullet list under "What I learned" in `docs/submission.md`.
- **Where AWS fits:** the table in `docs/submission.md`; for Build It, name Strands Agents SDK and Powertools for AWS Lambda explicitly (both run in `make local`).
