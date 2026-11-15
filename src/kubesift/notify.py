"""Send findings to Slack, Microsoft Teams or any webhook."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from kubesift.config import Settings
from kubesift.models import Finding, Report

log = logging.getLogger(__name__)
_EMOJI = {"critical": ":red_circle:", "high": ":large_orange_circle:", "medium": ":large_yellow_circle:",
          "low": ":white_circle:", "info": ":information_source:"}


def _slack_blocks(title: str, report: Report, findings: list[Finding]) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = [{"type": "header", "text": {"type": "plain_text", "text": title[:150]}}]
    if report.diagnosis and report.diagnosis.summary:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": report.diagnosis.summary[:2900]}})
    for f in findings[:8]:
        text = f"{_EMOJI[f.severity.value]} *{f.title}* — `{f.owner or f.resource}`\n{f.message[:500]}"
        if f.related_changes:
            text += f"\n:arrows_counterclockwise: After: {f.related_changes[0].summary[:200]}"
        if f.commands:
            text += f"\n`{f.commands[0][:200]}`"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
    if len(findings) > 8:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"+{len(findings) - 8} more findings"}]})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                                                    "text": f"cluster `{report.cluster}` · KubeSift"}]})
    return {"text": title, "blocks": blocks}


def _teams_card(title: str, report: Report, findings: list[Finding]) -> dict[str, Any]:
    body: list[dict[str, Any]] = [{"type": "TextBlock", "size": "Large", "weight": "Bolder", "text": title, "wrap": True}]
    if report.diagnosis:
        body.append({"type": "TextBlock", "text": report.diagnosis.summary, "wrap": True})
    for f in findings[:8]:
        body.append({"type": "TextBlock", "wrap": True,
                     "text": f"**[{f.severity.value.upper()}] {f.title}** — {f.owner or f.resource}\n\n{f.message[:500]}"})
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                    "version": "1.4", "body": body}}]}


def send(settings: Settings, report: Report, findings: list[Finding], title: str | None = None) -> list[str]:
    """Returns the list of channels that were notified."""
    if not findings:
        return []
    worst = findings[0].severity.value.upper()
    title = title or f"[{worst}] {len(findings)} new Kubernetes problem(s) in {report.cluster}"
    sent = []
    targets = [
        (settings.slack_webhook_url, lambda: _slack_blocks(title, report, findings), "slack"),
        (settings.teams_webhook_url, lambda: _teams_card(title, report, findings), "teams"),
        (settings.webhook_url, lambda: {"title": title, "report": report.model_dump(mode="json"),
                                        "new_findings": [f.fingerprint for f in findings]}, "webhook"),
    ]
    with httpx.Client(timeout=15) as http:
        for url, payload, name in targets:
            if not url:
                continue
            try:
                http.post(url, json=payload()).raise_for_status()
                sent.append(name)
            except httpx.HTTPError as exc:
                log.warning("notification to %s failed: %s", name, exc)
    return sent
