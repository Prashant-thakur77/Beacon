"""Fix at the source: the pull request that ends the incident.

The runtime fix (restore the rule) is a patch on the account. The cause was a
change; the durable fix lives in the infrastructure code. After a fix has been
applied and verified, ``open_fix_pr`` opens a pull request on the configured
repository that

* adds ``docs/incidents/<date>-<alarm>.md`` — the postmortem the API already
  generates (timeline, the approval quote, the verify checks, the change ledger);
* for ``sg.restore_ingress`` on a CloudFormation template that does **not**
  declare the rule, appends an ``AWS::EC2::SecurityGroupIngress`` resource with
  exactly the parameters that passed the dry run (mapped to the template's
  logical ids through the ``aws:cloudformation:logical-id`` tags on the groups);
  when the template already declares it, the revoke was out-of-band and the PR
  says so, naming the CloudTrail actor from the ledger;
* for ``ecs.force_redeploy`` there is no code change: the PR carries the
  postmortem only.

Nothing here merges. The patch is produced by code from the same params the
dry run validated; the model only decides *whether* to call the tool, and only
after the engineer says the phrase. GitHub is reached with a fine-grained token
(``contents:write`` + ``pull_requests:write`` on one repository) read from SSM.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from beacon import aws

logger = logging.getLogger(__name__)

_GH = "https://api.github.com"
_TIMEOUT = 15
PHRASE = "open the pull request"
_PHRASES = (
    re.compile(r"\bopen (?:the )?pull request\b"),
    re.compile(r"\bopen (?:the |a )?pr\b"),
    re.compile(r"\bpull request (?:kholo|khol do|banao|bana do)\b"),
)

_token_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def repo() -> str:
    return os.environ.get("FIX_PR_REPO", "").strip()


def base_branch() -> str:
    return os.environ.get("FIX_PR_BASE", "main").strip() or "main"


def template_path() -> str:
    return os.environ.get("FIX_PR_TEMPLATE_PATH", "demo/demo-infra-template.yaml")


def configured() -> bool:
    return bool(repo() and token())


def token() -> str:
    direct = os.environ.get("GITHUB_PR_TOKEN", "")
    if direct:
        return direct
    param = os.environ.get("FIX_PR_TOKEN_PARAM", "")
    if not param:
        return ""
    if param not in _token_cache:
        try:
            _token_cache[param] = str(
                aws.client("ssm").get_parameter(Name=param, WithDecryption=True)[
                    "Parameter"
                ]["Value"]
            )
        except Exception:
            logger.exception("fix-pr token %s unreadable", param)
            return ""
    return _token_cache[param]


def said_phrase(transcript: str) -> bool:
    spoken = re.sub(r"[^a-z0-9\s]", " ", transcript.lower())
    spoken = re.sub(r"\s+", " ", spoken)
    return any(p.search(spoken) for p in _PHRASES)


# ---------------------------------------------------------------------------
# GitHub REST (urllib; no SDK)
# ---------------------------------------------------------------------------


def gh(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{_GH}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "authorization": f"Bearer {token()}",
            "accept": "application/vnd.github+json",
            "x-github-api-version": "2022-11-28",
            "content-type": "application/json",
            "user-agent": "beacon-night-shift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            raw = resp.read().decode()
            return dict(json.loads(raw)) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        raise RuntimeError(f"github {method} {path}: {exc.code} {detail}") from exc


def _get_file(path: str, ref: str) -> tuple[str, str] | None:
    """(text, blob sha) of a file at *ref*, or None when it does not exist."""
    try:
        got = gh("GET", f"/repos/{repo()}/contents/{path}?ref={ref}")
    except RuntimeError as exc:
        if " 404 " in str(exc):
            return None
        raise
    return base64.b64decode(str(got.get("content", ""))).decode(), str(got.get("sha"))


def _put_file(
    path: str, text: str, *, branch: str, message: str, sha: str | None
) -> None:
    body: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(text.encode()).decode(),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    gh("PUT", f"/repos/{repo()}/contents/{path}", body)


# ---------------------------------------------------------------------------
# The deterministic patch
# ---------------------------------------------------------------------------


def logical_ids(group_ids: list[str]) -> dict[str, str]:
    """Physical security-group id → CloudFormation logical id, from the tags
    CloudFormation puts on every resource it creates."""
    if not group_ids:
        return {}
    try:
        resp = aws.client("ec2").describe_security_groups(GroupIds=group_ids)
    except Exception:
        logger.exception("describe_security_groups failed")
        return {}
    out: dict[str, str] = {}
    for sg in resp.get("SecurityGroups", []):
        tags = {t["Key"]: t["Value"] for t in sg.get("Tags", [])}
        if "aws:cloudformation:logical-id" in tags:
            out[str(sg["GroupId"])] = str(tags["aws:cloudformation:logical-id"])
    return out


def _cfn_load(text: str) -> dict[str, Any]:
    """Parse a CloudFormation template; intrinsic tags become {"!Tag": value}."""
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    def any_tag(loader: yaml.SafeLoader, suffix: str, node: yaml.Node) -> Any:
        if isinstance(node, yaml.ScalarNode):
            return {f"!{suffix}": loader.construct_scalar(node)}
        if isinstance(node, yaml.SequenceNode):
            return {f"!{suffix}": loader.construct_sequence(node)}
        if isinstance(node, yaml.MappingNode):
            return {f"!{suffix}": loader.construct_mapping(node)}
        return {f"!{suffix}": None}

    Loader.add_multi_constructor("!", any_tag)
    loaded = yaml.load(text, Loader=Loader)  # noqa: S506 - SafeLoader subclass
    return dict(loaded) if isinstance(loaded, dict) else {}


def _refers(value: Any, logical: str) -> bool:
    if isinstance(value, dict):
        return value.get("!Ref") == logical or any(
            _refers(v, logical) for v in value.values()
        )
    if isinstance(value, list):
        return any(_refers(v, logical) for v in value)
    return bool(value == logical)


def template_declares_rule(
    text: str, *, group_logical: str, source_logical: str, port: int, protocol: str
) -> bool:
    """True when the template already has this ingress, inline or standalone."""
    resources = _cfn_load(text).get("Resources") or {}
    for name, res in resources.items():
        if not isinstance(res, dict):
            continue
        props = res.get("Properties") or {}
        rtype = res.get("Type")
        if rtype == "AWS::EC2::SecurityGroup" and name == group_logical:
            for rule in props.get("SecurityGroupIngress") or []:
                if (
                    str(rule.get("IpProtocol", "tcp")) == protocol
                    and int(rule.get("FromPort", -1)) == port
                    and _refers(rule.get("SourceSecurityGroupId"), source_logical)
                ):
                    return True
        if (
            rtype == "AWS::EC2::SecurityGroupIngress"
            and _refers(props.get("GroupId"), group_logical)
            and str(props.get("IpProtocol", "tcp")) == protocol
            and int(props.get("FromPort", -1)) == port
            and _refers(props.get("SourceSecurityGroupId"), source_logical)
        ):
            return True
    return False


def ingress_resource_text(
    *,
    group_logical: str,
    source_logical: str,
    port: int,
    protocol: str,
    incident_id: str,
) -> str:
    name = f"Beacon{group_logical}Restored{port}Ingress"
    return (
        f"\n  # Restored by Beacon after incident {incident_id}: the rule that was\n"
        f"  # missing at runtime, declared explicitly so drift is visible in review.\n"
        f"  {name}:\n"
        f"    Type: AWS::EC2::SecurityGroupIngress\n"
        f"    Properties:\n"
        f"      GroupId: !Ref {group_logical}\n"
        f"      IpProtocol: {protocol}\n"
        f"      FromPort: {port}\n"
        f"      ToPort: {port}\n"
        f"      SourceSecurityGroupId: !Ref {source_logical}\n"
    )


def patch_template(text: str, resource_text: str) -> str:
    """Insert the resource at the end of the Resources section (before Outputs)."""
    m = re.search(r"^Outputs:\s*$", text, flags=re.MULTILINE)
    if m:
        head, tail = text[: m.start()], text[m.start() :]
        return head.rstrip("\n") + "\n" + resource_text + "\n" + tail
    return text.rstrip("\n") + "\n" + resource_text


def plan_patch(
    *, action: str, params: dict[str, Any], incident_id: str, template_text: str | None
) -> dict[str, Any]:
    """What the PR will change, decided by code from the dry-run params."""
    if action != "sg.restore_ingress" or template_text is None:
        return {
            "kind": "postmortem_only",
            "reason": (
                "no template in the repository"
                if template_text is None
                else f"{action} has no code change"
            ),
        }
    group, source = str(params.get("group_id")), str(params.get("source_group_id"))
    ids = logical_ids([g for g in (group, source) if g])
    if group not in ids or source not in ids:
        return {
            "kind": "postmortem_only",
            "reason": "security groups are not CloudFormation-managed",
        }
    port = int(params.get("from_port", 0))
    protocol = str(params.get("ip_protocol", "tcp"))
    if template_declares_rule(
        template_text,
        group_logical=ids[group],
        source_logical=ids[source],
        port=port,
        protocol=protocol,
    ):
        return {
            "kind": "postmortem_only",
            "reason": (
                "the template already declares the rule; the revoke was out-of-band"
            ),
            "logical": {"group": ids[group], "source": ids[source]},
        }
    resource = ingress_resource_text(
        group_logical=ids[group],
        source_logical=ids[source],
        port=port,
        protocol=protocol,
        incident_id=incident_id,
    )
    return {
        "kind": "template_patch",
        "logical": {"group": ids[group], "source": ids[source]},
        "resource": resource,
        "patched": patch_template(template_text, resource),
    }


# ---------------------------------------------------------------------------
# The pull request
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "incident"


def pr_body(
    incident: dict[str, Any],
    *,
    plan: dict[str, Any],
    approval: dict[str, Any] | None,
    link: str,
) -> str:
    rca = incident.get("rca_json") or {}
    verifies = [
        e for e in incident.get("timeline") or [] if e.get("event") == "verify_attempt"
    ]
    last = (verifies[-1].get("detail") or {}) if verifies else {}
    checks = last.get("checks") or []
    change = (incident.get("changes") or [{}])[0]
    att = (approval or {}).get("attestation") or {}
    lines = [
        f"**Incident** `{incident.get('incident_id')}` on alarm "
        f"`{incident.get('alarm_name')}` — "
        f"status **{incident.get('status')}**.",
        "",
        str(rca.get("summary") or ""),
        "",
        "### What this PR does",
    ]
    if plan["kind"] == "template_patch":
        lines.append(
            f"- Declares the ingress rule that was missing at runtime as an explicit "
            f"`AWS::EC2::SecurityGroupIngress` on `{plan['logical']['group']}` from "
            f"`{plan['logical']['source']}` (the same parameters that passed the "
            "dry run)."
        )
    else:
        lines.append(f"- No template change: {plan.get('reason')}.")
    lines.append("- Adds the postmortem under `docs/incidents/`.")
    lines += ["", "### The approval that applied the runtime fix"]
    if approval:
        lines.append(
            f"> “{approval.get('transcript_quote')}” — via "
            f"**{approval.get('channel')}** at "
            f"{str(approval.get('granted_at', ''))[:19]}Z"
            + (
                f", STT confidence {int(float(att['confidence']) * 100)}%"
                if isinstance(att.get("confidence"), int | float)
                else ""
            )
            + (
                f", AssemblyAI session `{att['session_id']}`"
                if att.get("session_id")
                else ""
            )
        )
    else:
        lines.append("_no approval record_")
    if checks:
        passed = sum(1 for c in checks if c.get("ok"))
        attempt = last.get("attempt")
        lines += [
            "",
            f"### Verification: {passed}/{len(checks)} checks on attempt {attempt}",
        ]
        for c in checks:
            mark = "✅" if c.get("ok") else "❌"
            name = c.get("name") or c.get("check")
            lines.append(f"- {mark} {name}: {c.get('detail') or ''}".rstrip())
    if change:
        lines += [
            "",
            "### The change behind it (CloudTrail)",
            f"`{change.get('event_name')}` by `{change.get('actor_short')}` at "
            f"{change.get('event_time')} on "
            f"{', '.join(str(i) for i in change.get('resource_ids', [])) or '-'}",
        ]
    if link:
        lines += ["", f"Console: {link}"]
    lines += [
        "",
        "_Opened by Beacon Night Shift after the engineer said “open the pull "
        "request”. Nothing is merged by Beacon._",
    ]
    return "\n".join(lines)


def open_pr(
    incident: dict[str, Any],
    *,
    action: str,
    params: dict[str, Any],
    postmortem_md: str,
    approval: dict[str, Any] | None,
    link: str,
) -> dict[str, Any]:
    """Branch, commit the files, open the PR; returns {url, number, files, kind}."""
    if not configured():
        raise RuntimeError("fix-at-source is not configured (FIX_PR_REPO / token)")
    inc_id = str(incident.get("incident_id"))
    short = inc_id[:8]
    alarm = str(incident.get("alarm_name") or "incident")
    day = str(incident.get("timestamp") or datetime.now(tz=UTC).isoformat())[:10]
    branch = f"beacon/incident-{short}"
    base_sha = str(
        gh("GET", f"/repos/{repo()}/git/ref/heads/{base_branch()}")["object"]["sha"]
    )
    try:
        gh(
            "POST",
            f"/repos/{repo()}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
    except RuntimeError as exc:
        if " 422 " not in str(exc):  # already exists: reuse it
            raise

    files: list[str] = []
    tpl = _get_file(template_path(), base_branch())
    plan = plan_patch(
        action=action,
        params=params,
        incident_id=inc_id,
        template_text=tpl[0] if tpl else None,
    )
    if plan["kind"] == "template_patch" and tpl is not None:
        _put_file(
            template_path(),
            plan["patched"],
            branch=branch,
            message=f"Restore ingress rule missing at runtime (Beacon {short})",
            sha=tpl[1],
        )
        files.append(template_path())
    pm_path = f"docs/incidents/{day}-{_slug(alarm)}-{short}.md"
    existing = _get_file(pm_path, branch)
    _put_file(
        pm_path,
        postmortem_md,
        branch=branch,
        message=f"Postmortem for {alarm} (Beacon incident {short})",
        sha=existing[1] if existing else None,
    )
    files.append(pm_path)

    title = (
        f"Restore {plan['logical']['group']} ingress from "
        f"{plan['logical']['source']} (Beacon incident {short})"
        if plan["kind"] == "template_patch"
        else f"Postmortem: {alarm} (Beacon incident {short})"
    )
    pr = gh(
        "POST",
        f"/repos/{repo()}/pulls",
        {
            "title": title,
            "head": branch,
            "base": base_branch(),
            "body": pr_body(incident, plan=plan, approval=approval, link=link),
        },
    )
    return {
        "url": str(pr.get("html_url")),
        "number": int(pr.get("number", 0)),
        "branch": branch,
        "files": files,
        "kind": plan["kind"],
        "reason": plan.get("reason"),
        "title": title,
    }
