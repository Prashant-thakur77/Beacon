# Open questions (agent → human) and answers (human → agent)

The agent proceeds on the stated assumption until you answer. Answer inline.

## Answers needed tonight
- [ ] AWS account id (for docs only): `____________`
- [ ] `uname -m` on the deploy machine — assumption: `x86_64` (this sandbox).
- [ ] Bedrock model access granted for Nova 2 Lite + Nova 2 Multimodal Embeddings? Are Nova 2 Sonic / Nova Micro listed?
- [ ] CloudTrail trail `beacon-trail` logging? (`IsLogging: true`)

## Assumptions the agent is proceeding on
- Region us-east-1 for everything (Nova 2 Lite, embeddings, Polly Kajal, Transcribe all available there).
- Stack names: `beacon`, `beacon-demo-infra`, `beacon-remediation`, `beacon-console`.
- The deploy machine is this machine (same working tree), so no git push/pull between agent and human.
- Amazon Connect phone path is not deployed and not in the video.
