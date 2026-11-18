"""The incident map: what a broken workload or Service is connected to, and which link is broken.

Ingress -> Service -> workload -> Pods -> Node, plus the ConfigMaps, Secrets, volumes and autoscaler it uses. Each
node and edge has a status (ok | warn | bad) and a short reason, so the dashboard can draw the broken link in red.
"""

from __future__ import annotations

from typing import Any

from kubesift.checks.config_refs import _refs
from kubesift.cluster.snapshot import Snapshot
from kubesift.util import meta, name_of, pod_spec_of, selector_matches

_WORKLOAD_KEYS = {"Deployment": "deployments", "StatefulSet": "statefulsets", "DaemonSet": "daemonsets"}
MAX_PODS = 6


def _id(kind: str, name: str) -> str:
    return f"{kind}/{name}"


def _pod_status(pod: dict[str, Any]) -> tuple[str, str]:
    st = pod.get("status") or {}
    if (pod.get("metadata") or {}).get("deletionTimestamp"):
        return "warn", "terminating"
    for cs in st.get("containerStatuses") or []:
        waiting = (cs.get("state") or {}).get("waiting") or {}
        if waiting.get("reason"):
            restarts = cs.get("restartCount") or 0
            return "bad", waiting["reason"] + (f", {restarts} restarts" if restarts else "")
        last = (cs.get("lastState") or {}).get("terminated") or {}
        if last.get("reason") == "OOMKilled":
            return "bad", "OOMKilled"
    if st.get("phase") == "Pending":
        return "bad", "Pending"
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in st.get("conditions") or [])
    return ("ok", "ready") if ready else ("warn", "not ready")


def _ready_endpoints(snap: Snapshot, ns: str | None, svc: str) -> int | None:
    if not snap.can_read("endpointslices"):
        return None
    return sum(1 for es in snap.in_namespace("endpointslices", ns)
               if (meta(es).get("labels") or {}).get("kubernetes.io/service-name") == svc
               for ep in es.get("endpoints") or [] if (ep.get("conditions") or {}).get("ready") is not False)


def _ingress_backends(ing: dict[str, Any]) -> set[str]:
    spec = ing.get("spec") or {}
    out = set()
    default = ((spec.get("defaultBackend") or {}).get("service") or {}).get("name")
    if default:
        out.add(default)
    for rule in spec.get("rules") or []:
        for path in ((rule.get("http") or {}).get("paths")) or []:
            name = ((path.get("backend") or {}).get("service") or {}).get("name")
            if name:
                out.add(name)
    return out


def build_map(snap: Snapshot, kind: str, namespace: str, name: str) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def node(kind: str, name: str, status: str, detail: str = "", column: int = 0) -> str:
        nid = _id(kind, name)
        nodes.setdefault(nid, {"id": nid, "kind": kind, "name": name, "status": status, "detail": detail,
                               "column": column})
        return nid

    def edge(a: str, b: str, status: str = "ok", label: str = "") -> None:
        edges.append({"from": a, "to": b, "status": status, "label": label})

    services = snap.in_namespace("services", namespace)
    ingresses = snap.in_namespace("ingresses", namespace)

    def add_service(svc: dict[str, Any], to: str | None) -> str:
        sname = name_of(svc)
        ready = _ready_endpoints(snap, namespace, sname)
        status = "ok" if ready else ("bad" if ready == 0 else "warn")
        detail = f"{ready} ready endpoint(s)" if ready is not None else "endpoints not readable"
        sid = node("Service", sname, status, detail, 1)
        if to:
            edge(sid, to, "ok" if ready else "bad", "" if ready else "no ready endpoints")
        for ing in ingresses:
            if sname in _ingress_backends(ing):
                edge(node("Ingress", name_of(ing), "ok", ",".join(
                    r.get("host", "*") for r in (ing.get("spec") or {}).get("rules") or [])[:60], 0), sid)
        return sid

    if kind == "Service":
        svc = snap.get("services", namespace, name)
        if svc is None:
            return {"nodes": [], "edges": [], "error": f"Service {namespace}/{name} not found"}
        selector = (svc.get("spec") or {}).get("selector") or {}
        sid = add_service(svc, None)
        matched = False
        for w in snap.workloads():
            tlabels = ((((w.get("spec") or {}).get("template")) or {}).get("metadata") or {}).get("labels") or {}
            if meta(w).get("namespace") == namespace and w.get("kind") in _WORKLOAD_KEYS \
                    and selector_matches(selector, tlabels):
                matched = True
                edge(sid, node(w["kind"], name_of(w), "ok", "", 2), "ok" if nodes[sid]["status"] == "ok" else "bad")
        if not matched:
            text = ",".join(f"{k}={v}" for k, v in sorted(selector.items())) or "(no selector)"
            edge(sid, node("Pods", f"no pods match {text}", "bad", "the selector matches nothing", 2), "bad",
                 "selector matches no pods")
        return {"nodes": list(nodes.values()), "edges": edges}

    if kind == "Pod":
        pod = snap.get("pods", namespace, name)
        if pod is None:
            return {"nodes": [], "edges": [], "error": f"Pod {namespace}/{name} not found"}
        owner = snap.owner_of(pod)
        if owner.kind in _WORKLOAD_KEYS:
            kind, name = owner.kind, owner.name
    key = _WORKLOAD_KEYS.get(kind)
    workload = snap.get(key, namespace, name) if key else None
    if workload is None:
        return {"nodes": [], "edges": [], "error": f"{kind} {namespace}/{name} not found"}

    spec = workload.get("spec") or {}
    st = workload.get("status") or {}
    want, ready = spec.get("replicas", 1), st.get("readyReplicas") or st.get("numberReady") or 0
    if kind == "DaemonSet":
        want = st.get("desiredNumberScheduled") or 0
    wid = node(kind, name, "ok" if ready >= want else ("bad" if ready == 0 and want else "warn"),
               f"{ready}/{want} ready", 2)

    # Services (and their Ingresses) that send traffic to this workload
    tlabels = ((spec.get("template") or {}).get("metadata") or {}).get("labels") or {}
    for svc in services:
        if selector_matches((svc.get("spec") or {}).get("selector") or {}, tlabels):
            add_service(svc, wid)

    # Pods and their nodes
    pods = [p for p in snap.in_namespace("pods", namespace) if snap.owner_of(p).name == name
            and snap.owner_of(p).kind == kind]
    pods.sort(key=lambda p: _pod_status(p)[0] != "bad")  # broken ones first
    for p in pods[:MAX_PODS]:
        status, detail = _pod_status(p)
        pid = node("Pod", name_of(p), status, detail, 3)
        edge(wid, pid, "ok" if status == "ok" else status)
        nname = (p.get("spec") or {}).get("nodeName")
        if nname:
            n = snap.get("nodes", None, nname)
            nready = n is None or any(c.get("type") == "Ready" and c.get("status") == "True"
                                      for c in (n.get("status") or {}).get("conditions") or [])
            edge(pid, node("Node", nname, "ok" if nready else "bad", "" if nready else "NotReady", 4),
                 "ok" if nready else "bad")
    if len(pods) > MAX_PODS:
        edge(wid, node("Pod", f"+{len(pods) - MAX_PODS} more", "ok", "", 3))

    # What the pod template depends on
    pspec = pod_spec_of(workload) or {}
    for rkind, rname, _key, where in _refs(pspec):
        rkey = "configmaps" if rkind == "ConfigMap" else "secrets"
        if not snap.can_read(rkey):
            status, detail = "warn", "not readable"
        elif snap.get(rkey, namespace, rname) is None:
            status, detail = "bad", "missing"
        else:
            status, detail = "ok", where
        edge(wid, node(rkind, rname, status, detail, 4), status, "missing" if status == "bad" else "")
    for v in pspec.get("volumes") or []:
        claim = (v.get("persistentVolumeClaim") or {}).get("claimName")
        if claim:
            pvc = snap.get("pvcs", namespace, claim)
            phase = ((pvc or {}).get("status") or {}).get("phase") if pvc else "missing"
            status = "ok" if phase == "Bound" else "bad"
            edge(wid, node("PVC", claim, status, phase or "", 4), status)
    for hpa in snap.in_namespace("hpas", namespace):
        ref = (hpa.get("spec") or {}).get("scaleTargetRef") or {}
        if ref.get("kind") == kind and ref.get("name") == name:
            edge(node("HPA", name_of(hpa), "ok", "", 1), wid)
    return {"nodes": list(nodes.values()), "edges": edges}
