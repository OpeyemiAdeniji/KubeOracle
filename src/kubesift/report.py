"""Output formats: terminal (rich), Markdown (tickets, PRs, postmortems), JSON and SARIF (code scanning)."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kubesift import __version__
from kubesift.models import Change, Diagnosis, Finding, Report, Severity

COLORS = {"critical": "bold red", "high": "red", "medium": "yellow", "low": "cyan", "info": "dim"}


def sev(s: Severity) -> Text:
    return Text(s.value.upper(), style=COLORS[s.value])


def _hanging(marker: str, text: Text, indent: int = 0, style: str = "") -> Padding:
    """`marker text` where wrapped lines line up under the text, not under the marker or the panel edge."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)
    grid.add_column(overflow="fold")
    grid.add_row(Text(marker, style=style), text)
    return Padding(grid, (0, 0, 0, indent))


# ---------------------------------------------------------------- terminal
def diagnosis_panel(d: Diagnosis, max_causes: int = 5) -> Panel:
    body: list[Any] = [Text(d.summary)]
    for i, rc in enumerate(d.root_causes[:max_causes], 1):
        body.append(Text(""))
        body.append(_hanging(f"{i}.", Text.assemble((rc.title, "bold"), (f"  [{rc.confidence}% confidence]", "dim"))))
        body.append(Padding(Text(rc.explanation), (0, 0, 0, 3)))
        for s in rc.fix_steps[:4]:
            body.append(_hanging("→", Text(s, style="green"), indent=3, style="green"))
        for c in rc.commands[:3]:
            body.append(_hanging("$", Text(c, style="cyan"), indent=3, style="cyan"))
    if d.next_checks:
        body.append(Text("\nNext checks: " + "; ".join(d.next_checks[:4]), style="dim"))
    return Panel(Group(*body), title=f"Diagnosis ({d.generated_by})", border_style="magenta")


def print_report(console: Console, report: Report, show_risks: bool = False, verbose: bool = False) -> None:
    counts = report.counts()
    problems = [f for f in report.findings if f.kind == "problem"]
    risks = [f for f in report.findings if f.kind == "risk"]
    header = Text.assemble(("KubeSift ", "bold"), (f"{report.cluster}", "bold cyan"),
                           (f"  ns={report.namespace or 'all'}  k8s={report.server_version or '?'}", "dim"))
    console.print(header)
    if report.question:
        console.print(Text(f"Q: {report.question}", style="bold"))

    if report.diagnosis:
        console.print(diagnosis_panel(report.diagnosis))

    if problems:
        t = Table(title="Problems", show_lines=False, expand=True)
        t.add_column("Sev", no_wrap=True)
        t.add_column("Resource", overflow="fold", ratio=2)
        t.add_column("What is wrong", ratio=5)
        for f in problems:
            msg = Text(f.message)
            if f.related_changes:
                msg.append(f"\n↳ after: {f.related_changes[0].summary}", style="magenta")
            if verbose:
                for e in f.evidence[:6]:
                    msg.append(f"\n  • {e}", style="dim")
                for n in f.notes[:2]:
                    msg.append(f"\n  📝 {n}", style="blue")
                if f.remediation:
                    msg.append(f"\n  fix: {f.remediation}", style="green")
                for c in f.commands[:2]:
                    msg.append(f"\n  $ {c}", style="cyan")
            t.add_row(sev(f.severity), str(f.owner or f.resource), msg)
        console.print(t)
    else:
        console.print(Text("✓ No active problems found.", style="bold green"))

    if risks and show_risks:
        t = Table(title="Risks (will hurt later)", expand=True)
        t.add_column("Sev", no_wrap=True)
        t.add_column("Check", no_wrap=True)
        t.add_column("Resource", overflow="fold")
        t.add_column("Detail", ratio=3)
        for f in risks:
            t.add_row(sev(f.severity), f.check, str(f.resource), f.message)
        console.print(t)
    elif risks:
        by_check: dict[str, int] = {}
        for f in risks:
            by_check[f.check] = by_check.get(f.check, 0) + 1
        top = ", ".join(f"{k} ×{v}" for k, v in sorted(by_check.items(), key=lambda x: -x[1])[:6])
        console.print(Text(f"{len(risks)} configuration risk(s): {top}. Run with --risks to list them.", style="dim"))

    if report.changes and verbose:
        print_changes(console, report.changes[:10], title="Recent changes")
    for w in report.warnings:
        console.print(Text(f"! {w}", style="yellow"))
    console.print(Text(f"critical={counts['critical']} high={counts['high']} medium={counts['medium']} "
                       f"low={counts['low']}", style="dim"))


def print_changes(console: Console, changes: list[Change], title: str = "Changes") -> None:
    if not changes:
        console.print(Text("No changes in this window.", style="dim"))
        return
    t = Table(title=title, expand=True)
    t.add_column("When (UTC)", no_wrap=True)
    t.add_column("Type", no_wrap=True)
    t.add_column("What changed", ratio=4)
    for c in changes:
        detail = Text(c.summary)
        for d in c.details[:4]:
            detail.append(f"\n  {d}", style="dim")
        if c.rollback_command:
            detail.append(f"\n  undo: {c.rollback_command}", style="cyan")
        t.add_row(c.time.strftime("%m-%d %H:%M"), c.kind, detail)
    console.print(t)


def print_findings(console: Console, findings: list[Finding], title: str = "Findings") -> None:
    if not findings:
        console.print(Text("✓ No findings.", style="bold green"))
        return
    t = Table(title=title, expand=True)
    t.add_column("Sev", no_wrap=True)
    t.add_column("Check", no_wrap=True)
    t.add_column("Resource", overflow="fold")
    t.add_column("Detail", ratio=3)
    for f in findings:
        where = str(f.resource) + (f"\n{f.source}" if f.source else "")
        detail = Text(f.message)
        if f.remediation:
            detail.append(f"\nfix: {f.remediation}", style="green")
        t.add_row(sev(f.severity), f.check, where, detail)
    console.print(t)


# ---------------------------------------------------------------- markdown
def to_markdown(report: Report) -> str:
    lines = [f"# KubeSift report: {report.cluster}", "",
             f"- Namespace: `{report.namespace or 'all'}`", f"- Kubernetes: `{report.server_version or 'unknown'}`",
             f"- Collected: {report.collected_at:%Y-%m-%d %H:%M UTC}", ""]
    if report.question:
        lines += [f"**Question:** {report.question}", ""]
    d = report.diagnosis
    if d:
        lines += ["## Diagnosis", "", d.summary, ""]
        for i, rc in enumerate(d.root_causes, 1):
            lines += [f"### {i}. {rc.title} ({rc.confidence}% confidence)", "", rc.explanation, ""]
            lines += [f"- {s}" for s in rc.fix_steps]
            if rc.commands:
                lines += ["", "```bash", *rc.commands, "```"]
            lines.append("")
    problems = [f for f in report.findings if f.kind == "problem"]
    risks = [f for f in report.findings if f.kind == "risk"]
    if problems:
        lines += ["## Problems", "", "| Severity | Resource | Finding |", "|---|---|---|"]
        lines += [f"| {f.severity.value} | `{f.owner or f.resource}` | {f.message.replace('|', '/')} |" for f in problems]
        lines.append("")
    if report.changes:
        lines += ["## Recent changes", ""]
        lines += [f"- {c.time:%m-%d %H:%M} **{c.kind}** {c.summary}" for c in report.changes[:15]]
        lines.append("")
    if risks:
        lines += ["## Risks", "", "| Severity | Check | Resource | Detail |", "|---|---|---|---|"]
        lines += [f"| {f.severity.value} | {f.check} | `{f.resource}` | {f.message.replace('|', '/')} |" for f in risks]
        lines.append("")
    if report.warnings:
        lines += ["## Data gaps", ""] + [f"- {w}" for w in report.warnings] + [""]
    lines.append(f"_Generated by KubeSift {__version__}_")
    return "\n".join(lines)


# ------------------------------------------------------------------- SARIF
_SARIF_LEVEL = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}


def to_sarif(findings: list[Finding]) -> str:
    from kubesift.checks import REGISTRY

    used = sorted({f.check for f in findings})
    rules = [{"id": c, "name": c, "shortDescription": {"text": REGISTRY[c].title},
              "fullDescription": {"text": REGISTRY[c].description or REGISTRY[c].title},
              "helpUri": f"https://github.com/OpeyemiAdeniji/KubeSift/blob/main/docs/checks.md#{c}",
              "defaultConfiguration": {"level": _SARIF_LEVEL[REGISTRY[c].severity.value]}} for c in used]
    results = []
    for f in findings:
        uri = (f.source or "manifest").split("#")[0]
        results.append({
            "ruleId": f.check, "level": _SARIF_LEVEL[f.severity.value],
            "message": {"text": f"{f.resource}: {f.message} {f.remediation}".strip()},
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri.replace("\\", "/")},
                                                "region": {"startLine": 1}}}],
            "partialFingerprints": {"kubesift/v1": f.fingerprint},
        })
    doc = {"version": "2.1.0", "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
           "runs": [{"tool": {"driver": {"name": "KubeSift", "version": __version__,
                                         "informationUri": "https://github.com/OpeyemiAdeniji/KubeSift", "rules": rules}},
                     "results": results}]}
    return json.dumps(doc, indent=2)
