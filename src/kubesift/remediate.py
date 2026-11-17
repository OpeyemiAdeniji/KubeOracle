"""Guarded fixes. Every action has a plan, a server-side dry run, an explicit confirmation and an audit entry.

KubeSift is read-only unless you run `kubesift fix ... --yes` or enable allow_writes on the server.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from kubesift.cluster.client import KubeClient, KubeError
from kubesift.store import Store
from kubesift.util import now

_PATHS = {
    "deployment": "/apis/apps/v1/namespaces/{ns}/deployments/{name}",
    "statefulset": "/apis/apps/v1/namespaces/{ns}/statefulsets/{name}",
    "daemonset": "/apis/apps/v1/namespaces/{ns}/daemonsets/{name}",
}
_ALIASES = {"deploy": "deployment", "deployments": "deployment", "sts": "statefulset", "statefulsets": "statefulset",
            "ds": "daemonset", "daemonsets": "daemonset", "svc": "service", "services": "service",
            "sa": "serviceaccount", "serviceaccounts": "serviceaccount", "po": "pod", "pods": "pod"}
ACTIONS = ("restart", "rollback", "scale", "delete-pod", "set-selector", "set-memory", "grant")
# What `grant` may hand out: namespaced, named verbs and resources only, never wildcards or escalation verbs.
GRANT_VERBS = {"get", "list", "watch", "create", "update", "patch", "delete"}
_LABEL = re.compile(r"^([a-z0-9]([-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?$")
_LABEL_VALUE = re.compile(r"^([A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?)?$")
_MEMORY = re.compile(r"^[0-9]+(\.[0-9]+)?(Ki|Mi|Gi|Ti|k|M|G|T)?$")
_RESOURCE = re.compile(r"^[a-z][a-z0-9-]*(/[a-z]+)?$")


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
    more: list[tuple[str, str, Any, str | None]] = field(default_factory=list)  # further steps, same dry-run rules


def plan(client: KubeClient, action: str, kind: str, namespace: str, name: str, replicas: int | None = None,
         to_revision: int | None = None, *, selector: dict[str, str] | None = None, match: str | None = None,
         container: str | None = None, memory: str | None = None, verbs: list[str] | None = None,
         resource: str | None = None, api_group: str = "") -> Plan:
    from kubesift.util import check_name

    if not namespace or not name:
        raise ValueError("namespace and name are required")
    check_name(namespace, "namespace")
    check_name(name)
    kind = _ALIASES.get(kind.lower(), kind.lower())
    target = f"{kind}/{namespace}/{name}"
    if action == "delete-pod":
        return Plan(action, f"pod/{namespace}/{name}", "Delete the pod so its controller recreates it.",
                    f"kubectl delete pod {name} -n {namespace}", "DELETE", f"/api/v1/namespaces/{namespace}/pods/{name}")
    if action == "set-selector":
        return _plan_selector(client, namespace, name, kind, selector, match)
    if action == "grant":
        return _plan_grant(client, namespace, name, kind, verbs or [], resource or "", api_group)
    if kind not in _PATHS:
        raise ValueError(f"{action} supports deployment, statefulset, daemonset (got {kind})")
    if action == "set-memory":
        return _plan_memory(client, kind, namespace, name, _PATHS[kind].format(ns=namespace, name=name),
                            container, memory)
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
        patch = [{"op": "replace", "path": "/spec/template", "value": template}]
        return Plan(action, target, f"Roll back to revision {rev(prev)} (current {rev(owned[-1])}).",
                    f"kubectl rollout undo deployment/{name} -n {namespace} --to-revision={rev(prev)}",
                    "PATCH", path, patch, "application/json-patch+json")
    raise ValueError(f"unknown action {action}; choose from {', '.join(ACTIONS)}")


def _plan_selector(client: KubeClient, namespace: str, name: str, kind: str, selector: dict[str, str] | None,
                   match: str | None) -> Plan:
    """Point a Service at the right pods (the fix for "Service has no endpoints" caused by a wrong selector)."""
    from kubesift.util import check_name
    if kind not in ("service", "svc"):
        raise ValueError("set-selector works on a service (svc/NAME)")
    if match:  # copy the selector of a workload: --match deploy/web
        mk, _, mname = match.partition("/")
        mkind = {"deploy": "deployment", "sts": "statefulset", "ds": "daemonset"}.get(mk.lower(), mk.lower())
        if mkind not in _PATHS or not mname:
            raise ValueError("--match takes deploy/NAME, sts/NAME or ds/NAME")
        check_name(mname)
        workload = client.get(_PATHS[mkind].format(ns=namespace, name=mname))
        selector = dict(((workload.get("spec") or {}).get("selector") or {}).get("matchLabels") or {})
    if not selector:
        raise ValueError("give the new selector (--selector app=web) or a workload to copy it from (--match deploy/web)")
    for k, v in selector.items():
        if not _LABEL.match(k) or not _LABEL_VALUE.match(v):
            raise ValueError(f"invalid label {k}={v}")
    svc = client.get(f"/api/v1/namespaces/{namespace}/services/{name}")
    old = (svc.get("spec") or {}).get("selector") or {}
    text = ",".join(f"{k}={v}" for k, v in sorted(selector.items()))
    pods = client.get(f"/api/v1/namespaces/{namespace}/pods", labelSelector=text).get("items") or []
    if not pods:
        raise ValueError(f"no pods in {namespace} have the labels {text}; the Service would still have no endpoints")
    old_text = ",".join(f"{k}={v}" for k, v in sorted(old.items())) or "none"
    patch = [{"op": "replace", "path": "/spec/selector", "value": selector}]
    return Plan("set-selector", f"service/{namespace}/{name}",
                f"Change the Service selector from {old_text} to {text} ({len(pods)} pod(s) match).",
                f"kubectl patch service {name} -n {namespace} --type=json -p '{json.dumps(patch)}'",
                "PATCH", f"/api/v1/namespaces/{namespace}/services/{name}", patch, "application/json-patch+json")


def _plan_memory(client: KubeClient, kind: str, namespace: str, name: str, path: str, container: str | None,
                 memory: str | None) -> Plan:
    """Raise a container's memory limit (the fix for OOM kills); the request follows if it would exceed it."""
    from kubesift.util import fmt_bytes, parse_quantity
    if not memory or not _MEMORY.match(memory):
        raise ValueError("set-memory needs --memory, e.g. 512Mi or 1Gi")
    workload = client.get(path)
    containers = ((((workload.get("spec") or {}).get("template") or {}).get("spec")) or {}).get("containers") or []
    if container is None and len(containers) == 1:
        container = containers[0].get("name")
    index = next((i for i, c in enumerate(containers) if c.get("name") == container), None)
    if index is None:
        names = ", ".join(c.get("name", "?") for c in containers)
        raise ValueError(f"name the container with --container ({names})")
    res = containers[index].get("resources") or {}
    old_limit = (res.get("limits") or {}).get("memory")
    if old_limit and parse_quantity(memory) <= parse_quantity(old_limit):
        raise ValueError(f"{memory} is not above the current limit {old_limit}; set-memory only raises it")
    base = f"/spec/template/spec/containers/{index}/resources"
    ops: list[dict[str, Any]] = []
    if not res:
        ops.append({"op": "add", "path": base, "value": {"limits": {"memory": memory}}})
    elif "limits" not in res:
        ops.append({"op": "add", "path": f"{base}/limits", "value": {"memory": memory}})
    else:
        ops.append({"op": "add", "path": f"{base}/limits/memory", "value": memory})
    request = (res.get("requests") or {}).get("memory")
    if request and parse_quantity(request) > parse_quantity(memory):
        ops.append({"op": "replace", "path": f"{base}/requests/memory", "value": memory})
    was = f" (was {fmt_bytes(parse_quantity(old_limit))})" if old_limit else " (it had no limit)"
    return Plan("set-memory", f"{kind}/{namespace}/{name}",
                f"Set the memory limit of container {container} to {memory}{was}. This rolls out new pods.",
                f"kubectl set resources {kind}/{name} -n {namespace} -c {container} --limits=memory={memory}",
                "PATCH", path, ops, "application/json-patch+json")


def _plan_grant(client: KubeClient, namespace: str, service_account: str, kind: str, verbs: list[str],
                resource: str, api_group: str) -> Plan:
    """Give a ServiceAccount one permission in its namespace (the fix for a proven Kubernetes RBAC denial)."""
    if kind not in ("serviceaccount", "sa"):
        raise ValueError("grant works on a service account (sa/NAME)")
    verbs = sorted({v.strip().lower() for v in verbs if v.strip()})
    if not verbs or not set(verbs) <= GRANT_VERBS:
        raise ValueError(f"--verb must be one or more of {', '.join(sorted(GRANT_VERBS))} (no wildcards)")
    if not _RESOURCE.match(resource or ""):
        raise ValueError("--resource must be one resource, e.g. secrets or pods/log (no wildcards)")
    if api_group and not re.match(r"^[a-z0-9.-]+$", api_group):
        raise ValueError(f"invalid API group {api_group!r}")
    if resource.split("/")[0] in ("roles", "rolebindings", "clusterroles", "clusterrolebindings") \
            or api_group.startswith("rbac."):
        raise ValueError("grant does not hand out RBAC permissions themselves")
    client.get(f"/api/v1/namespaces/{namespace}/serviceaccounts/{service_account}")  # must exist (404 otherwise)
    short = re.sub(r"[^a-z0-9-]", "-", f"{service_account}-{'-'.join(verbs)}-{resource}")[:50].strip("-")
    role = f"kubesift-{short}"
    rules = [{"apiGroups": [api_group], "resources": [resource], "verbs": verbs}]
    labels = {"app.kubernetes.io/managed-by": "kubesift"}
    role_body = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
                 "metadata": {"name": role, "namespace": namespace, "labels": labels}, "rules": rules}
    binding_body = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
                    "metadata": {"name": role, "namespace": namespace, "labels": labels},
                    "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": role},
                    "subjects": [{"kind": "ServiceAccount", "name": service_account, "namespace": namespace}]}
    base = f"/apis/rbac.authorization.k8s.io/v1/namespaces/{namespace}"
    what = f"{','.join(verbs)} {resource}" + (f" ({api_group})" if api_group else "")
    res_flag = resource + (f".{api_group}" if api_group else "")
    return Plan("grant", f"serviceaccount/{namespace}/{service_account}",
                f"Allow ServiceAccount {namespace}/{service_account} to {what}, in namespace {namespace} only "
                f"(Role and RoleBinding {role}).",
                f"kubectl create role {role} -n {namespace} --verb={','.join(verbs)} --resource={res_flag} && "
                f"kubectl create rolebinding {role} -n {namespace} --role={role} "
                f"--serviceaccount={namespace}:{service_account}",
                "POST", f"{base}/roles", role_body, None, [("POST", f"{base}/rolebindings", binding_body, None)])


def verification_target(client: KubeClient, p: Plan) -> tuple[str, str] | None:
    """What to verify after applying `p`. Call it *before* applying: a deleted pod can't be looked up afterwards.

    None when nothing can show the result (e.g. a grant to an account that no workload runs as). Never raises:
    not being able to verify must not stop a fix."""
    try:
        return _verification_target(client, p)
    except Exception:
        return None


def _verification_target(client: KubeClient, p: Plan) -> tuple[str, str] | None:
    from kubesift.verify import workload_of_pod
    kind, ns, name = p.target.split("/", 2)
    if p.action == "delete-pod":
        return workload_of_pod(client, ns, name)
    if p.action == "set-selector":
        return "service", name
    if p.action == "grant":  # the workloads running as that account show whether the denial is gone
        for plural in ("deployments", "statefulsets", "daemonsets"):
            for w in client.get(f"/apis/apps/v1/namespaces/{ns}/{plural}").get("items") or []:
                spec = (((w.get("spec") or {}).get("template") or {}).get("spec")) or {}
                if (spec.get("serviceAccountName") or "default") == name:
                    return plural[:-1], (w.get("metadata") or {}).get("name", "")
        return None
    return kind, name


def execute(client: KubeClient, p: Plan, dry_run: bool, store: Store | None = None, actor: str | None = None) -> str:
    query = {"dryRun": "All"} if dry_run else None
    try:
        for method, path, body, ctype in [(p.method, p.path, p.body, p.content_type), *p.more]:
            client.request(method, path, query=query, body=body, content_type=ctype)
        result = "dry run succeeded" if dry_run else "applied"
    except KubeError as exc:
        result = f"failed: {exc.message}"
        if store:
            store.audit(p.action, p.target, dry_run, result, actor)
        raise
    if store:
        store.audit(p.action, p.target, dry_run, result, actor)
    return result
