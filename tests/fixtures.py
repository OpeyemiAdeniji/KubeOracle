"""A synthetic broken cluster used by the tests. Each object mirrors a real incident pattern."""

from __future__ import annotations

import base64
import gzip
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from kubesift.cluster.snapshot import Snapshot

NOW = datetime.now(timezone.utc)


def ts(minutes_ago: float = 0, days_ago: float = 0) -> str:
    return (NOW - timedelta(minutes=minutes_ago, days=days_ago)).isoformat().replace("+00:00", "Z")


def m(name: str, ns: str | None = "shop", labels: dict | None = None, created: str | None = None, **extra: Any) -> dict:
    out: dict[str, Any] = {"name": name, "labels": labels or {}, "creationTimestamp": created or ts(days_ago=3),
                           "uid": f"uid-{name}"}
    if ns:
        out["namespace"] = ns
    out.update(extra)
    return out


def container(name: str = "app", image: str = "ghcr.io/acme/app:1.0", **extra: Any) -> dict:
    c = {"name": name, "image": image, "resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                                                      "limits": {"memory": "256Mi"}}}
    c.update(extra)
    return c


def pod(name: str, owner_rs: str | None = None, labels: dict | None = None, containers: list | None = None,
        statuses: list | None = None, phase: str = "Running", node: str | None = "node-1", ns: str = "shop",
        conditions: list | None = None, **spec_extra: Any) -> dict:
    meta = m(name, ns, labels or {"app": name})
    if owner_rs:
        meta["ownerReferences"] = [{"kind": "ReplicaSet", "name": owner_rs, "controller": True, "uid": f"uid-{owner_rs}"}]
    spec = {"containers": containers or [container()], **spec_extra}
    if node:
        spec["nodeName"] = node
    ready = all(s.get("ready") for s in statuses or [{"ready": True}])
    return {"kind": "Pod", "apiVersion": "v1", "metadata": meta, "spec": spec,
            "status": {"phase": phase, "startTime": ts(days_ago=1), "containerStatuses": statuses or [
                {"name": "app", "ready": True, "restartCount": 0, "state": {"running": {"startedAt": ts(days_ago=1)}}}],
                "conditions": conditions or [{"type": "Ready", "status": "True" if ready else "False",
                                              "lastTransitionTime": ts(minutes_ago=30)}]}}


def deployment(name: str, replicas: int, ready: int, labels: dict, containers: list, ns: str = "shop", **status: Any) -> dict:
    return {"kind": "Deployment", "apiVersion": "apps/v1", "metadata": m(name, ns),
            "spec": {"replicas": replicas, "selector": {"matchLabels": labels},
                     "template": {"metadata": {"labels": labels}, "spec": {"containers": containers}}},
            "status": {"replicas": replicas, "readyReplicas": ready, "availableReplicas": ready,
                       "updatedReplicas": replicas, "conditions": [
                           {"type": "Available", "status": "True" if ready >= replicas else "False",
                            "lastTransitionTime": ts(minutes_ago=15), "message": "Deployment does not have minimum availability."}],
                       **status}}


def rs(name: str, deploy: str, revision: int, containers: list, created_minutes_ago: float, labels: dict) -> dict:
    return {"kind": "ReplicaSet", "apiVersion": "apps/v1",
            "metadata": m(name, "shop", labels, created=ts(minutes_ago=created_minutes_ago),
                          annotations={"deployment.kubernetes.io/revision": str(revision)},
                          ownerReferences=[{"kind": "Deployment", "name": deploy, "controller": True, "uid": f"uid-{deploy}"}]),
            "spec": {"template": {"metadata": {"labels": labels}, "spec": {"containers": containers}}}}


def event(kind: str, name: str, reason: str, message: str, ns: str | None = "shop", minutes_ago: float = 5,
          etype: str = "Warning") -> dict:
    return {"kind": "Event", "apiVersion": "v1", "metadata": m(f"{name}.{reason}".lower(), ns),
            "involvedObject": {"kind": kind, "name": name, "namespace": ns}, "reason": reason, "message": message,
            "type": etype, "lastTimestamp": ts(minutes_ago=minutes_ago), "count": 3}


def tls_secret(name: str, days_left: int, ns: str = "shop") -> dict:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    from kubesift.cluster.snapshot import _sanitize_secret

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "shop.example.com")])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(1).not_valid_before(NOW - timedelta(days=60)).not_valid_after(NOW + timedelta(days=days_left))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("shop.example.com")]), critical=False)
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM)
    raw = {"kind": "Secret", "metadata": m(name, ns), "type": "kubernetes.io/tls",
           "data": {"tls.crt": base64.b64encode(pem).decode(), "tls.key": "c2VjcmV0"}}
    return _sanitize_secret(raw)


def helm_secret(release: str, ns: str, revision: int, manifest: str, minutes_ago: float) -> dict:
    payload = {"name": release, "namespace": ns, "version": revision, "manifest": manifest,
               "info": {"status": "deployed", "last_deployed": ts(minutes_ago=minutes_ago)},
               "chart": {"metadata": {"name": release, "version": "1.2.0", "appVersion": "2.0"}}}
    inner = base64.b64encode(gzip.compress(json.dumps(payload).encode())).decode()
    return {"kind": "Secret", "metadata": m(f"sh.helm.release.v1.{release}.v{revision}", ns,
                                            {"owner": "helm", "name": release, "version": str(revision), "status": "deployed"},
                                            created=ts(minutes_ago=minutes_ago)),
            "type": "helm.sh/release.v1", "data": {"release": base64.b64encode(inner.encode()).decode()}}


def node(name: str, ready: bool = True, **info: Any) -> dict:
    return {"kind": "Node", "apiVersion": "v1", "metadata": m(name, None, {"kubernetes.io/hostname": name}),
            "spec": {},
            "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "Unknown",
                                       "reason": "KubeletReady" if ready else "NodeStatusUnknown",
                                       "message": "" if ready else "Kubelet stopped posting node status.",
                                       "lastTransitionTime": ts(minutes_ago=40)}],
                       "allocatable": {"cpu": "4", "memory": "16Gi", "pods": "110"},
                       "nodeInfo": {"kubeletVersion": info.get("kubelet", "v1.34.2"),
                                    "containerRuntimeVersion": info.get("runtime", "containerd://2.1.0")}}}


def broken_cluster() -> Snapshot:
    from kubesift.cluster.snapshot import _helm_releases, _sanitize_secret

    o: dict[str, list[dict]] = defaultdict(list)
    o["nodes"] += [node("node-1"), node("node-2", ready=False), node("node-3", runtime="containerd://1.7.20", kubelet="v1.31.0")]
    o["namespaces"] += [{"kind": "Namespace", "metadata": m(n, None)} for n in ("shop", "kube-system", "ingress-nginx")]

    # 1. Bad rollout: checkout v2 dropped DATABASE_URL and crash loops.
    v1 = [container(image="ghcr.io/acme/checkout:1.4", env=[{"name": "DATABASE_URL", "value": "postgres://db:5432/shop"}],
                    ports=[{"containerPort": 8080, "name": "http"}])]
    v2 = [container(image="ghcr.io/acme/checkout:1.5", ports=[{"containerPort": 8080, "name": "http"}])]
    labels = {"app": "checkout"}
    o["deployments"].append(deployment("checkout", 2, 0, labels, v2))
    o["replicasets"] += [rs("checkout-6d8f", "checkout", 1, v1, 60 * 24, labels),
                         rs("checkout-7c9a", "checkout", 2, v2, 20, labels)]
    o["pods"].append(pod("checkout-7c9a-x1", "checkout-7c9a", labels, v2, statuses=[{
        "name": "app", "ready": False, "restartCount": 7, "image": "ghcr.io/acme/checkout:1.5",
        "state": {"waiting": {"reason": "CrashLoopBackOff"}},
        "lastState": {"terminated": {"exitCode": 1, "reason": "Error", "startedAt": ts(minutes_ago=18),
                                     "finishedAt": ts(minutes_ago=18)}}}]))

    # 2. OOM
    api = [container(image="ghcr.io/acme/api:3.2", resources={"limits": {"memory": "64Mi"}, "requests": {"memory": "64Mi", "cpu": "50m"}})]
    o["deployments"].append(deployment("api", 1, 0, {"app": "api"}, api))
    o["replicasets"].append(rs("api-55", "api", 1, api, 60 * 48, {"app": "api"}))
    o["pods"].append(pod("api-55-a", "api-55", {"app": "api"}, api, statuses=[{
        "name": "app", "ready": False, "restartCount": 4, "state": {"waiting": {"reason": "CrashLoopBackOff"}},
        "lastState": {"terminated": {"exitCode": 137, "reason": "OOMKilled", "finishedAt": ts(minutes_ago=3)}}}]))

    # 3. Docker Hub rate limit
    o["pods"].append(pod("worker", containers=[container(image="redis:7")], statuses=[{
        "name": "app", "ready": False, "restartCount": 0, "image": "redis:7",
        "state": {"waiting": {"reason": "ImagePullBackOff", "message": "Back-off pulling image \"redis:7\""}}}]))
    o["events"].append(event("Pod", "worker", "Failed", "Failed to pull image \"redis:7\": 429 Too Many Requests - "
                                                         "toomanyrequests: You have reached your pull rate limit."))

    # 4. Service selector mismatch
    o["pods"].append(pod("web-1", labels={"app": "web"}, containers=[container(ports=[{"containerPort": 80}])]))
    o["services"].append({"kind": "Service", "metadata": m("web"), "spec": {"selector": {"app": "wbe"},
                                                                              "ports": [{"port": 80, "targetPort": 80}]}})

    # 5. Pending: insufficient CPU
    o["pods"].append(pod("batch-big", node=None, phase="Pending", statuses=[], conditions=[
        {"type": "PodScheduled", "status": "False", "reason": "Unschedulable",
         "message": "0/3 nodes are available: 3 Insufficient cpu. preemption: 0/3 nodes are available."}]))
    o["pods"][-1]["metadata"]["creationTimestamp"] = ts(minutes_ago=30)
    o["pods"][-1]["status"]["containerStatuses"] = []

    # 6. Missing ConfigMap
    o["pods"].append(pod("reports", containers=[container(envFrom=[{"configMapRef": {"name": "reports-config"}}])],
                         statuses=[{"name": "app", "ready": False, "restartCount": 0, "state": {"waiting": {
                             "reason": "CreateContainerConfigError", "message": "configmap \"reports-config\" not found"}}}]))

    # 7. Pods on a dead node
    o["pods"].append(pod("cart-1", labels={"app": "cart"}, node="node-2", statuses=[{
        "name": "app", "ready": False, "restartCount": 0, "state": {"running": {"startedAt": ts(days_ago=1)}}}],
        conditions=[{"type": "Ready", "status": "False", "lastTransitionTime": ts(minutes_ago=40)}]))

    # 8. Expiring certificate, ingress to a missing service, retired ingress-nginx
    o["secrets"].append(tls_secret("shop-tls", days_left=3))
    o["ingresses"].append({"kind": "Ingress", "metadata": m("shop", annotations={"nginx.ingress.kubernetes.io/rewrite-target": "/"}),
                           "spec": {"ingressClassName": "nginx", "tls": [{"hosts": ["shop.example.com"], "secretName": "shop-tls"}],
                                    "rules": [{"host": "shop.example.com", "http": {"paths": [
                                        {"path": "/", "backend": {"service": {"name": "storefront", "port": {"number": 80}}}}]}}]}})
    o["ingressclasses"].append({"kind": "IngressClass", "metadata": m("nginx", None), "spec": {"controller": "k8s.io/ingress-nginx"}})
    o["pods"].append(pod("ingress-nginx-controller-1", ns="ingress-nginx", labels={"app.kubernetes.io/name": "ingress-nginx"},
                         containers=[container(image="registry.k8s.io/ingress-nginx/controller:v1.11.2")]))

    # 9. PDB blocking drains; NetworkPolicy blocking DNS
    o["pdbs"].append({"kind": "PodDisruptionBudget", "metadata": m("checkout"),
                      "spec": {"minAvailable": 2, "selector": {"matchLabels": labels}},
                      "status": {"expectedPods": 2, "disruptionsAllowed": 0, "currentHealthy": 0, "desiredHealthy": 2}})
    o["networkpolicies"].append({"kind": "NetworkPolicy", "metadata": m("lockdown"), "spec": {
        "podSelector": {"matchLabels": {"app": "web"}}, "policyTypes": ["Egress"],
        "egress": [{"to": [{"podSelector": {"matchLabels": {"app": "db"}}}], "ports": [{"port": 5432}]}]}})

    # 10. Webhook with no backend
    o["validatingwebhooks"].append({"kind": "ValidatingWebhookConfiguration", "metadata": m("policy-webhook", None),
                                    "webhooks": [{"name": "validate.policy.example.com", "failurePolicy": "Fail",
                                                  "clientConfig": {"service": {"namespace": "policy", "name": "policy-svc"}},
                                                  "rules": [{"operations": ["CREATE"], "resources": ["pods"]}]}]})
    o["events"].append(event("ReplicaSet", "checkout-7c9a", "FailedCreate",
                             'Internal error occurred: failed calling webhook "validate.policy.example.com": '
                             'no endpoints available for service "policy-svc"'))

    # 11. Helm release with a removed API + config edit
    secrets_raw = [helm_secret("legacy", "shop", 3, "apiVersion: policy/v1beta1\nkind: PodDisruptionBudget\nmetadata:\n  name: legacy\n", 90)]
    o["secrets"] += [_sanitize_secret(s) for s in secrets_raw]
    o["configmaps"].append({"kind": "ConfigMap", "metadata": m("checkout-flags", managedFields=[
        {"manager": "kubectl-edit", "operation": "Update", "time": ts(minutes_ago=45)}]), "keys": ["FEATURE_X"]})

    snap = Snapshot(context="test-cluster", live=True, server_version="v1.34.2")
    for key, items in o.items():
        for item in items:
            item.setdefault("apiVersion", "v1")
        snap.objects[key] = items
    snap.readable = set(o.keys()) | {"endpointslices", "configmaps", "secrets", "storageclasses"}
    snap.helm_releases = _helm_releases(secrets_raw)
    snap.logs["shop/checkout-7c9a-x1/app"] = {"previous": True, "errors": [
        "Traceback (most recent call last):", "KeyError: 'DATABASE_URL'"], "tail": []}
    snap.metrics = {"pods": {"shop/web-1/app": {"cpu": 0.005, "memory": 40 * 2**20}}, "nodes": {}}
    return snap
