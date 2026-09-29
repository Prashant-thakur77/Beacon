/** A slow, seamless ticker: the track is duplicated so translateX(-50%) loops without a seam. */
/* What this actually runs on, in the order that matters. AssemblyAI led the
   list nowhere and Transcribe was on it, which read as though the speech came
   from somewhere else. Bedrock came off: inference is refused on the demo
   account ("Operation not allowed"), so the deterministic triage path is what
   runs, and a ticker should not name a dependency the live system does without.
   CloudFront came off too -- the console is deployed in S3 website mode. */
export const AWS_SERVICES = [
  "AssemblyAI Voice Agent API",
  "Universal-3 Pro",
  "Lambda",
  "Step Functions",
  "DynamoDB",
  "EventBridge",
  "CloudTrail",
  "CloudWatch",
  "ECS",
  "RDS",
  "SNS",
  "S3",
];

export function Marquee({ items = AWS_SERVICES, label = "Built on" }: { items?: string[]; label?: string }) {
  const track = (
    <>
      {items.map((it) => (
        <span key={it} className="mq-item">
          {it}
        </span>
      ))}
    </>
  );
  return (
    <div className="marquee" role="marquee" aria-label={`${label}: ${items.join(", ")}`}>
      {label ? <span className="mq-label">{label}</span> : null}
      <div className="mq-clip">
        <div className="mq-track">
          <div className="mq-half" aria-hidden="false">
            {track}
          </div>
          <div className="mq-half" aria-hidden="true">
            {track}
          </div>
        </div>
      </div>
    </div>
  );
}
