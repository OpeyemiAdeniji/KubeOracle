"""What changed recently? Most incidents follow a change, so this is the first question to answer."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from kubesift.cluster.snapshot import Snapshot, event_time
from kubesift.models import Change, ResourceRef
from kubesift.util import containers_of, meta, now, parse_time

_CONTROLLERS = ("kube-controller-manager", "kubelet", "kube-scheduler", "cert-manager-certificates-issuing")


def _ref(obj: dict[str, Any]) -> ResourceRef:
    return ResourceRef(kind=obj.get("kind", "?"), name=meta(obj).get("name", ""), namespace=meta(obj).get("namespace"))


def last_user_update(obj: dict[str, Any]) -> tuple[datetime | None, str | None]:
    """Latest spec/data change (ignores status updates and controllers)."""
    best: tuple[datetime | None, str | None] = (None, None)
    for f in meta(obj).get("managedFields") or []:
        if f.get("subresource") == "status" or f.get("manager") in _CONTROLLERS:
            continue
        t = parse_time(f.get("time"))
        if t and (best[0] is None or t > best[0]):
            best = (t, f.get("manager"))
    return best


def _template_diff(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    out: list[str] = []
    o = {c.get("name"): c for c in containers_of(((old.get("spec") or {}).get("template") or {}).get("spec"))}
    n = {c.get("name"): c for c in containers_of(((new.get("spec") or {}).get("template") or {}).get("spec"))}
    for name, nc in n.items():
        oc = o.get(name)
        if oc is None:
            out.append(f"container {name} added")
            continue
        if oc.get("image") != nc.get("image"):
            out.append(f"{name}: image {oc.get('image')} -> {nc.get('image')}")
        if (oc.get("resources") or {}) != (nc.get("resources") or {}):
            out.append(f"{name}: resources {oc.get('resources') or {}} -> {nc.get('resources') or {}}")
        old_env = {e.get("name"): e for e in oc.get("env") or []}
        new_env = {e.get("name"): e for e in nc.get("env") or []}
        added = sorted(set(new_env) - set(old_env))
        removed = sorted(set(old_env) - set(new_env))
        changed = sorted(k for k in set(old_env) & set(new_env) if old_env[k] != new_env[k])
        if added:
            out.append(f"{name}: env added {', '.join(added)}")
        if removed:
            out.append(f"{name}: env removed {', '.join(removed)}")
        if changed:
            out.append(f"{name}: env changed {', '.join(changed)}")
        if (oc.get("envFrom") or []) != (nc.get("envFrom") or []):
            out.append(f"{name}: envFrom changed")
        for field in ("command", "args", "livenessProbe", "readinessProbe", "ports"):
            if oc.get(field) != nc.get(field):
                out.append(f"{name}: {field} changed")
    for name in set(o) - set(n):
        out.append(f"container {name} removed")
    return out


def detect_changes(snap: Snapshot, window: timedelta = timedelta(hours=6)) -> list[Change]:
    cutoff = now() - window
    changes: list[Change] = []

    # Deployment rollouts, from ReplicaSet revisions
    rs_by_owner: dict[tuple[str | None, str], list[dict[str, Any]]] = {}
    for rs in snap.all("replicasets"):
        for o in meta(rs).get("ownerReferences") or []:
            if o.get("kind") == "Deployment":
                rs_by_owner.setdefault((meta(rs).get("namespace"), o.get("name")), []).append(rs)
    for (ns, name), sets in rs_by_owner.items():
        def rev(r: dict[str, Any]) -> int:
            try:
                return int((meta(r).get("annotations") or {}).get("deployment.kubernetes.io/revision", "0"))
            except ValueError:
                return 0
        sets.sort(key=rev)
        newest = sets[-1]
        t = parse_time(meta(newest).get("creationTimestamp"))
        if not t or t < cutoff:
            continue
        details = _template_diff(sets[-2], newest) if len(sets) > 1 else ["first revision"]
        prev = rev(sets[-2]) if len(sets) > 1 else None
        changes.append(Change(
            time=t, kind="rollout", resource=ResourceRef(kind="Deployment", name=name, namespace=ns),
            summary=f"Deployment {name} rolled out revision {rev(newest)}" + (f": {details[0]}" if details else ""),
            details=details,
            rollback_command=f"kubectl rollout undo deployment/{name} -n {ns}" + (f" --to-revision={prev}" if prev else "")
            if prev else None,
        ))

    # Spec/data edits on tracked objects
    for key in ("configmaps", "secrets", "statefulsets", "daemonsets", "cronjobs", "services", "ingresses",
                "networkpolicies", "hpas", "resourcequotas", "deployments"):
        for obj in snap.all(key):
            if key == "secrets" and obj.get("type") == "helm.sh/release.v1":
                continue
            t, manager = last_user_update(obj)
            created = parse_time(meta(obj).get("creationTimestamp"))
            if not t or t < cutoff:
                continue
            if key == "deployments" and any(c.resource.name == meta(obj).get("name") and c.kind == "rollout"
                                            and abs((c.time - t).total_seconds()) < 120 for c in changes):
                continue  # already covered by the rollout entry
            verb = "created" if created and abs((t - created).total_seconds()) < 5 else "updated"
            kind = "config" if key in ("configmaps", "secrets") else "scale" if key == "hpas" else "other"
            summary = f"{obj.get('kind')} {meta(obj).get('name')} {verb}" + (f" by {manager}" if manager else "")
            details = []
            if kind == "config" and verb == "updated":
                details.append("Pods that read it through env vars keep the old values until they restart.")
            changes.append(Change(time=t, kind=kind, resource=_ref(obj), summary=summary, details=details))

    # Helm releases
    for rel in snap.helm_releases:
        t = parse_time(rel.get("updated"))
        if not t or t < cutoff:
            continue
        r = rel.get("revision", 0)
        changes.append(Change(
            time=t, kind="helm", resource=ResourceRef(kind="HelmRelease", name=rel["name"], namespace=rel.get("namespace")),
            summary=f"Helm release {rel['name']} -> revision {r} ({rel.get('chart', '?')}), status {rel.get('status')}",
            details=[f"{x['kind']}/{x['name']}" for x in (rel.get("resources") or [])[:15]],
            rollback_command=f"helm rollback {rel['name']} {r - 1} -n {rel.get('namespace')}" if r > 1 else None,
        ))

    # Argo CD syncs
    for app in snap.all("argoapps"):
        op = (app.get("status") or {}).get("operationState") or {}
        t = parse_time(op.get("finishedAt") or op.get("startedAt"))
        if not t or t < cutoff:
            continue
        git_rev = ((op.get("syncResult") or {}).get("revision") or "")[:10]
        changes.append(Change(time=t, kind="argocd", resource=_ref(app),
                              summary=f"Argo CD synced {meta(app).get('name')} to {git_rev or 'a new revision'} "
                                      f"({op.get('phase')})",
                              details=[op.get("message", "")[:200]] if op.get("message") else []))

    # Nodes joining
    for node in snap.all("nodes"):
        t = parse_time(meta(node).get("creationTimestamp"))
        if t and t >= cutoff:
            changes.append(Change(time=t, kind="node", resource=_ref(node), summary=f"Node {meta(node).get('name')} joined"))

    # Scaling and node events
    for e in snap.all("events"):
        reason = e.get("reason")
        if reason not in ("SuccessfulRescale", "NodeNotReady", "RemovingNode", "TaintManagerEviction", "Rebooted"):
            continue
        t = parse_time(event_time(e))
        if not t or t < cutoff:
            continue
        inv = e.get("involvedObject") or {}
        changes.append(Change(time=t, kind="scale" if reason == "SuccessfulRescale" else "node",
                              resource=ResourceRef(kind=inv.get("kind", "?"), name=inv.get("name", ""),
                                                   namespace=inv.get("namespace") or None),
                              summary=f"{reason}: {(e.get('message') or '')[:160]}"))

    changes.sort(key=lambda c: c.time, reverse=True)
    if snap.namespace:
        changes = [c for c in changes if c.resource.namespace in (snap.namespace, None)]
    return changes
