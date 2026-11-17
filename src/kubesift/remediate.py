"""Guarded fixes. Every action has a plan, a server-side dry run, an explicit confirmation and an audit entry.

KubeSift is read-only unless you run `kubesift fix ... --yes` or enable allow_writes on the server.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from kubesift.cluster.client import KubeClient, KubeError
from kubesift.store import Store
from kubesift.util import now

_PATHS = {
    "deployment": "/apis/apps/v1/namespaces/{ns}/deployments/{name}",
    "statefulset": "/apis/apps/v1/namespaces/{ns}/statefulsets/{name}",
    "daemonset": "/apis/apps/v1/namespaces/{ns}/daemonsets/{name}",
}
ACTIONS = ("restart", "rollback", "scale", "delete-pod")


@dataclass
class Plan:
    action: str
    target: str
    description: str
    kubectl: str
    method: str
    path: str
    body: Any = None
    content_type: str | None = None


def plan(client: KubeClient, action: str, kind: str, namespace: str, name: str, replicas: int | None = None,
         to_revision: int | None = None) -> Plan:
    from kubesift.util import check_name

    if not namespace or not name:
        raise ValueError("namespace and name are required")
    check_name(namespace, "namespace")
    check_name(name)
    kind = kind.lower()
    target = f"{kind}/{namespace}/{name}"
    if action == "delete-pod":
        return Plan(action, f"pod/{namespace}/{name}", "Delete the pod so its controller recreates it.",
                    f"kubectl delete pod {name} -n {namespace}", "DELETE", f"/api/v1/namespaces/{namespace}/pods/{name}")
    if kind not in _PATHS:
        raise ValueError(f"{action} supports deployment, statefulset, daemonset (got {kind})")
    path = _PATHS[kind].format(ns=namespace, name=name)
    if action == "restart":
        body = {"spec": {"template": {"metadata": {"annotations": {
            "kubectl.kubernetes.io/restartedAt": now().isoformat()}}}}}
        return Plan(action, target, "Rolling restart: replace every pod, respecting the rollout strategy.",
                    f"kubectl rollout restart {kind}/{name} -n {namespace}", "PATCH", path, body,
                    "application/strategic-merge-patch+json")
    if action == "scale":
        if replicas is None or replicas < 0:
            raise ValueError("scale needs --replicas >= 0")
        return Plan(action, target, f"Set replicas to {replicas}.",
                    f"kubectl scale {kind}/{name} -n {namespace} --replicas={replicas}", "PATCH",
                    path + "/scale", {"spec": {"replicas": replicas}}, "application/merge-patch+json")
    if action == "rollback":
        if kind != "deployment":
            raise ValueError("rollback is supported for deployments")
        dep = client.get(path)
        sets = client.get(f"/apis/apps/v1/namespaces/{namespace}/replicasets").get("items") or []
        uid = (dep.get("metadata") or {}).get("uid")
        owned = [rs for rs in sets if any(o.get("uid") == uid for o in (rs.get("metadata") or {}).get("ownerReferences") or [])]

        def rev(rs: dict[str, Any]) -> int:
            return int(((rs.get("metadata") or {}).get("annotations") or {}).get("deployment.kubernetes.io/revision", "0"))
        owned.sort(key=rev)
        if len(owned) < 2:
            raise ValueError("no previous revision to roll back to")
        current = owned[-1]
        if to_revision is not None:
            prev = next((rs for rs in owned if rev(rs) == to_revision), None)
            if prev is None or prev is current:
                kept = ", ".join(str(rev(rs)) for rs in owned[:-1])
                raise ValueError(f"revision {to_revision} is not a previous revision (kept: {kept})")
        else:
            # The previous *version*: skip revisions that only restarted the current one (`rollout restart`),
            # otherwise "roll back" would bring back the same broken pods.
            from kubesift.util import template_version
            prev = next((rs for rs in reversed(owned[:-1]) if template_version(rs) != template_version(current)), None)
            if prev is None:
                raise ValueError("every earlier revision is only a restart of the current version; "
                                 "there is no different version to roll back to")
        template = json.loads(json.dumps((prev.get("spec") or {}).get("template") or {}))
        labels = (template.get("metadata") or {}).get("labels") or {}
        labels.pop("pod-template-hash", None)
        body = [{"op": "replace", "path": "/spec/template", "value": template}]
        return Plan(action, target, f"Roll back to revision {rev(prev)} (current {rev(owned[-1])}).",
                    f"kubectl rollout undo deployment/{name} -n {namespace} --to-revision={rev(prev)}",
                    "PATCH", path, body, "application/json-patch+json")
    raise ValueError(f"unknown action {action}; choose from {', '.join(ACTIONS)}")


def execute(client: KubeClient, p: Plan, dry_run: bool, store: Store | None = None, actor: str | None = None) -> str:
    query = {"dryRun": "All"} if dry_run else None
    try:
        client.request(p.method, p.path, query=query, body=p.body, content_type=p.content_type)
        result = "dry run succeeded" if dry_run else "applied"
    except KubeError as exc:
        result = f"failed: {exc.message}"
        if store:
            store.audit(p.action, p.target, dry_run, result, actor)
        raise
    if store:
        store.audit(p.action, p.target, dry_run, result, actor)
    return result
