/** A slow, seamless ticker: the track is duplicated so translateX(-50%) loops without a seam. */
export const AWS_SERVICES = ["CloudWatch", "Bedrock", "Strands", "Step Functions", "DynamoDB", "Transcribe", "Polly", "SNS", "CloudFront", "EventBridge", "Fargate", "RDS"];

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
