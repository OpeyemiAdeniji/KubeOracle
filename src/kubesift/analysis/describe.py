"""Compact, LLM-friendly summaries of individual resources."""

from __future__ import annotations

from typing import Any

from kubesift.cluster.snapshot import KIND_TO_KEY, Snapshot, event_time
from kubesift.util import age, containers_of, human_duration, meta, pod_spec_of

ALIASES = {
    "po": "Pod", "pod": "Pod", "pods": "Pod", "deploy": "Deployment", "deployment": "Deployment",
    "deployments": "Deployment", "sts": "StatefulSet", "statefulset": "StatefulSet", "ds": "DaemonSet",
    "daemonset": "DaemonSet", "svc": "Service", "service": "Service", "ing": "Ingress", "ingress": "Ingress",
    "job": "Job", "cj": "CronJob", "cronjob": "CronJob", "no": "Node", "node": "Node", "pvc": "PersistentVolumeClaim",
    "hpa": "HorizontalPodAutoscaler", "cm": "ConfigMap", "configmap": "ConfigMap", "secret": "Secret",
}


def resolve(snap: Snapshot, kind: str, namespace: str | None, name: str) -> dict[str, Any] | None:
    k = ALIASES.get(kind.lower(), kind)
    key = KIND_TO_KEY.get(k)
    return snap.get(key, namespace, name) if key else None


def summarize(snap: Snapshot, obj: dict[str, Any], events: int = 8) -> dict[str, Any]:
    kind = obj.get("kind", "")
    m = meta(obj)
    out: dict[str, Any] = {"kind": kind, "name": m.get("name"), "namespace": m.get("namespace"),
                           "age": human_duration(age(m.get("creationTimestamp"))),
                           "labels": m.get("labels") or {}}
    st = obj.get("status") or {}
    spec = obj.get("spec") or {}
    if kind == "Pod":
        out["phase"] = st.get("phase")
        out["node"] = spec.get("nodeName")
        out["containers"] = []
        for cs in (st.get("initContainerStatuses") or []) + (st.get("containerStatuses") or []):
            state = cs.get("state") or {}
            sname = next(iter(state), "unknown")
            detail = state.get(sname) or {}
            last = (cs.get("lastState") or {}).get("terminated") or {}
            out["containers"].append({
                "name": cs.get("name"), "image": cs.get("image"), "ready": cs.get("ready"),
                "restarts": cs.get("restartCount"), "state": sname, "reason": detail.get("reason"),
                "last_exit": {"code": last.get("exitCode"), "reason": last.get("reason")} if last else None,
            })
        out["owner"] = str(snap.owner_of(obj))
    elif kind in ("Deployment", "StatefulSet"):
        out["replicas"] = {"desired": spec.get("replicas"), "ready": st.get("readyReplicas", 0),
                           "updated": st.get("updatedReplicas", 0), "available": st.get("availableReplicas", 0)}
        out["conditions"] = [{k: c.get(k) for k in ("type", "status", "reason", "message")} for c in st.get("conditions") or []]
    elif kind == "DaemonSet":
        out["scheduled"] = {"desired": st.get("desiredNumberScheduled"), "ready": st.get("numberReady")}
    elif kind == "Service":
        out["type"] = spec.get("type")
        out["selector"] = spec.get("selector")
        out["ports"] = spec.get("ports")
    elif kind == "Node":
        out["conditions"] = [{k: c.get(k) for k in ("type", "status", "reason")} for c in st.get("conditions") or []]
        out["allocatable"] = st.get("allocatable")
        out["version"] = (st.get("nodeInfo") or {}).get("kubeletVersion")
        out["unschedulable"] = spec.get("unschedulable", False)
    elif kind in ("ConfigMap", "Secret"):
        out["keys"] = obj.get("keys")
    else:
        out["status"] = {k: v for k, v in st.items() if k in ("phase", "conditions", "loadBalancer", "succeeded", "failed")}
    pspec = pod_spec_of(obj)
    if pspec:
        out["template"] = [{
            "name": c.get("name"), "image": c.get("image"), "resources": c.get("resources"),
            "env": [e.get("name") for e in c.get("env") or []],
            "envFrom": c.get("envFrom"), "probes": {p: bool(c.get(p)) for p in ("livenessProbe", "readinessProbe", "startupProbe")},
            "ports": c.get("ports"),
        } for c in containers_of(pspec, include_init=True)]
    evs = snap.events_for(kind, m.get("namespace"), m.get("name", ""))[:events]
    if evs:
        out["events"] = [f"{event_time(e)} {e.get('type')} {e.get('reason')}: {(e.get('message') or '')[:200]}" for e in evs]
    if kind == "Pod":
        logs = {k.rsplit("/", 1)[-1]: v for k, v in snap.logs.items()
                if k.startswith(f"{m.get('namespace')}/{m.get('name')}/")}
        if logs:
            out["logs"] = logs
    return out
