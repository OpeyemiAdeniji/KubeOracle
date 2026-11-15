"""Preflight: check a change against the live cluster before it is deployed.

Linters look at YAML alone. Preflight also asks the real cluster: does the Secret exist there? Will the pods
fit on any node? Does the new memory limit sit below what the app uses today? Will admission webhooks or
Pod Security reject it (server-side dry run)?
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from kubesift.analysis.changes import _template_diff
from kubesift.checks.base import DOCS_URL, run_checks
from kubesift.checks.config_refs import _refs
from kubesift.cluster.client import KubeClient, KubeError
from kubesift.cluster.snapshot import KIND_TO_KEY, KINDS, Snapshot
from kubesift.config import Settings
from kubesift.models import Category, Finding, ResourceRef, Severity
from kubesift.util import (
    check_name,
    containers_of,
    fmt_bytes,
    fmt_cpu,
    image_registry,
    label_selector_matches,
    meta,
    parse_quantity,
    pod_spec_of,
    selector_matches,
)

_WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod"}


@dataclass
class PreflightResult:
    findings: list[Finding] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)
    target: str = ""
    server_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"target": self.target, "server_version": self.server_version, "changes": self.changes,
                "findings": [f.model_dump(mode="json") for f in self.findings]}


def _f(check: str, title: str, sev: Severity, cat: Category, ref: ResourceRef, msg: str, fix: str = "",
       evidence: list[str] | None = None, source: str | None = None) -> Finding:
    return Finding(check=check, title=title, severity=sev, category=cat, resource=ref, owner=ref, message=msg,
                   remediation=fix, evidence=evidence or [], source=source, docs=DOCS_URL.replace("checks.md", "preflight.md"))


_CLUSTER_SCOPED = {kind for _, kind, _, _, namespaced, _ in KINDS if not namespaced} | {
    "CustomResourceDefinition", "PriorityClass", "RuntimeClass", "CSIDriver", "VolumeSnapshotClass",
    "GatewayClass", "ClusterIssuer", "NodePool", "EC2NodeClass", "PodSecurityPolicy", "ValidatingAdmissionPolicy",
    "ValidatingAdmissionPolicyBinding", "MutatingAdmissionPolicy", "MutatingAdmissionPolicyBinding"}


def _namespaced(kind: str) -> bool:
    return kind not in _CLUSTER_SCOPED and not kind.startswith("Cluster")


def _ref(doc: dict[str, Any]) -> ResourceRef:
    return ResourceRef(kind=doc.get("kind", "?"), name=meta(doc).get("name", ""), namespace=meta(doc).get("namespace"))


def _source(doc: dict[str, Any]) -> str | None:
    return (meta(doc).get("annotations") or {}).get("kubesift.dev/source")


def _pod_requests(spec: dict[str, Any]) -> tuple[float, float]:
    cpu = sum(parse_quantity(((c.get("resources") or {}).get("requests") or {}).get("cpu")) for c in containers_of(spec))
    mem = sum(parse_quantity(((c.get("resources") or {}).get("requests") or {}).get("memory")) for c in containers_of(spec))
    return cpu, mem


def _replicas(doc: dict[str, Any]) -> int:
    if doc.get("kind") in ("Deployment", "StatefulSet"):
        return int((doc.get("spec") or {}).get("replicas", 1))
    return 1


# ------------------------------------------------------------------ main entry
def preflight(docs: list[dict[str, Any]], live: Snapshot, settings: Settings, *, default_namespace: str = "default",
              client: KubeClient | None = None, server_dry_run: bool = False,
              check_images: bool = False) -> PreflightResult:
    for d in docs:
        if _namespaced(d.get("kind", "")):
            d.setdefault("metadata", {}).setdefault("namespace", default_namespace)
            if not d["metadata"].get("namespace"):
                d["metadata"]["namespace"] = default_namespace
    res = PreflightResult(target=live.context, server_version=live.server_version)

    # 1. Static checks, against the live server version
    res.findings += run_checks(Snapshot.from_manifests(json.loads(json.dumps(docs))), settings,
                               target_version=live.server_version)

    incoming: dict[tuple[str, str | None, str], dict[str, Any]] = {
        (d.get("kind", ""), meta(d).get("namespace"), meta(d).get("name", "")): d for d in docs}

    def exists(kind: str, ns: str | None, name: str) -> dict[str, Any] | None:
        if (kind, ns, name) in incoming:
            return incoming[(kind, ns, name)]
        key = KIND_TO_KEY.get(kind)
        return live.get(key, ns, name) if key else None

    # 2. Things the change needs that are not in the cluster
    res.findings += _missing_dependencies(docs, live, exists)
    # 3. Capacity: nodes, quota, PDBs
    res.findings += _capacity(docs, live)
    # 4. What changes versus the running version, and whether it is risky
    changes, risky = _diff_live(docs, live)
    res.changes, res.findings = changes, res.findings + risky
    # 5. Optional: image tags exist; server-side dry run (admission webhooks, Pod Security, quota, schema)
    if check_images:
        res.findings += _images(docs)
    if server_dry_run and client is not None:
        res.findings += _server_dry_run(docs, client)
    res.findings.sort(key=lambda f: (-f.severity.rank, str(f.resource)))
    return res


def _missing_dependencies(docs: list[dict[str, Any]], live: Snapshot, exists: Any) -> list[Finding]:
    out: list[Finding] = []
    live_ns = {meta(n).get("name") for n in live.all("namespaces")}
    new_ns = {meta(d).get("name") for d in docs if d.get("kind") == "Namespace"}
    for d in docs:
        ns, ref, src = meta(d).get("namespace"), _ref(d), _source(d)
        if ns and live.can_read("namespaces") and ns not in live_ns and ns not in new_ns:
            out.append(_f("preflight-namespace-missing", "Namespace does not exist", Severity.CRITICAL, Category.CONFIG,
                          ref, f"Namespace '{ns}' does not exist in {live.context} and is not part of this change.",
                          "Create the namespace first or include it in the change.", source=src))
        spec = pod_spec_of(d) if d.get("kind") in _WORKLOADS else None
        if not spec:
            continue
        problems = []
        for kind, name, key, where in _refs(spec):
            store = "secrets" if kind == "Secret" else "configmaps"
            if store == "secrets" and not live.can_read("secrets"):
                continue
            obj = exists(kind, ns, name)
            if obj is None:
                problems.append(f"{kind} '{name}' ({where}) does not exist in namespace {ns}")
            elif key:
                keys = set(obj.get("keys") or []) | set((obj.get("data") or {}).keys()) | \
                    set((obj.get("stringData") or {}).keys()) | set((obj.get("binaryData") or {}).keys())
                if key not in keys:
                    problems.append(f"{kind} '{name}' has no key '{key}' ({where})")
        for ps in spec.get("imagePullSecrets") or []:
            if live.can_read("secrets") and exists("Secret", ns, ps.get("name", "")) is None:
                problems.append(f"imagePullSecret '{ps.get('name')}' does not exist in namespace {ns}")
        sa = spec.get("serviceAccountName")
        if sa and sa != "default" and live.can_read("serviceaccounts") and exists("ServiceAccount", ns, sa) is None:
            problems.append(f"ServiceAccount '{sa}' does not exist in namespace {ns}")
        if problems:
            out.append(_f("preflight-dependency-missing", "Change depends on something missing", Severity.CRITICAL,
                          Category.CONFIG, ref, problems[0] + ". Pods will not start after this deploy.",
                          "Create the missing objects in the target namespace first, or include them in the change.",
                          evidence=problems[:8], source=src))
    classes = {meta(sc).get("name") for sc in live.all("storageclasses")}
    for d in docs:
        claims = [d] if d.get("kind") == "PersistentVolumeClaim" else \
            ((d.get("spec") or {}).get("volumeClaimTemplates") or []) if d.get("kind") == "StatefulSet" else []
        for c in claims:
            sc = (c.get("spec") or {}).get("storageClassName")
            if sc and live.can_read("storageclasses") and sc not in classes:
                out.append(_f("preflight-storageclass-missing", "StorageClass does not exist", Severity.HIGH,
                              Category.STORAGE, _ref(d), f"StorageClass '{sc}' is not in {live.context} "
                              f"(available: {', '.join(sorted(c for c in classes if c)) or 'none'}). Volumes will stay Pending.",
                              "Use an existing StorageClass.", source=_source(d)))
        if d.get("kind") == "Ingress":
            cls = (d.get("spec") or {}).get("ingressClassName")
            names = {meta(i).get("name") for i in live.all("ingressclasses")}
            if cls and names and cls not in names:
                out.append(_f("preflight-ingressclass-missing", "IngressClass does not exist", Severity.HIGH,
                              Category.NETWORK, _ref(d), f"ingressClassName '{cls}' has no controller in {live.context}.",
                              f"Use one of: {', '.join(sorted(n for n in names if n))}.", source=_source(d)))
            for rule in (d.get("spec") or {}).get("rules") or []:
                for path in (rule.get("http") or {}).get("paths") or []:
                    svc = ((path.get("backend") or {}).get("service") or {}).get("name")
                    if svc and exists("Service", meta(d).get("namespace"), svc) is None:
                        out.append(_f("preflight-dependency-missing", "Change depends on something missing",
                                      Severity.HIGH, Category.NETWORK, _ref(d),
                                      f"Ingress routes {rule.get('host', '*')}{path.get('path', '/')} to Service '{svc}', "
                                      "which does not exist.", "Deploy the Service with this change.", source=_source(d)))
    return out


def _capacity(docs: list[dict[str, Any]], live: Snapshot) -> list[Finding]:
    out: list[Finding] = []
    nodes = live.all("nodes")
    if not nodes:
        return out
    used: dict[str, list[float]] = {meta(n).get("name"): [0.0, 0.0] for n in nodes}
    for p in live.all("pods"):
        node = (p.get("spec") or {}).get("nodeName")
        if node in used and (p.get("status") or {}).get("phase") in ("Running", "Pending"):
            cpu, mem = _pod_requests(p.get("spec") or {})
            used[node][0] += cpu
            used[node][1] += mem
    free = []
    for n in nodes:
        if (n.get("spec") or {}).get("unschedulable"):
            continue
        ready = any(c.get("type") == "Ready" and c.get("status") == "True"
                    for c in (n.get("status") or {}).get("conditions") or [])
        if not ready:
            continue
        alloc = (n.get("status") or {}).get("allocatable") or {}
        name = meta(n).get("name")
        free.append((n, parse_quantity(alloc.get("cpu")) - used[name][0], parse_quantity(alloc.get("memory")) - used[name][1]))
    for d in docs:
        if d.get("kind") not in _WORKLOADS:
            continue
        spec = pod_spec_of(d) or {}
        cpu, mem = _pod_requests(spec)
        if not cpu and not mem:
            continue
        sel = spec.get("nodeSelector") or {}
        candidates = [(n, c, m) for n, c, m in free if selector_matches(sel, meta(n).get("labels")) or not sel]
        if not any(c >= cpu and m >= mem for _, c, m in candidates):
            best = max(candidates, key=lambda x: (x[1], x[2]), default=None)
            detail = f"most free node: {fmt_cpu(max(best[1], 0))} CPU, {fmt_bytes(max(best[2], 0))} memory" if best else \
                "no ready node matches the nodeSelector"
            out.append(_f("preflight-will-not-fit", "Pods will not fit on any node", Severity.HIGH, Category.SCHEDULING,
                          _ref(d), f"Each pod requests {fmt_cpu(cpu)} CPU and {fmt_bytes(mem)} memory; no ready node has that "
                          f"free right now ({detail}). New pods will stay Pending unless the autoscaler adds nodes.",
                          "Lower the requests, free capacity, or confirm the cluster autoscaler can add a node this size.",
                          source=_source(d)))
        # ResourceQuota headroom: only the increase over what the running version already uses counts
        ns = meta(d).get("namespace")
        key = KIND_TO_KEY.get(d.get("kind", ""))
        cur = live.get(key, ns, meta(d).get("name", "")) if key else None
        old_cpu, old_mem = _pod_requests(pod_spec_of(cur) or {}) if cur else (0.0, 0.0)
        old_n = _replicas(cur) if cur else 0
        for q in live.in_namespace("resourcequotas", ns):
            hard, usedq = (q.get("status") or {}).get("hard") or {}, (q.get("status") or {}).get("used") or {}
            for res_name, need, old in (("requests.cpu", cpu, old_cpu), ("requests.memory", mem, old_mem)):
                if res_name not in hard:
                    continue
                left = parse_quantity(hard[res_name]) - parse_quantity(usedq.get(res_name))
                extra = need * _replicas(d) - old * old_n
                if extra > 0 and extra > left:
                    human = fmt_bytes if "memory" in res_name else fmt_cpu
                    out.append(_f("preflight-quota-exceeded", "ResourceQuota too small for this change",
                                  Severity.HIGH, Category.RESOURCES, _ref(d),
                                  f"Needs {human(extra)} more {res_name} but quota {meta(q).get('name')} has "
                                  f"{human(max(left, 0))} left; new pods will be rejected.",
                                  "Raise the quota or lower requests/replicas.", source=_source(d)))
        # PodDisruptionBudgets that will block every drain
        labels = (((d.get("spec") or {}).get("template") or {}).get("metadata") or {}).get("labels") or {}
        for pdb in live.in_namespace("pdbs", ns) + [x for x in docs if x.get("kind") == "PodDisruptionBudget"
                                                    and meta(x).get("namespace") == ns]:
            if not label_selector_matches((pdb.get("spec") or {}).get("selector") or {}, labels):
                continue
            min_av = (pdb.get("spec") or {}).get("minAvailable")
            if isinstance(min_av, int) and min_av >= _replicas(d):
                out.append(_f("preflight-pdb-blocks", "PDB will block node drains", Severity.MEDIUM, Category.UPGRADE,
                              _ref(d), f"{_replicas(d)} replica(s) with PodDisruptionBudget minAvailable={min_av}: no pod "
                              "can ever be evicted, so node upgrades will hang.",
                              "Use maxUnavailable: 1 or add replicas.", source=_source(d)))
    return out


def _diff_live(docs: list[dict[str, Any]], live: Snapshot) -> tuple[list[dict[str, Any]], list[Finding]]:
    changes, risky = [], []
    usage = live.metrics.get("pods") or {}
    for d in docs:
        key = KIND_TO_KEY.get(d.get("kind", ""))
        ns, name = meta(d).get("namespace"), meta(d).get("name", "")
        cur = live.get(key, ns, name) if key else None
        if cur is None:
            changes.append({"resource": str(_ref(d)), "action": "create", "details": []})
            continue
        details = _template_diff(cur, d) if d.get("kind") in ("Deployment", "StatefulSet", "DaemonSet") else []
        old_r, new_r = (cur.get("spec") or {}).get("replicas"), (d.get("spec") or {}).get("replicas")
        if old_r is not None and new_r is not None and old_r != new_r:
            details.append(f"replicas {old_r} -> {new_r}")
        changes.append({"resource": str(_ref(d)), "action": "update" if details else "unchanged", "details": details})
        removed_env = [x for x in details if "env removed" in x]
        if removed_env:
            risky.append(_f("preflight-risky-change", "Risky change", Severity.HIGH, Category.CONFIG, _ref(d),
                            f"This change removes environment variables the running version uses ({'; '.join(removed_env)}). "
                            "Crash loops after a deploy are most often caused by exactly this.",
                            "Confirm the new version no longer reads them before deploying.", source=_source(d)))
        if new_r == 0 and old_r:
            risky.append(_f("preflight-risky-change", "Risky change", Severity.HIGH, Category.WORKLOAD, _ref(d),
                            f"Scales {name} from {old_r} replicas to 0: it will stop serving.", source=_source(d)))
        # New memory limit below what the app uses right now: it will be OOMKilled.
        new_spec, cur_pods = pod_spec_of(d) or {}, [p for p in live.in_namespace("pods", ns)
                                                    if live.owner_of(p).name == name]
        for c in containers_of(new_spec):
            lim = parse_quantity(((c.get("resources") or {}).get("limits") or {}).get("memory"))
            if not lim:
                continue
            peak = max((usage.get(f"{ns}/{meta(p).get('name')}/{c.get('name')}", {}).get("memory", 0.0) for p in cur_pods),
                       default=0.0)
            if peak and lim < peak:
                risky.append(_f("preflight-will-oom", "New memory limit is below current usage", Severity.CRITICAL,
                                Category.RESOURCES, _ref(d),
                                f"Container '{c.get('name')}' gets a {fmt_bytes(lim)} memory limit but uses {fmt_bytes(peak)} "
                                "right now: it will be OOMKilled after the deploy.",
                                f"Keep the limit above {fmt_bytes(peak * 1.25)}.", source=_source(d)))
    return changes, risky


# ------------------------------------------------------------------ images
_TAG = re.compile(r"^(?:(?P<reg>[^/]+\.[^/]+|localhost(?::\d+)?)/)?(?P<repo>[^:@]+)(?::(?P<tag>[^@]+))?(?:@(?P<digest>.+))?$")


def _public_https(url: str) -> bool:
    """Only talk to public registries over HTTPS: never loopback, private, link-local or metadata addresses."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        return False
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def image_exists(image: str, timeout: float = 10.0) -> bool | None:
    """True/False if the registry answered, None if it could not be checked (private, unreachable)."""
    m = _TAG.match(image)
    if not m:
        return None
    reg = m.group("reg") or "registry-1.docker.io"
    repo = m.group("repo")
    if image_registry(image) == "docker.io":
        reg = "registry-1.docker.io"
        if "/" not in repo:
            repo = f"library/{repo}"
    ref = m.group("digest") or m.group("tag") or "latest"
    url = f"https://{reg}/v2/{repo}/manifests/{ref}"
    accept = ", ".join(["application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
                        "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"])
    if not _public_https(url):
        return None
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as http:
            r = http.head(url, headers={"Accept": accept})
            if r.status_code == 401 and "bearer" in r.headers.get("www-authenticate", "").lower():
                params = dict(re.findall(r'(\w+)="([^"]*)"', r.headers["www-authenticate"]))
                if not _public_https(params.get("realm", "")):
                    return None  # the token endpoint is chosen by the registry: check it too
                tok = http.get(params.get("realm", ""), params={"service": params.get("service"),
                                                                "scope": params.get("scope", f"repository:{repo}:pull")})
                if tok.status_code != 200:
                    return None
                token = tok.json().get("token") or tok.json().get("access_token")
                r = http.head(url, headers={"Accept": accept, "Authorization": f"Bearer {token}"})
            if r.status_code == 200:
                return True
            if r.status_code == 404:
                return False
            return None  # 401/403: private image, can't tell
    except httpx.HTTPError:
        return None


def _images(docs: list[dict[str, Any]]) -> list[Finding]:
    out, seen = [], {}
    for d in docs:
        for c in containers_of(pod_spec_of(d) if d.get("kind") in _WORKLOADS else None, include_init=True):
            img = c.get("image", "")
            if img not in seen:
                seen[img] = image_exists(img)
            if seen[img] is False:
                out.append(_f("preflight-image-missing", "Image does not exist", Severity.CRITICAL, Category.WORKLOAD,
                              _ref(d), f"Image '{img}' was not found in its registry: pods will fail with ImagePullBackOff.",
                              "Check the tag was pushed (CI may not have finished) and the name is spelled right.",
                              source=_source(d)))
    return out


# ------------------------------------------------------------------ server-side dry run
def _api_path(doc: dict[str, Any]) -> str | None:
    kind, api = doc.get("kind", ""), doc.get("apiVersion", "")
    entry = next((e for e in KINDS if e[1] == kind), None)
    if entry is None:
        return None
    _, _, prefix, plural, namespaced, _ = entry
    if not re.match(r"^[a-z0-9.-]+(/v[0-9a-z]+)?$|^v1$", api):
        return None
    prefix = "/api/v1" if api == "v1" else f"/apis/{api}"
    name = meta(doc).get("name", "")
    try:
        check_name(name)
        check_name(meta(doc).get("namespace"), "namespace")
    except ValueError:
        return None
    if namespaced:
        return f"{prefix}/namespaces/{meta(doc).get('namespace')}/{plural}/{name}"
    return f"{prefix}/{plural}/{name}"


def _server_dry_run(docs: list[dict[str, Any]], client: KubeClient) -> list[Finding]:
    out = []
    for d in docs:
        path = _api_path(d)
        if not path:
            continue
        body = json.loads(json.dumps(d))
        (body.get("metadata") or {}).get("annotations", {}).pop("kubesift.dev/source", None)
        try:
            client.request("PATCH", path, query={"dryRun": "All", "fieldManager": "kubesift-preflight", "force": "true"},
                           body=json.dumps(body), content_type="application/apply-patch+yaml")  # JSON is valid YAML
        except KubeError as exc:
            # 403 is used both for RBAC ("... is forbidden: User X cannot patch ...") and for admission
            # denials ("admission webhook ... denied the request"). Only the first means "could not check".
            if exc.status == 401 or (exc.status == 403 and re.search(r"is forbidden: User .* cannot", exc.message)):
                out.append(_f("preflight-dry-run-skipped", "Server-side dry run not permitted", Severity.INFO,
                              Category.PLATFORM, _ref(d), f"No permission to dry-run apply: {exc.message[:160]}",
                              source=_source(d)))
                continue
            out.append(_f("preflight-rejected", "The cluster would reject this change", Severity.CRITICAL,
                          Category.PLATFORM, _ref(d),
                          f"Server-side dry run failed (HTTP {exc.status}): {exc.message[:400]}",
                          "This is what the API server, admission webhooks, Pod Security or quotas would answer on deploy.",
                          source=_source(d)))
    return out
