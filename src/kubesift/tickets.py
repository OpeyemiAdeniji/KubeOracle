"""Turn an incident into a ticket: Markdown anyone can paste, or a GitHub issue (one per incident, no duplicates).

The issue body carries a hidden marker with a stable key (cluster + workload + check). When the same incident is
reported again while its issue is open, KubeSift comments "still happening" on it instead of opening another.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

import httpx

from kubesift import __version__
from kubesift.config import Settings
from kubesift.models import Incident, Report

_SEV_LABEL = {"critical": "severity: critical", "high": "severity: high", "medium": "severity: medium",
              "low": "severity: low", "info": "severity: info"}
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def incident_key(report: Report, inc: Incident) -> str:
    """Stable across scans: same cluster, same workload, same main check."""
    top = inc.findings[0].check if inc.findings else inc.title
    raw = f"{report.cluster}|{inc.root.kind}/{inc.root.namespace}/{inc.root.name}|{top}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _marker(key: str) -> str:
    return f"<!-- kubesift-incident: {key} -->"


def incident_markdown(report: Report, inc: Incident) -> tuple[str, str]:
    """(title, body) for one incident."""
    title = f"[{inc.severity.value}] {inc.title}"
    lines = [f"**{inc.title}**", "",
             f"- Cluster: `{report.cluster}` · namespace: `{inc.root.namespace or 'cluster-wide'}`",
             f"- Severity: **{inc.severity.value}** · layer: {inc.layer}",
             f"- Detected: {report.collected_at.strftime('%Y-%m-%d %H:%M UTC')} by KubeSift {__version__}"]
    if inc.likely_trigger:
        t = inc.likely_trigger
        lines += ["", "### Likely cause", f"{t.summary} ({t.time.strftime('%Y-%m-%d %H:%M UTC')})"]
        lines += [f"- {d}" for d in t.details[:6]]
        if t.rollback_command:
            lines += ["", "Roll back:", "```bash", t.rollback_command, "```"]
    lines += ["", "### What is wrong"]
    for f in inc.findings[:8]:
        lines.append(f"- **{f.check}** on `{f.resource}`: {f.message}")
    evidence = [e for f in inc.findings[:4] for e in f.evidence[:4]]
    if evidence:
        lines += ["", "<details><summary>Evidence</summary>", "", "```", *evidence[:16], "```", "", "</details>"]
    fixes = [f.remediation for f in inc.findings if f.remediation]
    commands = list(dict.fromkeys(c for f in inc.findings for c in f.commands))[:6]
    if fixes or commands:
        lines += ["", "### Fix"]
        lines += [f"- {fx}" for fx in dict.fromkeys(fixes[:4])]
        if commands:
            lines += ["", "```bash", *commands, "```"]
    root = f"{inc.root.kind.lower()}/{inc.root.name}"
    lines += ["", "### After fixing",
              "Check that it worked (waits for the rollout, watches the pods, re-runs the checks):",
              "```bash", f"kubesift verify {root} -n {inc.root.namespace or 'default'}", "```",
              "", _marker(incident_key(report, inc))]
    return title, "\n".join(lines)


class TicketError(Exception):
    pass


def _github(settings: Settings) -> tuple[str, dict[str, str], str]:
    repo = settings.github_repo or ""
    token = settings.github_token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not _REPO.match(repo):
        raise TicketError("set github_repo (KUBESIFT_GITHUB_REPO) to owner/repo")
    if not token:
        raise TicketError("set a GitHub token with issues:write (KUBESIFT_GITHUB_TOKEN or GITHUB_TOKEN)")
    api = (settings.github_api_url or "https://api.github.com").rstrip("/")
    if not api.startswith("https://"):
        raise TicketError("github_api_url must be https://")
    return f"{api}/repos/{repo}", {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "kubesift"}, repo


def open_github_issue(settings: Settings, report: Report, inc: Incident,
                      client: httpx.Client | None = None) -> dict[str, Any]:
    """Create the issue, or comment on the open one for the same incident. Returns url, number, created."""
    base, headers, _ = _github(settings)
    title, body = incident_markdown(report, inc)
    marker = _marker(incident_key(report, inc))
    http = client or httpx.Client(timeout=20, follow_redirects=False)
    try:
        r = http.get(f"{base}/issues", headers=headers, params={"state": "open", "labels": "kubesift", "per_page": 100})
        _raise(r)
        existing = next((i for i in r.json() if marker in (i.get("body") or "") and "pull_request" not in i), None)
        if existing:
            note = (f"Still happening ({report.collected_at.strftime('%Y-%m-%d %H:%M UTC')}): "
                    f"{inc.findings[0].message if inc.findings else inc.title}")
            _raise(http.post(f"{existing['url']}/comments", headers=headers, json={"body": note}))
            return {"url": existing["html_url"], "number": existing["number"], "created": False}
        labels = ["kubesift", "incident", _SEV_LABEL.get(inc.severity.value, "incident")]
        r = http.post(f"{base}/issues", headers=headers, json={"title": title[:250], "body": body, "labels": labels})
        _raise(r)
        issue = r.json()
        return {"url": issue["html_url"], "number": issue["number"], "created": True}
    finally:
        if client is None:
            http.close()


def _raise(r: httpx.Response) -> None:
    if r.status_code >= 400:
        try:
            msg = r.json().get("message", "")
        except ValueError:
            msg = r.text[:200]
        hint = {401: " (token invalid or expired)", 403: " (token lacks issues:write, or rate limited)",
                404: " (repository not found, or the token cannot see it)"}.get(r.status_code, "")
        raise TicketError(f"GitHub answered {r.status_code}: {msg}{hint}")
