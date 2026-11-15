"""A point-in-time copy of the cluster state that every check reads from.

A Snapshot can be collected live, saved to JSON (already redacted) and loaded
again, or built from manifest files for offline linting.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from kubeoracle.cluster.client import KubeClient, KubeError
from kubeoracle.models import ResourceRef
from kubeoracle.redact import redact
from kubeoracle.util import meta, now, parse_time

# (key, kind, api prefix, plural, namespaced, optional)
KINDS: list[tuple[str, str, str, str, bool, bool]] = [
    ("pods", "Pod", "/api/v1", "pods", True, False),
    ("events", "Event", "/api/v1", "events", True, False),
    ("nodes", "Node", "/api/v1", "nodes", False, False),
    ("namespaces", "Namespace", "/api/v1", "namespaces", False, False),
    ("services", "Service", "/api/v1", "services", True, False),
    ("endpointslices", "EndpointSlice", "/apis/discovery.k8s.io/v1", "endpointslices", True, False),
    ("configmaps", "ConfigMap", "/api/v1", "configmaps", True, False),
    ("secrets", "Secret", "/api/v1", "secrets", True, True),
    ("serviceaccounts", "ServiceAccount", "/api/v1", "serviceaccounts", True, False),
    ("pvcs", "PersistentVolumeClaim", "/api/v1", "persistentvolumeclaims", True, False),
    ("pvs", "PersistentVolume", "/api/v1", "persistentvolumes", False, False),
    ("resourcequotas", "ResourceQuota", "/api/v1", "resourcequotas", True, False),
    ("limitranges", "LimitRange", "/api/v1", "limitranges", True, False),
    ("deployments", "Deployment", "/apis/apps/v1", "deployments", True, False),
    ("replicasets", "ReplicaSet", "/apis/apps/v1", "replicasets", True, False),
    ("statefulsets", "StatefulSet", "/apis/apps/v1", "statefulsets", True, False),
    ("daemonsets", "DaemonSet", "/apis/apps/v1", "daemonsets", True, False),
    ("jobs", "Job", "/apis/batch/v1", "jobs", True, False),
    ("cronjobs", "CronJob", "/apis/batch/v1", "cronjobs", True, False),
    ("hpas", "HorizontalPodAutoscaler", "/apis/autoscaling/v2", "horizontalpodautoscalers", True, False),
    ("pdbs", "PodDisruptionBudget", "/apis/policy/v1", "poddisruptionbudgets", True, False),
    ("ingresses", "Ingress", "/apis/networking.k8s.io/v1", "ingresses", True, False),
    ("ingressclasses", "IngressClass", "/apis/networking.k8s.io/v1", "ingressclasses", False, False),
    ("networkpolicies", "NetworkPolicy", "/apis/networking.k8s.io/v1", "networkpolicies", True, False),
    ("storageclasses", "StorageClass", "/apis/storage.k8s.io/v1", "storageclasses", False, False),
    ("clusterrolebindings", "ClusterRoleBinding", "/apis/rbac.authorization.k8s.io/v1", "clusterrolebindings", False, True),
    ("rolebindings", "RoleBinding", "/apis/rbac.authorization.k8s.io/v1", "rolebindings", True, True),
    ("clusterroles", "ClusterRole", "/apis/rbac.authorization.k8s.io/v1", "clusterroles", False, True),
    ("roles", "Role", "/apis/rbac.authorization.k8s.io/v1", "roles", True, True),
    ("validatingwebhooks", "ValidatingWebhookConfiguration", "/apis/admissionregistration.k8s.io/v1",
     "validatingwebhookconfigurations", False, True),
    ("mutatingwebhooks", "MutatingWebhookConfiguration", "/apis/admissionregistration.k8s.io/v1",
     "mutatingwebhookconfigurations", False, True),
    # Optional add-ons: missing APIs are skipped silently.
    ("gateways", "Gateway", "/apis/gateway.networking.k8s.io/v1", "gateways", True, True),
    ("httproutes", "HTTPRoute", "/apis/gateway.networking.k8s.io/v1", "httproutes", True, True),
    ("certificates", "Certificate", "/apis/cert-manager.io/v1", "certificates", True, True),
    ("argoapps", "Application", "/apis/argoproj.io/v1alpha1", "applications", True, True),
]
KIND_TO_KEY = {kind: key for key, kind, *_ in KINDS}
CLUSTER_SCOPED = {key for key, _, _, _, namespaced, _ in KINDS if not namespaced}
_LAST_APPLIED = "kubectl.kubernetes.io/last-applied-configuration"
APPLIED_API = "kubeoracle.io/applied-api-version"
SOURCE = "kubeoracle.io/source"
_ERROR_LINE = re.compile(
    r"(?i)(error|exception|fatal|panic|traceback|refused|denied|forbidden|timeout|timed out|not found|"
    r"no such|unable to|cannot|could not|failed|killed|exec format|oom|out of memory|unauthorized|invalid)"
)


@dataclass
class Snapshot:
    context: str = "offline"
    namespace: str | None = None
    live: bool = True
    collected_at: datetime = field(default_factory=now)
    server_version: str | None = None
    objects: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    warnings: list[str] = field(default_factory=list)
    logs: dict[str, dict[str, Any]] = field(default_factory=dict)
    metrics: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    helm_releases: list[dict[str, Any]] = field(default_factory=list)
    readable: set[str] = field(default_factory=set)

    # ----------------------------------------------------------------- access
    def all(self, key: str) -> list[dict[str, Any]]:
        return self.objects.get(key, [])

    def can_read(self, key: str) -> bool:
        """False when the kind could not be listed (no permission), so absence means nothing."""
        return not self.live or key in self.readable

    def get(self, key: str, namespace: str | None, name: str) -> dict[str, Any] | None:
        return self._index().get((key, namespace if key not in CLUSTER_SCOPED else None, name))

    def _index(self) -> dict[tuple[str, str | None, str], dict[str, Any]]:
        if not hasattr(self, "_idx"):
            idx = {}
            for key, items in self.objects.items():
                for o in items:
                    m = meta(o)
                    idx[(key, None if key in CLUSTER_SCOPED else m.get("namespace"), m.get("name", ""))] = o
            self._idx = idx  # type: ignore[attr-defined]
        return self._idx  # type: ignore[attr-defined]

    def in_namespace(self, key: str, namespace: str | None) -> list[dict[str, Any]]:
        cache = self.__dict__.setdefault("_ns_idx", {})
        if key not in cache:
            by_ns: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
            for o in self.all(key):
                by_ns[meta(o).get("namespace")].append(o)
            cache[key] = by_ns
        return cache[key].get(namespace, [])

    def events_for(self, kind: str, namespace: str | None, name: str) -> list[dict[str, Any]]:
        if "_ev_idx" not in self.__dict__:
            idx: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for e in self.all("events"):
                inv = e.get("involvedObject") or e.get("regarding") or {}
                idx[(inv.get("kind", ""), inv.get("name", ""))].append(e)
            for items in idx.values():
                items.sort(key=event_time, reverse=True)
            self.__dict__["_ev_idx"] = idx
        return [e for e in self.__dict__["_ev_idx"].get((kind, name), [])
                if namespace is None or ((e.get("involvedObject") or e.get("regarding") or {}).get("namespace")
                                         in (namespace, None, ""))]

    def owner_of(self, obj: dict[str, Any]) -> ResourceRef:
        """Follow ownerReferences up to the top-level workload (Pod -> ReplicaSet -> Deployment)."""
        current = obj
        for _ in range(4):
            refs = meta(current).get("ownerReferences") or []
            ref = next((r for r in refs if r.get("controller")), refs[0] if refs else None)
            if not ref:
                break
            key = KIND_TO_KEY.get(ref.get("kind", ""))
            parent = self.get(key, meta(current).get("namespace"), ref.get("name", "")) if key else None
            if parent is None:
                return ResourceRef(kind=ref.get("kind", "?"), name=ref.get("name", "?"),
                                   namespace=meta(current).get("namespace"))
            current = parent
        kind = current.get("kind") or "Pod"
        return ResourceRef(kind=kind, name=meta(current).get("name", ""), namespace=meta(current).get("namespace"))

    def workloads(self) -> list[dict[str, Any]]:
        """Objects that carry a pod template (what people actually write and deploy)."""
        out = []
        for key in ("deployments", "statefulsets", "daemonsets", "cronjobs"):
            out.extend(self.all(key))
        out.extend(j for j in self.all("jobs") if not meta(j).get("ownerReferences"))
        if not self.live:
            out.extend(p for p in self.all("pods") if not meta(p).get("ownerReferences"))
        return out

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict[str, Any]:
        return {
            "kubeoracle_snapshot": 1,
            "context": self.context,
            "namespace": self.namespace,
            "collected_at": self.collected_at.isoformat(),
            "server_version": self.server_version,
            "objects": dict(self.objects),
            "warnings": self.warnings,
            "logs": self.logs,
            "metrics": self.metrics,
            "helm_releases": self.helm_releases,
            "readable": sorted(self.readable),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Snapshot:
        snap = cls(
            context=data.get("context", "snapshot"),
            namespace=data.get("namespace"),
            live=True,
            collected_at=parse_time(data.get("collected_at")) or now(),
            server_version=data.get("server_version"),
            warnings=list(data.get("warnings") or []),
            logs=dict(data.get("logs") or {}),
            metrics=dict(data.get("metrics") or {}),
            helm_releases=list(data.get("helm_releases") or []),
        )
        snap.objects = defaultdict(list, {k: list(v) for k, v in (data.get("objects") or {}).items()})
        snap.readable = set(data.get("readable") or snap.objects.keys())
        return snap

    @classmethod
    def from_manifests(cls, docs: list[dict[str, Any]]) -> Snapshot:
        snap = cls(context="manifests", live=False)
        for doc in docs:
            kind = doc.get("kind")
            if kind == "List":
                for item in doc.get("items") or []:
                    item.setdefault("metadata", {}).setdefault("annotations", {})
                    src = meta(doc).get("annotations", {}).get(SOURCE)
                    if src:
                        item["metadata"]["annotations"][SOURCE] = src
                    snap._add_manifest(item)
            else:
                snap._add_manifest(doc)
        return snap

    def _add_manifest(self, doc: dict[str, Any]) -> None:
        key = KIND_TO_KEY.get(doc.get("kind", ""))
        doc.setdefault("metadata", {}).setdefault("namespace", None if key in CLUSTER_SCOPED else "default")
        self.objects[key or f"other:{doc.get('kind')}"].append(doc)


def event_time(e: dict[str, Any]) -> str:
    return e.get("lastTimestamp") or e.get("eventTime") or meta(e).get("creationTimestamp") or ""


# --------------------------------------------------------------------- collect
def collect(
    client: KubeClient,
    namespace: str | None = None,
    max_items: int = 5000,
    with_logs: bool = True,
    kinds: set[str] | None = None,
) -> Snapshot:
    snap = Snapshot(context=client.context_name, namespace=namespace, live=True)
    snap.server_version = client.server_version()

    def fetch(entry: tuple[str, str, str, str, bool, bool]) -> None:
        key, kind, prefix, plural, namespaced, optional = entry
        if kinds is not None and key not in kinds:
            return
        if namespace and namespaced:
            path = f"{prefix}/namespaces/{namespace}/{plural}"
        else:
            path = f"{prefix}/{plural}"
        try:
            items, truncated = client.list_all(path, max_items=max_items)
        except KubeError as exc:
            if exc.status == 404:
                return
            if exc.status == 403:
                if not optional or key == "secrets":
                    snap.warnings.append(f"No permission to list {plural}; checks that need them were skipped.")
                return
            snap.warnings.append(f"Could not list {plural}: {exc.message}")
            return
        except Exception as exc:  # network errors etc.
            snap.warnings.append(f"Could not list {plural}: {exc}")
            return
        api_version = prefix.removeprefix("/apis/").removeprefix("/api/")
        for item in items:
            item["kind"] = kind
            item["apiVersion"] = api_version
        if truncated:
            snap.warnings.append(f"Only the first {max_items} {plural} were read.")
        if key == "secrets":
            snap.helm_releases.extend(_helm_releases(items))
            items = [_sanitize_secret(s) for s in items]
        elif key == "configmaps":
            items = [_sanitize_configmap(c) for c in items]
        for item in items:
            _shrink(item)
        snap.objects[key] = items
        snap.readable.add(key)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch, KINDS))

    if "pods" not in snap.readable and not snap.warnings:
        snap.warnings.append("Could not read pods from the cluster.")
    _collect_metrics(client, snap, namespace)
    _collect_kube_proxy_mode(client, snap)
    if with_logs:
        collect_logs(client, snap)
    return snap


CHANGE_TRACKED = {"ConfigMap", "Secret", "Deployment", "StatefulSet", "DaemonSet", "CronJob", "Service", "Ingress",
                  "NetworkPolicy", "HorizontalPodAutoscaler", "ResourceQuota"}


def _shrink(obj: dict[str, Any]) -> None:
    m = obj.get("metadata") or {}
    ann = m.get("annotations") or {}
    applied = ann.pop(_LAST_APPLIED, None)
    if applied:
        with contextlib.suppress(ValueError):
            ann[APPLIED_API] = json.loads(applied).get("apiVersion", "")
    mf = m.pop("managedFields", None)
    if mf and obj.get("kind") in CHANGE_TRACKED:
        # keep only who changed the object and when; used by `kubeoracle changes`
        m["managedFields"] = [{"manager": f.get("manager"), "operation": f.get("operation"), "time": f.get("time"),
                               "subresource": f.get("subresource")} for f in mf]


def _sanitize_configmap(cm: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": cm.get("kind"),
        "apiVersion": cm.get("apiVersion"),
        "metadata": cm.get("metadata") or {},
        "keys": sorted(list((cm.get("data") or {}).keys()) + list((cm.get("binaryData") or {}).keys())),
    }


def _sanitize_secret(secret: dict[str, Any]) -> dict[str, Any]:
    """Keep only key names, the type and certificate expiry. Secret values never leave this function."""
    data = secret.get("data") or {}
    out: dict[str, Any] = {
        "kind": "Secret",
        "apiVersion": "v1",
        "metadata": secret.get("metadata") or {},
        "type": secret.get("type", "Opaque"),
        "keys": sorted(data.keys()),
    }
    if out["type"] == "kubernetes.io/tls" or "tls.crt" in data:
        out["tls"] = _cert_info(data.get("tls.crt"))
    return out


def _cert_info(b64: str | None) -> dict[str, Any] | None:
    if not b64:
        return None
    try:
        from cryptography import x509

        pem = base64.b64decode(b64)
        cert = x509.load_pem_x509_certificate(pem)
        try:
            not_after = cert.not_valid_after_utc
        except AttributeError:  # cryptography < 42
            not_after = cert.not_valid_after
        names: list[str] = []
        try:
            ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            names = ext.value.get_values_for_type(x509.DNSName)
        except Exception:
            pass
        return {"not_after": not_after.isoformat(), "subject": cert.subject.rfc4514_string(), "dns_names": names[:10]}
    except Exception:
        return {"error": "could not parse certificate"}


def _helm_releases(secrets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decode the latest revision of each Helm 3 release (resource list only, no values)."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for s in secrets:
        if s.get("type") != "helm.sh/release.v1":
            continue
        labels = meta(s).get("labels") or {}
        key = (meta(s).get("namespace", ""), labels.get("name", ""))
        try:
            version = int(labels.get("version", "0"))
        except ValueError:
            version = 0
        prev = latest.get(key)
        if prev is None or version > prev["revision"]:
            latest[key] = {"secret": s, "revision": version, "status": labels.get("status", "")}
    out = []
    for (ns, name), item in latest.items():
        rel: dict[str, Any] = {"name": name, "namespace": ns, "revision": item["revision"], "status": item["status"],
                               "updated": meta(item["secret"]).get("creationTimestamp"), "resources": []}
        try:
            raw = base64.b64decode(base64.b64decode((item["secret"].get("data") or {}).get("release", "")))
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            payload = json.loads(raw)
            chart = ((payload.get("chart") or {}).get("metadata") or {})
            rel["chart"] = f"{chart.get('name', '')}-{chart.get('version', '')}"
            rel["app_version"] = chart.get("appVersion")
            rel["updated"] = (payload.get("info") or {}).get("last_deployed") or rel["updated"]
            rel["resources"] = _manifest_index(payload.get("manifest") or "")
        except Exception:
            pass
        out.append(rel)
    return out


def _manifest_index(manifest: str) -> list[dict[str, str]]:
    import yaml

    out = []
    try:
        for doc in yaml.safe_load_all(manifest):
            if isinstance(doc, dict) and doc.get("kind"):
                out.append({"apiVersion": doc.get("apiVersion", ""), "kind": doc["kind"],
                            "name": meta(doc).get("name", "")})
    except yaml.YAMLError:
        pass
    return out


def _collect_metrics(client: KubeClient, snap: Snapshot, namespace: str | None) -> None:
    from kubeoracle.util import parse_quantity

    base = "/apis/metrics.k8s.io/v1beta1"
    try:
        pods = client.get(f"{base}/namespaces/{namespace}/pods" if namespace else f"{base}/pods")
        nodes = client.get(f"{base}/nodes")
    except Exception:
        return
    pm: dict[str, dict[str, float]] = {}
    for p in pods.get("items") or []:
        for c in p.get("containers") or []:
            k = f"{meta(p).get('namespace')}/{meta(p).get('name')}/{c.get('name')}"
            usage = c.get("usage") or {}
            pm[k] = {"cpu": parse_quantity(usage.get("cpu")), "memory": parse_quantity(usage.get("memory"))}
    nm = {}
    for n in nodes.get("items") or []:
        usage = n.get("usage") or {}
        nm[meta(n).get("name", "")] = {"cpu": parse_quantity(usage.get("cpu")),
                                       "memory": parse_quantity(usage.get("memory"))}
    snap.metrics = {"pods": pm, "nodes": nm}


def _collect_kube_proxy_mode(client: KubeClient, snap: Snapshot) -> None:
    try:
        cm = client.get("/api/v1/namespaces/kube-system/configmaps/kube-proxy")
    except Exception:
        return
    for value in (cm.get("data") or {}).values():
        m = re.search(r"(?m)^\s*mode:\s*\"?(\w+)", value or "")
        if m:
            snap.metrics.setdefault("cluster", {})["kube_proxy"] = {"mode_" + m.group(1): 1.0}
            return


def failing_containers(pod: dict[str, Any]) -> list[tuple[str, bool]]:
    """Containers worth reading logs from: (name, use_previous_logs)."""
    out = []
    statuses = (pod.get("status") or {}).get("containerStatuses") or []
    statuses += (pod.get("status") or {}).get("initContainerStatuses") or []
    for cs in statuses:
        waiting = (cs.get("state") or {}).get("waiting") or {}
        terminated = (cs.get("state") or {}).get("terminated") or {}
        last = (cs.get("lastState") or {}).get("terminated") or {}
        if waiting.get("reason") == "CrashLoopBackOff" or (last and last.get("exitCode", 0) != 0):
            out.append((cs.get("name", ""), bool(last)))
        elif terminated and terminated.get("exitCode", 0) != 0 or cs.get("ready") is False and (cs.get("state") or {}).get("running"):
            out.append((cs.get("name", ""), False))
    return out


def summarize_log(text: str, max_lines: int = 25) -> dict[str, Any]:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    errors: list[str] = []
    seen: set[str] = set()
    for ln in lines:
        if _ERROR_LINE.search(ln):
            key = re.sub(r"\d+", "#", ln)[:160]
            if key not in seen:
                seen.add(key)
                errors.append(redact(ln[:400]))
    return {"errors": errors[-max_lines:], "tail": [redact(ln[:400]) for ln in lines[-12:]]}


def collect_logs(client: KubeClient, snap: Snapshot, max_containers: int = 25) -> None:
    targets = []
    for pod in snap.all("pods"):
        for container, previous in failing_containers(pod):
            targets.append((meta(pod).get("namespace", ""), meta(pod).get("name", ""), container, previous))
    targets = targets[:max_containers]

    def fetch(t: tuple[str, str, str, bool]) -> None:
        ns, pod, container, previous = t
        try:
            text = client.pod_logs(ns, pod, container, previous=previous)
        except KubeError:
            if not previous:
                return
            try:
                text = client.pod_logs(ns, pod, container, previous=False)
            except Exception:
                return
        except Exception:
            return
        snap.logs[f"{ns}/{pod}/{container}"] = {"previous": previous, **summarize_log(text)}

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch, targets))
