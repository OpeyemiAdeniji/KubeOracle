"""Time travel: compact digests of cluster state, stored over time, and the difference between any two moments.

Kubernetes keeps little history (ReplicaSet revisions, managedFields timestamps). A digest records what
matters for troubleshooting — images, replicas, env names, config keys, selectors, node health, pod health
per workload — in a few KB, so the background scanner can keep one every few minutes.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from kubesift.cluster.snapshot import Snapshot
from kubesift.util import containers_of, meta, pod_spec_of


def _h(value: Any) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:10]


def _template(obj: dict[str, Any]) -> dict[str, Any]:
    spec = pod_spec_of(obj) or {}
    out = {}
    for c in containers_of(spec, include_init=True):
        out[c.get("name", "")] = {
            "image": c.get("image"),
            "env": sorted(e.get("name", "") for e in c.get("env") or []),
            "env_hash": _h(c.get("env") or []),
            "envFrom": _h(c.get("envFrom") or []),
            "resources": c.get("resources") or {},
            "probes": _h([c.get(p) for p in ("livenessProbe", "readinessProbe", "startupProbe")]),
            "command": _h([c.get("command"), c.get("args")]),
        }
    return out


def digest(snap: Snapshot) -> dict[str, Any]:
    items: dict[str, dict[str, Any]] = {}
    pods_by_owner: dict[str, dict[str, int]] = defaultdict(lambda: {"pods": 0, "ready": 0, "restarts": 0})
    for p in snap.all("pods"):
        o = pods_by_owner[str(snap.owner_of(p))]
        o["pods"] += 1
        conds = (p.get("status") or {}).get("conditions") or []
        o["ready"] += any(c.get("type") == "Ready" and c.get("status") == "True" for c in conds)
        o["restarts"] += sum(cs.get("restartCount", 0) for cs in (p.get("status") or {}).get("containerStatuses") or [])
    for key in ("deployments", "statefulsets", "daemonsets", "cronjobs"):
        for w in snap.all(key):
            ref = f"{w.get('kind')}/{meta(w).get('namespace')}/{meta(w).get('name')}"
            items[ref] = {"replicas": (w.get("spec") or {}).get("replicas"), "containers": _template(w),
                          "health": pods_by_owner.get(ref, {"pods": 0, "ready": 0, "restarts": 0})}
    for key in ("configmaps", "secrets"):
        for c in snap.all(key):
            if c.get("type") == "helm.sh/release.v1":
                continue
            mf = [f.get("time") for f in meta(c).get("managedFields") or [] if f.get("time")]
            items[f"{c.get('kind')}/{meta(c).get('namespace')}/{meta(c).get('name')}"] = {
                "keys": c.get("keys") or [], "updated": max(mf) if mf else meta(c).get("creationTimestamp")}
    for s in snap.all("services"):
        spec = s.get("spec") or {}
        items[f"Service/{meta(s).get('namespace')}/{meta(s).get('name')}"] = {
            "type": spec.get("type"), "selector": spec.get("selector"),
            "ports": [(p.get("port"), str(p.get("targetPort"))) for p in spec.get("ports") or []]}
    for i in snap.all("ingresses"):
        rules = [(r.get("host"), [(p.get("path"), ((p.get("backend") or {}).get("service") or {}).get("name"))
                                  for p in (r.get("http") or {}).get("paths") or []])
                 for r in (i.get("spec") or {}).get("rules") or []]
        items[f"Ingress/{meta(i).get('namespace')}/{meta(i).get('name')}"] = {"rules": rules}
    for n in snap.all("nodes"):
        st = n.get("status") or {}
        items[f"Node/{meta(n).get('name')}"] = {
            "ready": next((c.get("status") for c in st.get("conditions") or [] if c.get("type") == "Ready"), None),
            "unschedulable": bool((n.get("spec") or {}).get("unschedulable")),
            "version": (st.get("nodeInfo") or {}).get("kubeletVersion"),
            "taints": sorted(t.get("key", "") for t in (n.get("spec") or {}).get("taints") or [])}
    for h in snap.all("hpas"):
        spec, st = h.get("spec") or {}, h.get("status") or {}
        items[f"HorizontalPodAutoscaler/{meta(h).get('namespace')}/{meta(h).get('name')}"] = {
            "min": spec.get("minReplicas"), "max": spec.get("maxReplicas"), "current": st.get("currentReplicas")}
    for key in ("networkpolicies", "pdbs"):
        for o in snap.all(key):
            items[f"{o.get('kind')}/{meta(o).get('namespace')}/{meta(o).get('name')}"] = {"spec": _h(o.get("spec"))}
    for rel in snap.helm_releases:
        items[f"HelmRelease/{rel.get('namespace')}/{rel.get('name')}"] = {
            "revision": rel.get("revision"), "chart": rel.get("chart"), "status": rel.get("status")}
    return {"v": 1, "cluster": snap.context, "namespace": snap.namespace,
            "time": snap.collected_at.isoformat(), "server_version": snap.server_version, "items": items}


def _describe(key: str, old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for field in sorted(set(old) | set(new)):
        a, b = old.get(field), new.get(field)
        if a == b:
            continue
        if field == "containers":
            for c in sorted(set(a or {}) | set(b or {})):
                ca, cb = (a or {}).get(c), (b or {}).get(c)
                if ca is None:
                    out.append(f"container {c} added ({cb.get('image')})")
                elif cb is None:
                    out.append(f"container {c} removed")
                else:
                    if ca["image"] != cb["image"]:
                        out.append(f"{c}: image {ca['image']} -> {cb['image']}")
                    added = sorted(set(cb["env"]) - set(ca["env"]))
                    removed = sorted(set(ca["env"]) - set(cb["env"]))
                    if added:
                        out.append(f"{c}: env added {', '.join(added)}")
                    if removed:
                        out.append(f"{c}: env removed {', '.join(removed)}")
                    if not added and not removed and ca["env_hash"] != cb["env_hash"]:
                        out.append(f"{c}: env values changed")
                    if ca["resources"] != cb["resources"]:
                        out.append(f"{c}: resources {ca['resources']} -> {cb['resources']}")
                    for f2, label in (("envFrom", "envFrom"), ("probes", "probes"), ("command", "command/args")):
                        if ca[f2] != cb[f2]:
                            out.append(f"{c}: {label} changed")
        elif field == "health":
            a, b = a or {}, b or {}
            out.append(f"ready pods {a.get('ready', 0)}/{a.get('pods', 0)} -> {b.get('ready', 0)}/{b.get('pods', 0)}"
                       + (f", +{b.get('restarts', 0) - a.get('restarts', 0)} restarts"
                          if b.get("restarts", 0) > a.get("restarts", 0) else ""))
        elif field == "keys":
            added, removed = sorted(set(b or []) - set(a or [])), sorted(set(a or []) - set(b or []))
            out.append("keys " + ", ".join(x for x in (f"+{','.join(added)}" if added else "",
                                                        f"-{','.join(removed)}" if removed else "") if x)
                       if added or removed else "data changed")
        elif field == "updated":
            if "data changed" not in out and not any(o.startswith("keys") for o in out):
                out.append("data changed")
        else:
            out.append(f"{field}: {a} -> {b}")
    return out


def diff(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    """Changes from digest a (earlier) to digest b (later), most interesting first."""
    ia, ib = a.get("items") or {}, b.get("items") or {}
    out = []
    for key in sorted(set(ia) | set(ib)):
        kind = key.split("/", 1)[0]
        if key not in ia:
            out.append({"resource": key, "kind": kind, "change": "added", "details": []})
        elif key not in ib:
            out.append({"resource": key, "kind": kind, "change": "removed", "details": []})
        elif ia[key] != ib[key]:
            details = _describe(key, ia[key], ib[key])
            if details:
                out.append({"resource": key, "kind": kind, "change": "changed", "details": details})
    order = {"Node": 0, "Deployment": 1, "StatefulSet": 1, "DaemonSet": 1, "ConfigMap": 2, "Secret": 2, "Service": 3,
             "Ingress": 3, "HelmRelease": 4}
    out.sort(key=lambda x: (order.get(x["kind"], 9), x["resource"]))
    return out
