"""Did the fix work? Wait for the rollout to finish, watch the new pods settle, then re-run the checks.

Used after `kubesift fix`, from Slack after "Apply", by the MCP `verify_fix` tool and by `kubesift verify` (which
also works after a change made by hand with kubectl or a GitOps sync).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from kubesift.cluster.client import KubeClient, KubeError
from kubesift.config import Settings
from kubesift.util import check_name

KINDS = {"deployment": "deployments", "deploy": "deployments", "statefulset": "statefulsets", "sts": "statefulsets",
         "daemonset": "daemonsets", "ds": "daemonsets", "pod": "pods", "po": "pods"}
_PATH = "/apis/apps/v1/namespaces/{ns}/{plural}/{name}"
_TITLE = {"deployments": "Deployment", "statefulsets": "StatefulSet", "daemonsets": "DaemonSet"}

RESOLVED, FAILING, ROLLING, UNKNOWN = "resolved", "still-failing", "rolling-out", "unknown"


@dataclass
class Verification:
    status: str                      # resolved | still-failing | rolling-out | unknown
    target: str                      # Deployment/shop/checkout
    summary: str
    problems: list[str] = field(default_factory=list)
    waited_seconds: float = 0.0
    commands: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == RESOLVED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"ok": self.ok}


def rollout_state(plural: str, obj: dict[str, Any]) -> tuple[bool, bool, str]:
    """(finished, failed, detail) from the controller's own status, like `kubectl rollout status`."""
    meta, spec, st = obj.get("metadata") or {}, obj.get("spec") or {}, obj.get("status") or {}
    if (st.get("observedGeneration") or 0) < (meta.get("generation") or 0):
        return False, False, "the controller has not picked up the change yet"
    if plural == "deployments":
        want = spec.get("replicas", 1)
        updated, available, total = (st.get("updatedReplicas") or 0, st.get("availableReplicas") or 0,
                                      st.get("replicas") or 0)
        for c in st.get("conditions") or []:
            if c.get("type") == "Progressing" and c.get("reason") == "ProgressDeadlineExceeded":
                return True, True, f"the rollout exceeded its progress deadline ({updated}/{want} updated)"
        detail = f"{updated}/{want} updated, {available} available"
        return updated >= want and available >= want and total == want, False, detail
    if plural == "statefulsets":
        want = spec.get("replicas", 1)
        updated, ready = st.get("updatedReplicas") or 0, st.get("readyReplicas") or 0
        same_revision = not st.get("updateRevision") or st.get("currentRevision") == st.get("updateRevision")
        return (want == 0 or (updated >= want and same_revision)) and ready >= want, False, \
            f"{updated}/{want} updated, {ready} ready"
    desired = st.get("desiredNumberScheduled") or 0
    updated, available = st.get("updatedNumberScheduled") or 0, st.get("numberAvailable") or 0
    return updated >= desired and available >= desired, False, f"{updated}/{desired} updated, {available} available"


def _pods(client: KubeClient, ns: str, obj: dict[str, Any]) -> list[dict[str, Any]]:
    labels = ((obj.get("spec") or {}).get("selector") or {}).get("matchLabels") or {}
    if not labels:
        return []
    selector = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
    return client.get(f"/api/v1/namespaces/{ns}/pods", labelSelector=selector).get("items") or []


def _restarts(pods: list[dict[str, Any]]) -> dict[str, int]:
    return {(p.get("metadata") or {}).get("name", ""): sum(c.get("restartCount") or 0
                                                          for c in (p.get("status") or {}).get("containerStatuses") or [])
            for p in pods}


def workload_of_pod(client: KubeClient, namespace: str, pod: str) -> tuple[str, str]:
    """The controller to verify after deleting a pod (ReplicaSet -> Deployment)."""
    obj = client.get(f"/api/v1/namespaces/{namespace}/pods/{pod}")
    refs = (obj.get("metadata") or {}).get("ownerReferences") or []
    ref = next((r for r in refs if r.get("controller")), None)
    if not ref:
        raise ValueError(f"pod {pod} has no controller, so nothing recreates it; there is no workload to verify")
    kind, name = ref.get("kind", ""), ref.get("name", "")
    if kind == "ReplicaSet":
        rs = client.get(f"/apis/apps/v1/namespaces/{namespace}/replicasets/{name}")
        parent = next((r for r in (rs.get("metadata") or {}).get("ownerReferences") or [] if r.get("controller")), None)
        if parent and parent.get("kind") == "Deployment":
            return "deployment", parent["name"]
    if kind.lower() not in ("statefulset", "daemonset", "deployment"):
        raise ValueError(f"pod {pod} is owned by a {kind}; verify supports Deployments, StatefulSets and DaemonSets")
    return kind.lower(), name


def verify(settings: Settings, client: KubeClient, kind: str, namespace: str, name: str, *,
           timeout: float = 180, settle: float = 20, interval: float = 3,
           progress: Callable[[str], None] | None = None,
           sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Verification:
    """Wait (up to `timeout`) for the workload to roll out, watch its pods for `settle` seconds, then re-check it."""
    from kubesift.checks import run_checks
    from kubesift.cluster.snapshot import collect

    check_name(namespace, "namespace")
    check_name(name)
    plural = KINDS.get(kind.lower())
    if plural is None:
        raise ValueError(f"cannot verify a {kind}; use deployment, statefulset, daemonset or pod")
    if plural == "pods":
        kind, name = workload_of_pod(client, namespace, name)
        plural = KINDS[kind]
    title = _TITLE[plural]
    target = f"{title}/{namespace}/{name}"
    path = _PATH.format(ns=namespace, plural=plural, name=name)
    say = progress or (lambda _msg: None)
    start = clock()

    def waited() -> float:
        return round(clock() - start, 1)

    # 1. The rollout: the controller's own view of "done".
    finished, failed, detail = False, False, ""
    while True:
        try:
            obj = client.get(path)
        except KubeError as exc:
            if exc.status == 404:
                return Verification(UNKNOWN, target, f"{target} no longer exists.", waited_seconds=waited())
            raise
        finished, failed, detail = rollout_state(plural, obj)
        if finished or clock() - start >= timeout:
            break
        say(f"waiting for the rollout: {detail}")
        sleep(interval)

    # 2. Settle: a crash loop often starts a few seconds after a pod first reports ready.
    restarted: list[str] = []
    if finished and not failed and settle > 0:
        say(f"rolled out ({detail}); watching the pods for {int(settle)}s")
        before = _restarts(_pods(client, namespace, obj))
        sleep(settle)
        after = _restarts(_pods(client, namespace, obj))
        restarted = [f"pod {p} restarted {after[p] - before.get(p, 0)} time(s) in the last {int(settle)}s"
                     for p in sorted(after) if p in before and after[p] > before[p]]

    # 3. Re-run the checks on the namespace and keep the problems that belong to this workload.
    say("re-running the checks")
    snap = collect(client, namespace=namespace, with_logs=True)
    # Old pods that are shutting down after the rollout are not the new version: don't judge the fix by them.
    leaving = {(p.get("metadata") or {}).get("name") for p in snap.all("pods")
               if (p.get("metadata") or {}).get("deletionTimestamp")}
    problems = [f for f in run_checks(snap, settings) if f.kind == "problem"
                and not (f.resource.kind == "Pod" and f.resource.name in leaving) and (
        (f.owner and f.owner.kind == title and f.owner.name == name and f.owner.namespace == namespace)
        or (f.resource.kind == title and f.resource.name == name and f.resource.namespace == namespace))]
    lines = restarted + [f"{f.check}: {f.message}" for f in problems]
    commands = [c for f in problems for c in f.commands][:3]
    again = f"kubesift verify {kind}/{name} -n {namespace}"

    if failed or lines:
        why = lines[0] if lines else detail
        return Verification(FAILING, target, f"{target} is still failing: {why}", lines or [detail], waited(),
                            commands)
    if not finished:
        return Verification(ROLLING, target, f"{target} has not finished rolling out after {int(timeout)}s "
                            f"({detail}); no problems found so far. Check again with: {again}", [], waited(), [again])
    return Verification(RESOLVED, target, f"{target} is healthy: {detail}, no restarts while watching, "
                        "and no problems found.", [], waited())
