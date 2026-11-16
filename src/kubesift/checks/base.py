"""Check registry. A check is a function Snapshot -> iterable of Findings."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from kubesift.cluster.snapshot import Snapshot
from kubesift.config import Settings
from kubesift.models import Category, Finding, ResourceRef, Severity
from kubesift.util import meta

log = logging.getLogger(__name__)

CheckFn = Callable[["Context"], Iterable[Finding]]
DOCS_URL = "https://github.com/OpeyemiAdeniji/KubeSift/blob/main/docs/checks.md"


@dataclass
class CheckInfo:
    id: str
    title: str
    category: Category
    severity: Severity
    live_only: bool
    description: str
    fn: CheckFn
    risk: bool = False


REGISTRY: dict[str, CheckInfo] = {}


def check(
    id: str,
    title: str,
    category: Category,
    severity: Severity,
    live_only: bool = True,
    description: str = "",
    risk: bool = False,
) -> Callable[[CheckFn], CheckFn]:
    """Register a check. `live_only` checks need runtime status and are skipped when linting files."""

    def wrap(fn: CheckFn) -> CheckFn:
        REGISTRY[id] = CheckInfo(id, title, category, severity, live_only,
                                 description or " ".join((fn.__doc__ or "").split()), fn, risk)
        return fn

    return wrap


@dataclass
class Context:
    snap: Snapshot
    settings: Settings
    target_version: str | None = None

    def is_system(self, namespace: str | None) -> bool:
        return namespace in self.settings.system_namespaces

    def finding(
        self,
        check_id: str,
        obj: dict[str, Any] | ResourceRef,
        message: str,
        *,
        severity: Severity | None = None,
        title: str | None = None,
        evidence: list[str] | None = None,
        remediation: str = "",
        commands: list[str] | None = None,
        docs: str | None = None,
        **extra: Any,
    ) -> Finding:
        info = REGISTRY[check_id]
        if isinstance(obj, ResourceRef):
            ref = obj
            owner = obj
            source = None
        else:
            m = meta(obj)
            ref = ResourceRef(kind=obj.get("kind", "?"), name=m.get("name", "?"), namespace=m.get("namespace"))
            owner = self.snap.owner_of(obj) if self.snap.live else ref
            source = (m.get("annotations") or {}).get("kubesift.dev/source")
        return Finding(
            check=check_id,
            title=title or info.title,
            severity=severity or info.severity,
            category=info.category,
            kind="risk" if info.risk else "problem",
            resource=ref,
            owner=owner,
            message=message,
            evidence=evidence or [],
            remediation=remediation,
            commands=commands or [],
            docs=docs or f"{DOCS_URL}#{check_id}",
            source=source,
            **extra,
        )


def run_checks(
    snap: Snapshot,
    settings: Settings,
    only: Iterable[str] | None = None,
    categories: Iterable[Category] | None = None,
    target_version: str | None = None,
) -> list[Finding]:
    from kubesift import checks as _  # noqa: F401  (registers all checks)

    ctx = Context(snap=snap, settings=settings, target_version=target_version)
    only_set = set(only) if only else None
    cat_set = set(categories) if categories else None
    findings: list[Finding] = []
    for info in REGISTRY.values():
        if only_set is not None and info.id not in only_set:
            continue
        if cat_set is not None and info.category not in cat_set:
            continue
        if info.live_only and not snap.live:
            continue
        try:
            findings.extend(info.fn(ctx))
        except Exception as exc:  # one broken check must never break the scan
            log.warning("check %s failed: %s", info.id, exc, exc_info=True)
            snap.warnings.append(f"Check {info.id} failed: {exc}")
    if snap.namespace:
        findings = [f for f in findings if f.resource.namespace in (snap.namespace, None)]
    return dedupe(findings)


def dedupe(findings: list[Finding]) -> list[Finding]:
    """Collapse identical findings on pods of the same workload into one, keeping the count."""
    seen: dict[tuple[str, str], Finding] = {}
    for f in findings:
        key = (f.check, str(f.owner or f.resource))
        prev = seen.get(key)
        if prev is None:
            seen[key] = f
            continue
        keep, drop = (f, prev) if f.severity.rank > prev.severity.rank else (prev, f)
        note = f"Also affects {drop.resource}"
        if str(drop.resource) != str(keep.resource) and note not in keep.evidence and len(keep.evidence) < 12:
            keep.evidence.append(note)
        seen[key] = keep
    return sorted(seen.values(), key=lambda f: (-f.severity.rank, f.category.value, str(f.resource)))
