"""Slack on-call: the `/sift` slash command and interactive fix buttons.

Requests are authenticated with Slack's signing secret (HMAC-SHA256 over `v0:timestamp:body`, rejected after
5 minutes). Fix buttons always run a server-side dry run first; applying needs allow_writes and, if set,
the Slack user to be listed in slack_allowed_users.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

HELP = ("*KubeSift on Slack*\n"
        "`/sift diagnose [namespace]` — what is broken and why\n"
        "`/sift why <workload> [namespace]` — focused answer about one workload\n"
        "`/sift changes [namespace] [30m|2h|1d]` — what changed recently\n"
        "`/sift postmortem [namespace]` — draft incident postmortem\n"
        "`/sift help`")

_SEV = {"critical": ":red_circle:", "high": ":large_orange_circle:", "medium": ":large_yellow_circle:",
        "low": ":white_circle:", "info": ":information_source:"}


def verify(secret: str | None, timestamp: str | None, body: bytes, signature: str | None,
           max_age: int = 300) -> bool:
    if not secret or not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > max_age:
            return False  # replay protection
    except ValueError:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse(text: str) -> tuple[str, list[str]]:
    parts = (text or "").strip().split()
    if not parts:
        return "help", []
    return parts[0].lower(), parts[1:]


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}


def report_blocks(report: Any, title: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{"type": "header", "text": {"type": "plain_text", "text": title[:150]}}]
    if report.diagnosis:
        blocks.append(_section(report.diagnosis.summary))
    for inc in report.incidents[:5]:
        top = inc.findings[0]
        text = f"{_SEV[inc.severity.value]} *{inc.title}*\n{top.message[:500]}"
        if inc.likely_trigger:
            text += f"\n:arrows_counterclockwise: After: {inc.likely_trigger.summary[:200]}"
        if top.commands:
            text += f"\n`{top.commands[0][:180]}`"
        block = _section(text)
        rb = inc.likely_trigger.rollback_command if inc.likely_trigger else None
        if rb and inc.likely_trigger and inc.likely_trigger.resource.kind == "Deployment":
            r = inc.likely_trigger.resource
            block["accessory"] = {"type": "button", "text": {"type": "plain_text", "text": "Roll back (dry run)"},
                                  "action_id": "sift_fix",
                                  "value": json.dumps({"action": "rollback", "kind": "deployment",
                                                       "namespace": r.namespace, "name": r.name, "dry_run": True})}
        blocks.append(block)
    if not report.incidents:
        blocks.append(_section(":white_check_mark: No active problems found."))
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                                                    "text": f"cluster `{report.cluster}` · namespace `{report.namespace or 'all'}`"}]})
    return blocks


def changes_blocks(changes: list[Any], scope: str, since: str) -> list[dict[str, Any]]:
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"Changes in {scope}, last {since}"}}]
    if not changes:
        blocks.append(_section("No changes in this window."))
    for c in changes[:12]:
        text = f"*{c.time:%H:%M}* `{c.kind}` {c.summary[:250]}"
        if c.rollback_command:
            text += f"\n`{c.rollback_command}`"
        blocks.append(_section(text))
    return blocks


def fix_result_blocks(plan_text: str, kubectl: str, result: str, value: dict[str, Any], can_apply: bool) -> list[dict[str, Any]]:
    mode = "Dry run" if value.get("dry_run") else "Applied"
    blocks: list[dict[str, Any]] = [_section(f"*{mode}:* {plan_text}\n`{kubectl}`\nResult: {result}")]
    if value.get("dry_run") and can_apply:
        apply = dict(value, dry_run=False)
        blocks.append({"type": "actions", "elements": [{
            "type": "button", "style": "danger", "text": {"type": "plain_text", "text": "Apply for real"},
            "action_id": "sift_fix", "value": json.dumps(apply),
            "confirm": {"title": {"type": "plain_text", "text": "Change the cluster?"},
                        "text": {"type": "plain_text", "text": plan_text[:250]},
                        "confirm": {"type": "plain_text", "text": "Apply"},
                        "deny": {"type": "plain_text", "text": "Cancel"}}}]})
    elif value.get("dry_run"):
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                                                        "text": "Applying from Slack is off (allow_writes / slack_allowed_users)."}]})
    return blocks
