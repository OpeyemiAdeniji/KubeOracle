"""Turn a flat list of findings into incidents: what is broken, at which layer, and what probably triggered it."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from kubesift.analysis.changes import argo_manages
from kubesift.cluster.snapshot import Snapshot
from kubesift.models import Category, Change, Finding, Incident, ResourceRef, Severity
from kubesift.util import containers_of, meta, now, pod_spec_of

_LAYER = {
    Category.WORKLOAD: "app", Category.CONFIG: "config", Category.RESOURCES: "app", Category.RELIABILITY: "app",
    Category.NETWORK: "app", Category.STORAGE: "infra", Category.SCHEDULING: "infra", Category.NODE: "infra",
    Category.PLATFORM: "platform", Category.SECURITY: "security", Category.COST: "cost", Category.UPGRADE: "platform",
}


# Findings that describe an effect; the pod-level finding next to them usually names the cause.
SYMPTOMS = {"deployment-unavailable", "statefulset-unready", "daemonset-unavailable", "deployment-rollout-stuck",
            "pod-not-ready"}


def _referenced_config(snap: Snapshot, owner: ResourceRef) -> set[tuple[str, str]]:
    """(kind, name) of ConfigMaps/Secrets the workload's template uses."""
    key = {"Deployment": "deployments", "StatefulSet": "statefulsets", "DaemonSet": "daemonsets",
           "CronJob": "cronjobs", "Job": "jobs", "Pod": "pods"}.get(owner.kind)
    obj = snap.get(key, owner.namespace, owner.name) if key else None
    spec = pod_spec_of(obj) if obj else None
    out: set[tuple[str, str]] = set()
    if not spec:
        return out
    for c in containers_of(spec, include_init=True):
        for ef in c.get("envFrom") or []:
            if ef.get("configMapRef"):
                out.add(("ConfigMap", ef["configMapRef"].get("name", "")))
            if ef.get("secretRef"):
                out.add(("Secret", ef["secretRef"].get("name", "")))
        for env in c.get("env") or []:
            vf = env.get("valueFrom") or {}
            if vf.get("configMapKeyRef"):
                out.add(("ConfigMap", vf["configMapKeyRef"].get("name", "")))
            if vf.get("secretKeyRef"):
                out.add(("Secret", vf["secretKeyRef"].get("name", "")))
    for v in spec.get("volumes") or []:
        if v.get("configMap"):
            out.add(("ConfigMap", v["configMap"].get("name", "")))
        if v.get("secret"):
            out.add(("Secret", v["secret"].get("secretName", "")))
    return out


def _helm_owns(snap: Snapshot, release: ResourceRef, owner: ResourceRef) -> bool:
    for rel in snap.helm_releases:
        if rel["name"] == release.name and rel.get("namespace") == release.namespace:
            return any(r["kind"] == owner.kind and r["name"] == owner.name for r in rel.get("resources") or [])
    return False


def score_change(snap: Snapshot, f: Finding, c: Change, config_refs: set[tuple[str, str]]) -> int:
    """How strongly a change explains a finding: 0 (unrelated) to 100."""
    owner = f.owner or f.resource
    start = f.since or now()
    if c.time > start + timedelta(minutes=5):
        return 0  # changed after the problem began
    gap_h = max((start - c.time).total_seconds() / 3600, 0)
    recency = max(0, 20 - int(gap_h * 5))
    if c.kind == "rollout" and c.resource.kind == owner.kind and c.resource.name == owner.name \
            and c.resource.namespace == owner.namespace:
        return 80 + recency
    if c.kind == "config" and (c.resource.kind, c.resource.name) in config_refs and c.resource.namespace == owner.namespace:
        return 70 + recency
    if c.kind == "helm" and _helm_owns(snap, c.resource, owner):
        return 75 + recency
    if c.kind == "argocd" and argo_manages(snap.get("argoapps", c.resource.namespace, c.resource.name), owner):
        return 75 + recency
    if c.kind == "node" and f.node and c.resource.name == f.node:
        return 70 + recency
    if c.resource.namespace and c.resource.namespace == owner.namespace and c.kind in ("rollout", "config", "helm", "argocd"):
        return 25 + recency
    return 0


def link_changes(snap: Snapshot, findings: list[Finding], changes: list[Change]) -> None:
    cache: dict[str, set[tuple[str, str]]] = {}
    for f in findings:
        if f.kind != "problem":
            continue
        owner = f.owner or f.resource
        refs = cache.setdefault(str(owner), _referenced_config(snap, owner))
        scored = [(score_change(snap, f, c, refs), c) for c in changes]
        scored = [x for x in scored if x[0] >= 60]  # only strong links; weak ones mislead
        scored.sort(key=lambda x: -x[0])
        f.related_changes = [c for _, c in scored[:3]]


def build_incidents(snap: Snapshot, findings: list[Finding]) -> list[Incident]:
    problems = [f for f in findings if f.kind == "problem"]
    groups: dict[str, list[Finding]] = {}

    # Infra first: a broken node explains the pod failures on it.
    bad_nodes = {f.resource.name for f in problems if f.category == Category.NODE and f.resource.kind == "Node"}
    for f in problems:
        if f.category == Category.NODE and f.resource.kind == "Node":
            groups.setdefault(f"node:{f.resource.name}", []).append(f)
    for f in problems:
        if f.category == Category.NODE and f.resource.kind == "Node":
            continue
        if f.node and f.node in bad_nodes:
            groups[f"node:{f.node}"].append(f)
            continue
        groups.setdefault(f"owner:{f.owner or f.resource}", []).append(f)

    # Pods of a Service with no endpoints belong to the same incident as the workload behind it.
    incidents = []
    for key, items in groups.items():
        items.sort(key=lambda f: (f.check in SYMPTOMS, -f.severity.rank))  # causes before symptoms
        top = items[0]
        sev = max((f.severity for f in items), key=lambda s: s.rank)
        triggers = [c for f in items for c in f.related_changes]
        layer = "infra" if key.startswith("node:") else _LAYER.get(top.category, "app")
        if any(f.category == Category.CONFIG for f in items):
            layer = "config"
        root = ResourceRef(kind="Node", name=key[5:]) if key.startswith("node:") else (top.owner or top.resource)
        incidents.append(Incident(
            id=hashlib.sha1(key.encode()).hexdigest()[:8],
            title=f"{root}: {top.title}",
            severity=sev,
            layer=layer,
            root=root,
            findings=items,
            likely_trigger=triggers[0] if triggers else None,
            summary=_summary(items, triggers[0] if triggers else None),
        ))
    incidents.sort(key=lambda i: (-i.severity.rank, i.likely_trigger is None, -len(i.findings)))
    return incidents


def _summary(items: list[Finding], trigger: Change | None) -> str:
    text = items[0].message
    if len(items) > 1:
        text += f" (+{len(items) - 1} related finding{'s' if len(items) > 2 else ''})"
    if trigger:
        text += f" Likely trigger: {trigger.summary} at {trigger.time:%H:%M UTC}."
    return text


def system_blast_radius(findings: list[Finding]) -> list[str]:
    """Short notes on findings that affect the whole cluster."""
    notes = []
    for f in findings:
        if f.category == Category.PLATFORM and f.severity in (Severity.CRITICAL, Severity.HIGH):
            notes.append(f"{f.title}: {f.message}")
    return notes


def pod_counts(snap: Snapshot) -> dict[str, Any]:
    pods = snap.all("pods")
    running = sum(1 for p in pods if (p.get("status") or {}).get("phase") == "Running")
    return {"pods": len(pods), "running": running, "nodes": len(snap.all("nodes")),
            "namespaces": len({meta(p).get("namespace") for p in pods})}
