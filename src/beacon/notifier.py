from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import boto3

from beacon.events import TriggerInfo, TriggerType

if TYPE_CHECKING:
    from mypy_boto3_sns import SNSClient

    from beacon.config import BeaconConfig


def _trigger_label(trigger: TriggerInfo) -> str:
    """Return a short human-readable label for the trigger source."""
    if trigger.trigger_type == TriggerType.ALARM:
        return f"Alarm: {trigger.alarm_name or 'Unknown'}"
    if trigger.trigger_type == TriggerType.SUBSCRIPTION:
        return "Subscription filter match"
    return "Scheduled scan"


def _format_message(
    analysis: str,
    trigger: TriggerInfo,
    *,
    link: str = "",
    variant: str = "page",
    contract: dict[str, Any] | None = None,
) -> str:
    """Build the notification body: header, optional contract note, link, RCA."""
    header = [
        f"Trigger: {_trigger_label(trigger)}",
        f"Time: {datetime.now(tz=UTC).isoformat()}",
    ]
    if variant == "contract" and contract is not None:
        header.append(
            "Handled under Sleep Contract "
            f"{contract.get('contract_id')} (you were not woken). Beacon is applying "
            f"{contract.get('action')} and will verify recovery; you get a second "
            "email when it is resolved or if a human is needed."
        )
        header.append(
            f'Contract granted by you: "{contract.get("transcript_quote", "")}"'
        )
    elif link:
        header.append(
            f"Beacon is awaiting your word. Open the Night Board to talk to it: {link}"
        )
    if link and variant == "contract":
        header.append(f"Night Board: {link}")
    return "\n".join(header) + f"\n\n{analysis}"


def notify(
    analysis: str,
    trigger: TriggerInfo,
    config: BeaconConfig,
    *,
    sns_client: SNSClient | None = None,
    link: str = "",
    variant: str = "page",
    contract: dict[str, Any] | None = None,
) -> None:
    """Publish the triage analysis to the configured SNS topic.

    ``variant="contract"`` produces the "you were not woken" email.
    The subject line is truncated to 100 characters (SNS limit).
    """
    if sns_client is None:
        sns_client = boto3.client("sns")

    message = _format_message(
        analysis, trigger, link=link, variant=variant, contract=contract
    )
    prefix = "Beacon (not woken)" if variant == "contract" else "Beacon"
    subject = f"{prefix} - {_trigger_label(trigger)}"[:100]

    sns_client.publish(
        TopicArn=config.sns_topic_arn,
        Subject=subject,
        Message=message,
    )
