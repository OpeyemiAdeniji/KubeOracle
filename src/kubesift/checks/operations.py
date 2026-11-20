"""Day-to-day operational failures from public postmortems: rollout 502s, stale config, stuck namespaces,
fragile DNS, single-node replicas, autoscaler thrashing, Karpenter, and baseline security gaps."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from kubesift.analysis.changes import last_user_update
from kubesift.checks.base import Context, check
from kubesift.checks.hygiene import templates
from kubesift.cluster.snapshot import event_time
from kubesift.models import Category, Finding, ResourceRef, Severity
from kubesift.util import (
    age,
    containers_of,
    human_duration,
    meta,
    parse_time,
    selector_matches,
)


def _prestop_sleep(container: dict[str, Any]) -> int | None:
    pre = ((container.get("lifecycle") or {}).get("preStop")) or {}
    if not pre:
        return None
    if pre.get("sleep"):
        return int(pre["sleep"].get("seconds", 0))
    cmd = " ".join(str(x) for x in ((pre.get("exec") or {}).get("command") or []))
    m = re.search(r"sleep\s+(\d+)", cmd)
    return int(m.group(1)) if m else 0


@check("rollout-drops-traffic", "Rollouts can drop live traffic (502s)", Category.RELIABILITY, Severity.MEDIUM,
       live_only=False, risk=True)
def rollout_drops_traffic(ctx: Context) -> Iterator[Finding]:
    """Pods behind a Service with no preStop delay stop before load balancers stop routing to them,
    so every deploy returns 502s / connection resets for a few seconds."""
    for obj, spec in templates(ctx):
        if obj.get("kind") not in ("Deployment", "StatefulSet"):
            continue
        labels = (((obj.get("spec") or {}).get("template") or {}).get("metadata") or {}).get("labels") or {}
        ns = meta(obj).get("namespace")
        served = any(selector_matches((s.get("spec") or {}).get("selector"), labels)
                     for s in ctx.snap.in_namespace("services", ns))
        if not served:
            continue
        grace = spec.get("terminationGracePeriodSeconds", 30)
        serving = [c for c in containers_of(spec) if c.get("ports")]
        for c in serving:
            sleep = _prestop_sleep(c)
            if sleep is None:
                yield ctx.finding(
                    "rollout-drops-traffic", obj,
                    f"Container '{c.get('name')}' has no preStop hook. During rollouts and scale-downs it gets SIGTERM "
                    "while load balancers and kube-proxy still send it traffic, so some requests fail.",
                    remediation="Add `lifecycle: {preStop: {sleep: {seconds: 10}}}` (or `exec: [sleep, '10']` on clusters "
                                "before 1.30), make the app drain on SIGTERM, and keep terminationGracePeriodSeconds above "
                                "the sleep plus the app's shutdown time.")
                break
            if sleep and grace <= sleep:
                yield ctx.finding(
                    "rollout-drops-traffic", obj,
                    f"terminationGracePeriodSeconds ({grace}s) is not longer than the preStop sleep ({sleep}s): the pod "
                    "is killed before the app can finish in-flight requests.",
                    severity=Severity.HIGH,
                    remediation=f"Set terminationGracePeriodSeconds to at least {sleep + 20}.")
                break


@check("config-stale-in-pod", "Pods run with old config values", Category.CONFIG, Severity.MEDIUM)
def config_stale(ctx: Context) -> Iterator[Finding]:
    """A ConfigMap/Secret used through env vars changed after the pod started; env vars are only read at start."""
    for pod in ctx.snap.all("pods"):
        st = pod.get("status") or {}
        if st.get("phase") != "Running":
            continue
        started = parse_time(st.get("startTime"))
        if not started:
            continue
        ns = meta(pod).get("namespace")
        stale = []
        for c in containers_of(pod.get("spec"), include_init=True):
            refs: set[tuple[str, str]] = set()
            for ef in c.get("envFrom") or []:
                if ef.get("configMapRef"):
                    refs.add(("configmaps", ef["configMapRef"].get("name", "")))
                if ef.get("secretRef"):
                    refs.add(("secrets", ef["secretRef"].get("name", "")))
            for env in c.get("env") or []:
                vf = env.get("valueFrom") or {}
                if vf.get("configMapKeyRef"):
                    refs.add(("configmaps", vf["configMapKeyRef"].get("name", "")))
                if vf.get("secretKeyRef"):
                    refs.add(("secrets", vf["secretKeyRef"].get("name", "")))
            for key, name in refs:
                obj = ctx.snap.get(key, ns, name)
                changed, who = last_user_update(obj) if obj else (None, None)
                if changed and changed > started + timedelta(seconds=30):
                    stale.append(((obj or {}).get("kind"), name, changed, who))
        if stale:
            kind, name, changed, who = stale[0]
            yield ctx.finding(
                "config-stale-in-pod", pod,
                f"{kind} '{name}' was changed at {changed:%Y-%m-%d %H:%M} UTC" + (f" by {who}" if who else "")
                + ", after this pod started. It still uses the old values (env vars are read only at start).",
                evidence=[f"pod started {st.get('startTime')}"] + [f"{k} {n} updated {t:%H:%M}" for k, n, t, _ in stale[:4]],
                remediation="Restart the workload to pick up the change, or add a config checksum annotation to the pod "
                            "template (or use a reloader) so changes roll out automatically.",
                commands=[f"kubectl rollout restart {ctx.snap.owner_of(pod).kind.lower()}/{ctx.snap.owner_of(pod).name} -n {ns}"]
                if ctx.snap.owner_of(pod).kind in ("Deployment", "StatefulSet", "DaemonSet") else [],
            )


@check("namespace-stuck-terminating", "Namespace stuck deleting", Category.PLATFORM, Severity.HIGH)
def namespace_stuck(ctx: Context) -> Iterator[Finding]:
    """A namespace has been Terminating for 10+ minutes, usually a finalizer or an unavailable APIService."""
    for ns in ctx.snap.all("namespaces"):
        if (ns.get("status") or {}).get("phase") != "Terminating":
            continue
        a = age(meta(ns).get("deletionTimestamp"))
        if a is not None and a < timedelta(minutes=10):
            continue
        conds = [c for c in (ns.get("status") or {}).get("conditions") or [] if c.get("status") == "True"]
        reason = conds[0].get("message", "") if conds else "resources with finalizers are still present"
        hint = "Remove what blocks it; force-removing finalizers can leave cloud resources behind."
        if any(c.get("type") in ("NamespaceDeletionDiscoveryFailure", "NamespaceDeletionGroupVersionParsingFailure")
               for c in conds):
            hint = "An aggregated API (APIService) is unavailable, so Kubernetes cannot list everything to delete. " \
                   "Fix or delete the broken APIService (see the apiservice-unavailable finding)."
        yield ctx.finding(
            "namespace-stuck-terminating", ns, f"Terminating for {human_duration(a)}: {reason[:250]}",
            evidence=[f"{c.get('type')}: {c.get('message', '')[:200]}" for c in conds[:4]],
            remediation=hint,
            commands=[f"kubectl api-resources --verbs=list --namespaced -o name | xargs -n 1 kubectl get --show-kind "
                      f"--ignore-not-found -n {meta(ns).get('name')}"],
        )


@check("apiservice-unavailable", "Extension API is unavailable", Category.PLATFORM, Severity.HIGH)
def apiservice_unavailable(ctx: Context) -> Iterator[Finding]:
    """An aggregated APIService (metrics-server, custom metrics, ...) is down: it breaks HPAs, `kubectl top`,
    API discovery and namespace deletion."""
    for svc in ctx.snap.all("apiservices"):
        if not (svc.get("spec") or {}).get("service"):
            continue  # built-in, served by the API server itself
        cond: dict[str, Any] = next((c for c in (svc.get("status") or {}).get("conditions") or [] if c.get("type") == "Available"), {})
        if cond.get("status") != "False":
            continue
        backend = (svc.get("spec") or {}).get("service") or {}
        name = meta(svc).get("name", "")
        impact = "HPAs and `kubectl top` stop working" if "metrics" in name else "API discovery and namespace deletion fail"
        yield ctx.finding(
            "apiservice-unavailable", svc,
            f"{name} is unavailable ({cond.get('reason')}): {impact}.",
            evidence=[cond.get("message", "")[:300], f"backend: {backend.get('namespace')}/{backend.get('name')}"],
            remediation="Fix the backing Service/pods, or delete the APIService if its add-on was uninstalled.",
            commands=[f"kubectl get apiservice {name} -o yaml", f"kubectl -n {backend.get('namespace')} get pods"],
        )


@check("dns-fragile", "Cluster DNS is a single point of failure", Category.PLATFORM, Severity.MEDIUM, risk=True)
def dns_fragile(ctx: Context) -> Iterator[Finding]:
    """CoreDNS with one replica, or with a CPU limit (throttling made DNS fail cluster-wide in public postmortems)."""
    for d in ctx.snap.in_namespace("deployments", "kube-system"):
        labels = (((d.get("spec") or {}).get("template") or {}).get("metadata") or {}).get("labels") or {}
        if labels.get("k8s-app") != "kube-dns" and meta(d).get("name") != "coredns":
            continue
        replicas = (d.get("spec") or {}).get("replicas", 1)
        if replicas < 2:
            yield ctx.finding("dns-fragile", d, f"CoreDNS runs {replicas} replica: if that pod or its node fails, every "
                              "service-name lookup in the cluster fails.",
                              remediation="Run at least 2 replicas spread across nodes (or enable the dns-autoscaler).",
                              commands=["kubectl -n kube-system scale deployment coredns --replicas=2"])
        for c in containers_of((d.get("spec") or {}).get("template", {}).get("spec")):
            if ((c.get("resources") or {}).get("limits") or {}).get("cpu"):
                yield ctx.finding("dns-fragile", d, "CoreDNS has a CPU limit. Under load it gets CPU-throttled, lookups "
                                  "time out and apps retry, which makes it worse.",
                                  severity=Severity.LOW,
                                  remediation="Remove the CPU limit (keep a CPU request), consider NodeLocal DNSCache, and "
                                              "lower ndots for apps that call external hosts.")
                break


@check("replicas-single-node", "All replicas on one node or zone", Category.RELIABILITY, Severity.MEDIUM, risk=True)
def replicas_single_node(ctx: Context) -> Iterator[Finding]:
    """Several replicas but every pod is on the same node (or zone): one failure takes the whole app down."""
    zone_of = {meta(n).get("name"): (meta(n).get("labels") or {}).get("topology.kubernetes.io/zone")
               for n in ctx.snap.all("nodes")}
    if len(zone_of) < 2:
        return
    zones = {z for z in zone_of.values() if z}
    by_owner: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in ctx.snap.all("pods"):
        if (p.get("status") or {}).get("phase") == "Running" and (p.get("spec") or {}).get("nodeName") \
                and not ctx.is_system(meta(p).get("namespace")):
            by_owner[str(ctx.snap.owner_of(p))].append(p)
    for owner, pods in by_owner.items():
        if len(pods) < 2 or not owner.startswith(("Deployment/", "StatefulSet/")):
            continue
        nodes = {(p.get("spec") or {}).get("nodeName") for p in pods}
        pod_zones = {zone_of.get(n) for n in nodes}
        kind, ns, name = owner.split("/", 2)
        ref = ResourceRef(kind=kind, namespace=ns, name=name)
        if len(nodes) == 1:
            yield ctx.finding("replicas-single-node", ref,
                              f"All {len(pods)} replicas run on node {next(iter(nodes))}: losing that node is a full outage.",
                              severity=Severity.HIGH,
                              remediation="Add topologySpreadConstraints on kubernetes.io/hostname (whenUnsatisfiable: "
                                          "ScheduleAnyway) or pod anti-affinity.")
        elif len(zones) > 1 and len(pod_zones) == 1 and None not in pod_zones:
            yield ctx.finding("replicas-single-node", ref,
                              f"All {len(pods)} replicas run in zone {next(iter(pod_zones))}: a zone outage takes it down.",
                              remediation="Spread across zones with topologySpreadConstraints on topology.kubernetes.io/zone.")


@check("hpa-flapping", "Autoscaler keeps scaling up and down", Category.RESOURCES, Severity.MEDIUM)
def hpa_flapping(ctx: Context) -> Iterator[Finding]:
    """Frequent alternating rescales in the last hour: pods start and stop constantly, hurting latency and cost."""
    rescales: dict[tuple[str | None, str], list[dict[str, Any]]] = defaultdict(list)
    for e in ctx.snap.all("events"):
        inv = e.get("involvedObject") or {}
        if e.get("reason") != "SuccessfulRescale" or inv.get("kind") != "HorizontalPodAutoscaler":
            continue
        a = age(event_time(e))
        if a is not None and a <= timedelta(hours=1):
            rescales[(inv.get("namespace"), inv.get("name", ""))].append(e)
    for (ns, name), events in rescales.items():
        total = sum(e.get("count", 1) or 1 for e in events)
        text = " ".join(e.get("message", "") for e in events)
        if total >= 6 and "above target" in text and "below target" in text:
            hpa = ctx.snap.get("hpas", ns, name)
            yield ctx.finding(
                "hpa-flapping", hpa or ResourceRef(kind="HorizontalPodAutoscaler", namespace=ns, name=name),
                f"Rescaled {total} times in the last hour, both up and down.",
                evidence=[e.get("message", "")[:200] for e in events[:3]],
                remediation="Add a scale-down stabilization window (behavior.scaleDown.stabilizationWindowSeconds: 300), "
                            "check that requests match real usage, and avoid targets near normal load.",
            )


@check("karpenter-nodeclaim-failed", "Karpenter cannot launch nodes", Category.NODE, Severity.HIGH)
def karpenter_failed(ctx: Context) -> Iterator[Finding]:
    """A Karpenter NodeClaim has not become Ready: capacity errors, launch template or IAM problems."""
    for nc in ctx.snap.all("nodeclaims"):
        a = age(meta(nc).get("creationTimestamp"))
        if a is not None and a < timedelta(minutes=10):
            continue
        bad = next((c for c in (nc.get("status") or {}).get("conditions") or []
                    if c.get("type") in ("Launched", "Registered", "Initialized", "Ready") and c.get("status") == "False"),
                   None)
        if not bad:
            continue
        msg = bad.get("message", "")
        hint = "Check the Karpenter controller logs and the NodePool/EC2NodeClass."
        if re.search(r"InsufficientInstanceCapacity|insufficient capacity", msg, re.I):
            hint = "The cloud has no capacity for the allowed instance types: allow more types/zones or use on-demand."
        elif re.search(r"UnauthorizedOperation|AccessDenied|not authorized", msg, re.I):
            hint = "The Karpenter or node IAM role lacks a permission named in the message."
        yield ctx.finding("karpenter-nodeclaim-failed", nc,
                          f"NodeClaim {bad.get('type')}=False for {human_duration(a)} ({bad.get('reason')}): {msg[:200]}",
                          remediation=hint, commands=["kubectl -n kube-system logs deploy/karpenter --tail=100"])


@check("pvc-stuck-terminating", "Volume claim stuck deleting", Category.STORAGE, Severity.MEDIUM)
def pvc_stuck(ctx: Context) -> Iterator[Finding]:
    """PVC deletion is blocked by kubernetes.io/pvc-protection because a pod still mounts it."""
    for pvc in ctx.snap.all("pvcs"):
        deleted = meta(pvc).get("deletionTimestamp")
        if not deleted or (age(deleted) or timedelta()) < timedelta(minutes=10):
            continue
        ns, name = meta(pvc).get("namespace"), meta(pvc).get("name")
        users = [meta(p).get("name", "") for p in ctx.snap.in_namespace("pods", ns)
                 if any((v.get("persistentVolumeClaim") or {}).get("claimName") == name
                        for v in (p.get("spec") or {}).get("volumes") or [])]
        yield ctx.finding("pvc-stuck-terminating", pvc,
                          f"Deletion requested {human_duration(age(deleted))} ago but blocked"
                          + (f": still mounted by {', '.join(users[:3])}." if users else f" by finalizers {meta(pvc).get('finalizers')}."),
                          remediation="Delete or scale down the pods using it; the claim is removed automatically afterwards.")


def _app_namespaces(ctx: Context) -> list[str]:
    counts = Counter(meta(p).get("namespace") for p in ctx.snap.all("pods"))
    return sorted(ns for ns in counts if ns and not ctx.is_system(ns) and ns not in ("ingress-nginx", "cert-manager"))


@check("security-pss-not-enforced", "Pod Security Standards not enforced", Category.SECURITY, Severity.LOW, risk=True)
def pss_not_enforced(ctx: Context) -> Iterator[Finding]:
    """Application namespaces without a pod-security.kubernetes.io/enforce label accept privileged pods."""
    if not ctx.snap.can_read("namespaces"):
        return
    labels = {meta(n).get("name"): meta(n).get("labels") or {} for n in ctx.snap.all("namespaces")}
    missing = [ns for ns in _app_namespaces(ctx) if "pod-security.kubernetes.io/enforce" not in labels.get(ns, {})]
    if missing:
        yield ctx.finding("security-pss-not-enforced", ResourceRef(kind="Namespace", name=missing[0]),
                          f"{len(missing)} application namespace(s) don't enforce a Pod Security level: "
                          f"{', '.join(missing[:6])}{'…' if len(missing) > 6 else ''}.",
                          remediation="Label them with pod-security.kubernetes.io/enforce=baseline (or restricted); start "
                                      "with the warn label to see what would break.",
                          commands=[f"kubectl label namespace {missing[0]} pod-security.kubernetes.io/warn=restricted"])


@check("security-no-network-policy", "Namespaces with no NetworkPolicy", Category.SECURITY, Severity.LOW, risk=True)
def no_network_policy(ctx: Context) -> Iterator[Finding]:
    """Without any NetworkPolicy every pod can reach every other pod: one compromised pod reaches all of them."""
    if not ctx.snap.can_read("networkpolicies"):
        return
    covered = {meta(n).get("namespace") for n in ctx.snap.all("networkpolicies")}
    missing = [ns for ns in _app_namespaces(ctx) if ns not in covered]
    if missing:
        yield ctx.finding("security-no-network-policy", ResourceRef(kind="Namespace", name=missing[0]),
                          f"{len(missing)} application namespace(s) have no NetworkPolicy: {', '.join(missing[:6])}"
                          f"{'…' if len(missing) > 6 else ''}.",
                          remediation="Start with a default-deny ingress policy per namespace plus explicit allows "
                                      "(remember to allow DNS egress if you also restrict egress).")
