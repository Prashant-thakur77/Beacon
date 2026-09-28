"""Why one change to production was allowed to happen, as one artifact.

Six weeks after a voice agent changes infrastructure somebody asks who told it to.
"The model decided to" is not an answer. These tests pin the shape of the answer —
and, more importantly, that it never claims more than it can show.
"""

from __future__ import annotations

from typing import Any

from beacon import certificate

INCIDENT: dict[str, Any] = {
    "incident_id": "inc-1",
    "alarm_name": "payments-errors",
    "resolved_at": "2026-09-28T03:20:00Z",
    "proposals": [
        {
            "fix_id": 1,
            "action": "sg.restore_ingress",
            "params": {"group_id": "sg-0abc", "from_port": 5432},
            "blast_radius": "1 ingress rule on 1 security group.",
            "dry_run": {"ok": True, "code": "DryRunOperation"},
        }
    ],
    "changes": [
        {
            "event_name": "RevokeSecurityGroupIngress",
            "actor_short": "deploy-bot",
            "event_time": "03:01Z",
            "resource_ids": ["sg-0abc"],
        }
    ],
    "verification": {
        "last": {
            "attempt": 2,
            "checks": [
                {"name": "alarm_ok_after_fix", "ok": True, "detail": "OK since 03:19"},
                {"name": "metric_zero", "ok": True, "detail": "no datapoints"},
                {"name": "postcondition", "ok": True, "detail": "holds"},
            ],
        }
    },
}

VOICE_NOTE: dict[str, Any] = {
    "approval_id": "a-1",
    "fix_id": 1,
    "transcript_quote": "approve fix one",
    "channel": "telegram",
    "granted_at": "2026-09-28T03:18:00Z",
    "action": "sg.restore_ingress",
    "params": {"group_id": "sg-0abc", "from_port": 5432},
    "attestation": {"stt": "assemblyai-prerecorded", "confidence": 0.97},
}

LIVE_TURN: dict[str, Any] = {
    **VOICE_NOTE,
    "channel": "assemblyai",
    "attestation": {"stt": "assemblyai-voice-agent", "session_id": "sess_x"},
}


def test_the_certificate_quotes_the_words_and_names_what_they_authorised() -> None:
    cert = certificate.build(INCIDENT, VOICE_NOTE)
    assert cert["consent"]["phrase"] == "approve fix one"
    assert cert["change"]["action"] == "sg.restore_ingress"
    assert cert["change"]["dry_run"] == "DryRunOperation"
    assert cert["verification"]["passed"] == 3
    assert cert["caused_by"]["event"] == "RevokeSecurityGroupIngress"


def test_a_voice_note_says_its_confidence_gated_the_change() -> None:
    """The pre-recorded API scores before anything runs, so the number is a gate."""
    conf = certificate.build(INCIDENT, VOICE_NOTE)["consent"]["confidence"]
    assert conf["value"] == 0.97
    assert conf["gated_the_change"] is True
    assert "before the change" in conf["measured"]


def test_a_live_turn_admits_it_was_not_measured_at_the_time() -> None:
    """The Voice Agent API sends no confidence, and the certificate must not pretend."""
    conf = certificate.build(INCIDENT, LIVE_TURN)["consent"]["confidence"]
    assert conf["value"] is None
    assert conf["gated_the_change"] is False
    assert "attest" in conf["note"]


def test_an_attested_live_turn_reports_the_number_and_when_it_was_taken() -> None:
    incident = {
        **INCIDENT,
        "voice_attestation": {
            "sess_x": {
                "checks": [
                    {
                        "phrase": "approve fix one",
                        "ok": True,
                        "confidence": 0.93,
                        "weakest_word": "one",
                    }
                ]
            }
        },
    }
    conf = certificate.build(incident, LIVE_TURN)["consent"]["confidence"]
    assert conf["value"] == 0.93
    assert conf["weakest_word"] == "one"
    assert conf["gated_the_change"] is False, (
        "it reviewed the change, it did not gate it"
    )
    assert "after the fact" in conf["measured"]


def test_the_fingerprint_changes_when_the_certificate_does() -> None:
    """It detects an edited certificate. Not a signature, and never called one."""
    a = certificate.build(INCIDENT, VOICE_NOTE)
    b = certificate.build(
        INCIDENT, {**VOICE_NOTE, "transcript_quote": "approve fix two"}
    )
    assert a["certificate_id"].startswith("sha256:")
    assert a["certificate_id"] != b["certificate_id"]
    assert (
        certificate.build(INCIDENT, VOICE_NOTE)["certificate_id"] == a["certificate_id"]
    )


def test_the_markdown_reads_as_a_justification_not_a_data_dump() -> None:
    md = certificate.to_markdown(certificate.build(INCIDENT, VOICE_NOTE))
    assert "> “approve fix one”" in md
    assert "heard at **97%**" in md
    assert "checked before the change" in md
    assert "dry run `DryRunOperation` before anything ran" in md
    assert "3/3 checks" in md
    assert "RevokeSecurityGroupIngress" in md


def test_the_markdown_does_not_invent_a_percentage_it_does_not_have() -> None:
    md = certificate.to_markdown(certificate.build(INCIDENT, LIVE_TURN))
    assert "confidence not measured" in md
    assert "%" not in md.split("Said")[1].split("\n")[0]


def test_a_recording_is_linked_only_when_there_is_one() -> None:
    with_rec = certificate.build(INCIDENT, VOICE_NOTE, recording_url="https://x/a.wav")
    assert "play the words back" in certificate.to_markdown(with_rec)
    assert "play the words back" not in certificate.to_markdown(
        certificate.build(INCIDENT, VOICE_NOTE)
    )


def test_the_pull_request_leads_with_why_the_change_was_allowed() -> None:
    """A reviewer six weeks later wants the justification before the diff."""
    from beacon import fix_pr

    incident = {
        **INCIDENT,
        "status": "resolved",
        "rca_json": {"summary": "A rule went missing."},
        "timeline": [
            {
                "event": "verify_attempt",
                "detail": {
                    "attempt": 2,
                    "checks": INCIDENT["verification"]["last"]["checks"],
                },
            }
        ],
    }
    body = fix_pr.pr_body(
        incident,
        plan={"kind": "template_patch", "logical": {"group": "Db", "source": "App"}},
        approval=VOICE_NOTE,
        link="https://console.example/#board/inc-1",
    )
    assert "### Consent certificate" in body
    assert "> “approve fix one”" in body
    assert "heard at **97%**" in body
    # and it comes before the older approval paragraph, not after it
    assert body.index("### Consent certificate") < body.index(
        "### The approval that applied the runtime fix"
    )


def _attested(one_voice: dict[str, Any] | None) -> dict[str, Any]:
    check: dict[str, Any] = {
        "phrase": "approve fix one",
        "ok": True,
        "confidence": 0.93,
        "weakest_word": "one",
    }
    if one_voice is not None:
        check["one_voice"] = one_voice
    return {**INCIDENT, "voice_attestation": {"sess_x": {"checks": [check]}}}


def test_a_caller_who_was_alone_is_said_to_have_been_alone() -> None:
    incident = _attested({"ok": True, "voices": ["1A"], "window_ms": 15000})
    cert = certificate.build(incident, LIVE_TURN)
    assert cert["consent"]["one_voice"]["alone"] is True
    assert "the caller was alone" in certificate.to_markdown(cert)


def test_a_second_voice_in_the_room_is_on_the_certificate() -> None:
    incident = _attested({"ok": False, "voices": ["1A", "1B"], "window_ms": 15000})
    cert = certificate.build(incident, LIVE_TURN)
    assert cert["consent"]["one_voice"]["alone"] is False
    md = certificate.to_markdown(cert)
    assert "2 voices" in md and "1A, 1B" in md


def test_a_browser_session_says_nothing_rather_than_guessing() -> None:
    """One channel cannot separate a second person in the room. Silence, not a guess."""
    cert = certificate.build(_attested(None), LIVE_TURN)
    assert cert["consent"]["one_voice"] is None
    assert "One voice" not in certificate.to_markdown(cert)


def test_the_verification_is_read_from_the_timeline_when_that_is_where_it_lives() -> (
    None
):
    """Incidents keep verify_attempt in the timeline; only some carry a rolled-up key.

    Reading only the rolled-up one produced a certificate whose "CloudWatch agreed"
    field was silently empty on a real incident.
    """
    from_timeline = {
        "incident_id": "inc-1",
        "alarm_name": "payments-errors",
        "proposals": INCIDENT["proposals"],
        "timeline": [
            {
                "event": "verify_attempt",
                "detail": {
                    "attempt": 1,
                    "checks": [
                        {
                            "name": "alarm_ok_after_fix",
                            "ok": False,
                            "detail": "not yet",
                        },
                    ],
                },
            },
            {
                "event": "verify_attempt",
                "detail": {
                    "attempt": 2,
                    "checks": [
                        {
                            "name": "alarm_ok_after_fix",
                            "ok": True,
                            "detail": "OK since 03:19",
                        },
                        {"name": "metric_zero", "ok": True, "detail": "no datapoints"},
                    ],
                },
            },
        ],
    }
    cert = certificate.build(from_timeline, VOICE_NOTE)
    assert cert["verification"]["attempt"] == 2, "the latest attempt, not the first"
    assert cert["verification"]["passed"] == 2
    assert "2/2 checks on attempt 2" in certificate.to_markdown(cert)


TYPED_TURN: dict[str, Any] = {
    **VOICE_NOTE,
    "channel": "typed",
    "attestation": {"stt": "typed", "session_id": "sess_x"},
}


def test_typed_words_are_never_described_as_heard() -> None:
    """The console's typed line used to arrive as a voice turn with confidence 1.

    It rode in on channel "assemblyai" and the certificate read "heard at 100%
    (checked before the change)" — the strongest claim the artifact can make, for
    words nobody spoke.
    """
    cert = certificate.build(INCIDENT, TYPED_TURN)
    conf = cert["consent"]["confidence"]
    assert conf["value"] is None
    assert conf["gated_the_change"] is False
    assert conf["typed"] is True
    md = certificate.to_markdown(cert)
    assert "**Typed** into the console" in md, "'Said' is a claim about speech"
    assert "not spoken, so there is no audio to score" in md
    assert "transcribed by" not in md, "typed words had no transcriber"
    assert "%" not in md.split("Typed")[1].split("\n")[0]
    assert "attest" not in md, "there is no recording to appeal to"
