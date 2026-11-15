"""Team runbooks: YAML files that attach your own steps and links to matching findings.

Example (runbooks/oom.yaml):

    - title: Payments service OOM playbook
      match: {check: pod-oomkilled, namespace: payments}
      url: https://wiki.example.com/payments/oom
      steps:
        - Check the heap dashboard
        - Page #payments-oncall if it repeats
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from kubesift.models import Finding


def load(directory: str | None) -> list[dict[str, Any]]:
    if not directory:
        return []
    path = Path(directory).expanduser()
    if not path.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for f in sorted(path.glob("*.y*ml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or []
        out.extend(x for x in (data if isinstance(data, list) else [data]) if isinstance(x, dict))
    return out


def _matches(rule: dict[str, Any], f: Finding) -> bool:
    m = rule.get("match") or {}
    owner = f.owner or f.resource
    if m.get("check") and m["check"] != f.check:
        return False
    if m.get("category") and m["category"] != f.category.value:
        return False
    if m.get("namespace") and m["namespace"] != owner.namespace:
        return False
    return not (m.get("name") and not re.search(m["name"], owner.name or ""))


def attach(runbooks: list[dict[str, Any]], findings: list[Finding]) -> None:
    for f in findings:
        for rb in runbooks:
            if _matches(rb, f):
                text = f"Runbook: {rb.get('title', 'untitled')}"
                if rb.get("url"):
                    text += f" ({rb['url']})"
                if rb.get("steps"):
                    text += " — " + "; ".join(str(s) for s in rb["steps"][:6])
                f.notes.append(text)
