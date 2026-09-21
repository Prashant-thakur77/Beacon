from __future__ import annotations

import pytest

from beacon.remediation.registry import (
    REGISTRY,
    ParamError,
    confirmation_phrase,
    get_action,
    iam_write_actions,
    validate_params,
)

SG_PARAMS = {
    "group_id": "sg-0abc123",
    "ip_protocol": "tcp",
    "from_port": 5432,
    "to_port": 5432,
    "source_group_id": "sg-0def456",
}


def test_registry_has_exactly_the_allowlisted_actions() -> None:
    assert set(REGISTRY) == {
        "sg.restore_ingress",
        "sg.revoke_ingress",
        "ecs.force_redeploy",
    }


def test_inverse_actions_share_the_schema_and_only_undo_what_beacon_did() -> None:
    restore = REGISTRY["sg.restore_ingress"]
    revoke = REGISTRY[restore.inverse or ""]
    assert revoke.params_schema == restore.params_schema
    assert revoke.inverse is None  # undo is one level deep, never a chain
    assert REGISTRY["ecs.force_redeploy"].inverse is None  # idempotent, nothing to undo
    assert revoke.iam_actions == ("ec2:RevokeSecurityGroupIngress",)


def test_get_action_returns_none_for_unknown_ids() -> None:
    assert get_action("ec2.terminate_instances") is None
    assert get_action("sg.restore_ingress") is not None


def test_validate_params_accepts_complete_sg_params() -> None:
    assert validate_params("sg.restore_ingress", SG_PARAMS) == SG_PARAMS


def test_validate_params_rejects_missing_keys() -> None:
    with pytest.raises(ParamError, match="source_group_id"):
        validate_params(
            "sg.restore_ingress",
            {k: v for k, v in SG_PARAMS.items() if k != "source_group_id"},
        )


def test_validate_params_rejects_unknown_keys_and_wrong_types() -> None:
    with pytest.raises(ParamError, match="cidr"):
        validate_params("sg.restore_ingress", {**SG_PARAMS, "cidr": "0.0.0.0/0"})
    with pytest.raises(ParamError, match="from_port"):
        validate_params("sg.restore_ingress", {**SG_PARAMS, "from_port": "5432"})


def test_validate_params_rejects_unknown_action() -> None:
    with pytest.raises(ParamError, match="not allowlisted"):
        validate_params("rds.reboot", {})


def test_blast_radius_names_the_exact_resources() -> None:
    spec = get_action("sg.restore_ingress")
    assert spec is not None
    text = spec.blast_radius(SG_PARAMS)
    assert "1 ingress rule" in text and "sg-0abc123" in text and "5432" in text

    ecs = get_action("ecs.force_redeploy")
    assert ecs is not None
    assert "beacon-demo-webapp" in ecs.blast_radius(
        {"cluster": "c", "service": "beacon-demo-webapp"}
    )


def test_confirmation_phrase_is_approve_fix_n() -> None:
    assert confirmation_phrase(1) == "approve fix 1"
    assert confirmation_phrase(2) == "approve fix 2"


def test_iam_write_actions_are_exactly_the_three_calls() -> None:
    assert iam_write_actions() == {
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:RevokeSecurityGroupIngress",
        "ecs:UpdateService",
    }
