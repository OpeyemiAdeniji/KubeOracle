"""Automatic postmortems: timeline, impact, root cause, resolution and action items, ready to share.

Built from facts KubeSift already has (findings, changes, warning events, the fix audit log), so the draft
is accurate and blameless. With an LLM configured, the summary paragraph is rewritten for readability.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from kubesift import __version__
from kubesift.analysis.llm import LLMError, get_provider
from kubesift.cluster.snapshot import Snapshot, event_time
from kubesift.config import Settings
from kubesift.models import Report
from kubesift.util import human_duration, now, parse_time


def _affected(report: Report) -> list[str]:
    owners = []
    for inc in report.incidents:
        name = str(inc.root)
        if name not in owners:
            owners.append(name)
    return owners


def build(report: Report, snap: Snapshot | None = None, audit: list[dict[str, Any]] | None = None,
          settings: Settings | None = None, title: str | None = None) -> dict[str, Any]:
    problems = [f for f in report.findings if f.kind == "problem"]
    starts = [f.since for f in problems if f.since]
    start = min(starts) if starts else None
    trigger = next((i.likely_trigger for i in report.incidents if i.likely_trigger), None)
    if trigger and (start is None or trigger.time < start):
        start = trigger.time

    timeline: list[tuple[datetime, str, str]] = []
    for c in report.changes:
        if start is None or c.time >= start - timedelta(hours=1):
            timeline.append((c.time, "change", c.summary))
    for f in problems:
        if f.since:
            timeline.append((f.since, "symptom", f"{f.owner or f.resource}: {f.title}"))
    if snap is not None:
        for e in snap.all("events"):
            t = parse_time(event_time(e))
            if e.get("type") == "Warning" and t and (start is None or t >= start):
                inv = e.get("involvedObject") or {}
                timeline.append((t, "event", f"{inv.get('kind')}/{inv.get('name')}: {e.get('reason')} — "
                                             f"{(e.get('message') or '')[:140]}"))
    resolved_at = None
    for a in audit or []:
        t = parse_time(a.get("created"))
        if t and (start is None or t >= start) and not a.get("dry_run"):
            timeline.append((t, "action", f"{a.get('action')} {a.get('target')} by {a.get('actor') or 'operator'}: "
                                          f"{a.get('result')}"))
            resolved_at = t
    timeline.sort(key=lambda x: x[0])
    # keep the timeline readable: collapse repeated events
    seen, compact = set(), []
    for t, kind, text in timeline:
        k = (kind, text[:80])
        if k in seen and kind == "event":
            continue
        seen.add(k)
        compact.append((t, kind, text))

    d = report.diagnosis
    root = d.root_causes[0] if d and d.root_causes else None
    actions: list[str] = []
    for f in problems[:6]:
        if f.remediation and f.remediation not in actions:
            actions.append(f.remediation)
    affected = set(_affected(report))
    for f in report.findings:
        if f.kind == "risk" and str(f.owner or f.resource) in affected and f.remediation not in actions:
            actions.append(f"{f.title} on {f.owner or f.resource}: {f.remediation}")
    if trigger:
        actions.append("Add a preflight check to the pipeline (`kubesift preflight`) so this kind of change is caught "
                       "before deploy.")

    summary = d.summary if d else "No active problems were found."
    if settings is not None and problems:
        summary = _polish(settings, report, summary) or summary
    end = resolved_at or now()
    return {
        "title": title or (f"Incident: {d.root_causes[0].title}" if root else f"Incident review: {report.cluster}"),
        "cluster": report.cluster, "namespace": report.namespace or "all",
        "status": "resolved" if resolved_at else "ongoing",
        "start": start.isoformat() if start else None, "end": end.isoformat(),
        "duration": human_duration(end - start) if start else "unknown",
        "summary": summary,
        "impact": [f"{inc.root}: {inc.findings[0].message}" for inc in report.incidents[:8]],
        "root_cause": {"title": root.title, "explanation": root.explanation, "confidence": root.confidence} if root else None,
        "trigger": {"time": trigger.time.isoformat(), "summary": trigger.summary, "details": trigger.details,
                    "rollback": trigger.rollback_command} if trigger else None,
        "timeline": [{"time": t.isoformat(), "type": k, "text": x} for t, k, x in compact[:60]],
        "actions": actions[:12],
    }


def _polish(settings: Settings, report: Report, fallback: str) -> str | None:
    try:
        provider = get_provider(settings)
    except LLMError:
        return None
    if provider is None:
        return None
    schema = {"type": "object", "additionalProperties": False, "required": ["summary"],
              "properties": {"summary": {"type": "string"}}}
    facts = "\n".join(f"- {i.title}: {i.summary}" for i in report.incidents[:8])
    try:
        out = provider.complete_json(
            "You write blameless incident postmortem summaries. Use only the facts given. Three to five sentences: "
            "what happened, the impact, the cause, how it was or can be fixed. No speculation.",
            f"Facts:\n{facts}\n\nCurrent draft: {fallback}", schema)
        return str(out.get("summary") or "") or None
    except (LLMError, KeyError, TypeError, ValueError):
        return None


def to_markdown(pm: dict[str, Any]) -> str:
    lines = [f"# {pm['title']}", "",
             f"| | |\n|---|---|\n| Status | {pm['status']} |\n| Cluster | `{pm['cluster']}` |\n"
             f"| Namespace | `{pm['namespace']}` |\n| Started | {pm['start'] or 'unknown'} |\n| Duration | {pm['duration']} |",
             "", "## Summary", "", pm["summary"], ""]
    if pm["impact"]:
        lines += ["## Impact", ""] + [f"- {x}" for x in pm["impact"]] + [""]
    if pm["root_cause"]:
        rc = pm["root_cause"]
        lines += ["## Root cause", "", f"**{rc['title']}** ({rc['confidence']}% confidence)", "", rc["explanation"], ""]
    if pm["trigger"]:
        t = pm["trigger"]
        lines += ["## Triggering change", "", f"{t['time'][:16].replace('T', ' ')} UTC — {t['summary']}"]
        lines += [f"- {x}" for x in t["details"][:6]]
        if t["rollback"]:
            lines += ["", "```bash", t["rollback"], "```"]
        lines.append("")
    if pm["timeline"]:
        lines += ["## Timeline (UTC)", "", "| Time | Type | What happened |", "|---|---|---|"]
        lines += [f"| {e['time'][11:16]} | {e['type']} | {e['text'].replace('|', '/')} |" for e in pm["timeline"]]
        lines.append("")
    if pm["actions"]:
        lines += ["## Action items", ""] + [f"- [ ] {a}" for a in pm["actions"]] + [""]
    lines += ["## What went well / what to improve", "", "- _Fill in with the team._", "",
              f"_Draft generated by KubeSift {__version__} from cluster evidence. Review before sharing._"]
    return "\n".join(lines)


def affected_namespaces(report: Report) -> list[str]:
    return sorted({(f.owner or f.resource).namespace for f in report.findings if (f.owner or f.resource).namespace})
