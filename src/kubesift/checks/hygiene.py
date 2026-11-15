"""Configuration risks found in pod templates. These run on live clusters and on manifest files."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from kubesift.checks.base import Context, check
from kubesift.models import Category, Finding, Severity
from kubesift.redact import SECRET_ENV_NAME
from kubesift.util import (
    containers_of,
    image_registry,
    image_tag,
    label_selector_matches,
    meta,
    pod_spec_of,
)


def templates(ctx: Context) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    for w in ctx.snap.workloads():
        if ctx.snap.live and ctx.is_system(meta(w).get("namespace")):
            continue
        spec = pod_spec_of(w)
        if spec:
            yield w, spec


def _long_running(obj: dict[str, Any]) -> bool:
    return obj.get("kind") in ("Deployment", "StatefulSet", "DaemonSet", "Pod")


@check("probe-missing-readiness", "No readiness probe", Category.RELIABILITY, Severity.MEDIUM, live_only=False, risk=True)
def missing_readiness(ctx: Context) -> Iterator[Finding]:
    """Without a readiness probe, traffic reaches pods before they can serve and during shutdown."""
    for obj, spec in templates(ctx):
        if obj.get("kind") not in ("Deployment", "StatefulSet"):
            continue
        missing = [c.get("name") for c in containers_of(spec) if not c.get("readinessProbe")]
        if missing and any(c.get("ports") for c in containers_of(spec)):
            yield ctx.finding("probe-missing-readiness", obj, f"Containers {', '.join(missing)} have no readinessProbe.",
                              remediation="Add a readinessProbe that checks the app can serve requests (e.g. GET /ready).")


@check("probe-liveness-equals-readiness", "Liveness probe is the same as readiness", Category.RELIABILITY,
       Severity.MEDIUM, live_only=False, risk=True)
def liveness_equals_readiness(ctx: Context) -> Iterator[Finding]:
    """When a dependency slows down, identical probes restart every pod at once (cascading failure)."""
    for obj, spec in templates(ctx):
        for c in containers_of(spec):
            live, ready = c.get("livenessProbe"), c.get("readinessProbe")
            if not live or not ready:
                continue
            strip = lambda p: {k: v for k, v in p.items() if k in ("httpGet", "tcpSocket", "exec", "grpc")}  # noqa: E731
            if strip(live) == strip(ready):
                yield ctx.finding(
                    "probe-liveness-equals-readiness", obj,
                    f"Container '{c.get('name')}' uses the same check for liveness and readiness.",
                    remediation="Liveness should only check that the process is alive (no dependency calls); "
                                "readiness can check dependencies. Otherwise a slow database restarts every replica.")


@check("probe-no-startup", "Slow-start liveness without startupProbe", Category.RELIABILITY, Severity.LOW,
       live_only=False, risk=True)
def aggressive_liveness(ctx: Context) -> Iterator[Finding]:
    """A liveness probe that can fail within 30s of start and no startupProbe kills slow-starting apps."""
    for obj, spec in templates(ctx):
        for c in containers_of(spec):
            p = c.get("livenessProbe")
            if not p or c.get("startupProbe"):
                continue
            budget = p.get("initialDelaySeconds", 0) + p.get("periodSeconds", 10) * p.get("failureThreshold", 3)
            if budget < 30:
                yield ctx.finding(
                    "probe-no-startup", obj,
                    f"Container '{c.get('name')}' is killed if not healthy within {budget}s of starting.",
                    remediation="Add a startupProbe (e.g. failureThreshold: 30, periodSeconds: 5) so slow starts are not killed.")


@check("resources-missing", "No resource requests or memory limit", Category.RESOURCES, Severity.MEDIUM,
       live_only=False, risk=True)
def resources_missing(ctx: Context) -> Iterator[Finding]:
    """Without requests the scheduler overpacks nodes; without a memory limit one pod can starve a node."""
    for obj, spec in templates(ctx):
        for c in containers_of(spec):
            res = c.get("resources") or {}
            req, lim = res.get("requests") or {}, res.get("limits") or {}
            gaps = []
            if not req.get("cpu") and not lim.get("cpu"):
                gaps.append("cpu request")
            if not req.get("memory") and not lim.get("memory"):
                gaps.append("memory request")
            if not lim.get("memory"):
                gaps.append("memory limit")
            if gaps:
                yield ctx.finding(
                    "resources-missing", obj, f"Container '{c.get('name')}' has no {', '.join(gaps)}.",
                    remediation="Set requests to typical usage and a memory limit above peak usage "
                                "(`kubesift cost` suggests values from real usage).",
                    severity=Severity.MEDIUM if "memory request" in gaps else Severity.LOW)


@check("image-mutable-tag", "Image uses latest or no tag", Category.RELIABILITY, Severity.MEDIUM,
       live_only=False, risk=True)
def mutable_tag(ctx: Context) -> Iterator[Finding]:
    """`latest` or untagged images change underneath you and make rollbacks impossible."""
    for obj, spec in templates(ctx):
        for c in containers_of(spec, include_init=True):
            tag = image_tag(c.get("image", ""))
            if tag in (None, "latest"):
                yield ctx.finding("image-mutable-tag", obj,
                                  f"Container '{c.get('name')}' uses image '{c.get('image')}' with "
                                  f"{'no tag' if tag is None else 'the latest tag'}.",
                                  remediation="Pin a version tag or digest so every node runs the same code and rollbacks work.")


@check("image-dockerhub-anonymous", "Images pulled from Docker Hub without credentials", Category.RELIABILITY,
       Severity.LOW, live_only=False, risk=True)
def dockerhub_anonymous(ctx: Context) -> Iterator[Finding]:
    """Anonymous Docker Hub pulls are rate limited per IP; scale-ups and node replacements can fail."""
    count = 0
    first = None
    for obj, spec in templates(ctx):
        if spec.get("imagePullSecrets"):
            continue
        if any(image_registry(c.get("image", "")) == "docker.io" for c in containers_of(spec, include_init=True)):
            count += 1
            first = first or obj
    if first is not None and count:
        yield ctx.finding(
            "image-dockerhub-anonymous", first,
            f"{count} workload(s) pull from Docker Hub without an imagePullSecret and can hit pull rate limits.",
            remediation="Mirror images to your registry or a pull-through cache (ECR, Artifact Registry, Harbor), "
                        "or authenticate pulls.")


@check("security-privileged", "Privileged or host-level access", Category.SECURITY, Severity.HIGH,
       live_only=False, risk=True)
def privileged(ctx: Context) -> Iterator[Finding]:
    """privileged, hostNetwork/hostPID/hostIPC, hostPath or dangerous capabilities: a container escape means node takeover."""
    for obj, spec in templates(ctx):
        issues = []
        for flag in ("hostNetwork", "hostPID", "hostIPC"):
            if spec.get(flag):
                issues.append(flag)
        for v in spec.get("volumes") or []:
            if v.get("hostPath"):
                issues.append(f"hostPath {v['hostPath'].get('path')}")
        for c in containers_of(spec, include_init=True):
            sc = c.get("securityContext") or {}
            if sc.get("privileged"):
                issues.append(f"privileged container {c.get('name')}")
            caps = (sc.get("capabilities") or {}).get("add") or []
            bad = [x for x in caps if x in ("SYS_ADMIN", "NET_ADMIN", "ALL", "SYS_PTRACE", "SYS_MODULE")]
            if bad:
                issues.append(f"{c.get('name')} adds {','.join(bad)}")
        if issues:
            yield ctx.finding("security-privileged", obj, "Host-level access: " + "; ".join(issues[:5]) + ".",
                              severity=Severity.HIGH if any("privileged" in i or "SYS_ADMIN" in i for i in issues) else Severity.MEDIUM,
                              remediation="Remove what the app does not need. Node agents may need it; application workloads should not.")


@check("security-runs-as-root", "Container may run as root", Category.SECURITY, Severity.LOW, live_only=False, risk=True)
def runs_as_root(ctx: Context) -> Iterator[Finding]:
    """No runAsNonRoot and allowPrivilegeEscalation not disabled (fails the 'restricted' Pod Security level)."""
    for obj, spec in templates(ctx):
        psc = spec.get("securityContext") or {}
        for c in containers_of(spec):
            sc = c.get("securityContext") or {}
            non_root = sc.get("runAsNonRoot", psc.get("runAsNonRoot")) or (sc.get("runAsUser", psc.get("runAsUser")) or 0) > 0
            if not non_root or sc.get("allowPrivilegeEscalation") is not False:
                yield ctx.finding(
                    "security-runs-as-root", obj,
                    f"Container '{c.get('name')}' does not set runAsNonRoot and allowPrivilegeEscalation: false.",
                    remediation="Set securityContext: {runAsNonRoot: true, allowPrivilegeEscalation: false, "
                                "capabilities: {drop: [ALL]}, seccompProfile: {type: RuntimeDefault}}.")
                break


@check("security-plaintext-secret", "Secret value written directly in env", Category.SECURITY, Severity.HIGH,
       live_only=False, risk=True)
def plaintext_secret(ctx: Context) -> Iterator[Finding]:
    """An env var named like a password/token has a literal value: it is visible to anyone who can read the workload."""
    for obj, spec in templates(ctx):
        for c in containers_of(spec, include_init=True):
            for env in c.get("env") or []:
                value = env.get("value")
                if value and len(str(value)) >= 6 and SECRET_ENV_NAME.search(env.get("name", "")) \
                        and not str(value).startswith("$("):
                    yield ctx.finding(
                        "security-plaintext-secret", obj,
                        f"Env var {env.get('name')} in container '{c.get('name')}' holds a literal value that looks like a secret.",
                        remediation="Move it to a Secret (or an external secret store) and use valueFrom.secretKeyRef. Rotate the value.")


@check("security-gitrepo-volume", "gitRepo volume (disabled in 1.36)", Category.UPGRADE, Severity.HIGH,
       live_only=False, risk=True)
def gitrepo_volume(ctx: Context) -> Iterator[Finding]:
    """gitRepo volumes are a known remote-code-execution vector and are disabled from Kubernetes 1.36."""
    for obj, spec in templates(ctx):
        if any(v.get("gitRepo") for v in spec.get("volumes") or []):
            yield ctx.finding("security-gitrepo-volume", obj, "Uses a gitRepo volume, which Kubernetes 1.36 no longer supports.",
                              remediation="Clone in an init container into an emptyDir instead.")


@check("reliability-single-replica", "Single replica", Category.RELIABILITY, Severity.LOW, live_only=False, risk=True)
def single_replica(ctx: Context) -> Iterator[Finding]:
    """One replica means downtime on every deploy, node drain and crash."""
    for obj, _ in templates(ctx):
        if obj.get("kind") != "Deployment":
            continue
        if (obj.get("spec") or {}).get("replicas", 1) == 1 and not _has_hpa(ctx, obj):
            yield ctx.finding("reliability-single-replica", obj, "Runs a single replica, so any restart is an outage.",
                              remediation="Run at least 2 replicas (with a PodDisruptionBudget) for anything user-facing.")


def _has_hpa(ctx: Context, obj: dict[str, Any]) -> bool:
    for h in ctx.snap.in_namespace("hpas", meta(obj).get("namespace")):
        ref = (h.get("spec") or {}).get("scaleTargetRef") or {}
        if ref.get("kind") == obj.get("kind") and ref.get("name") == meta(obj).get("name"):
            return (h.get("spec") or {}).get("minReplicas", 1) > 1
    return False


@check("reliability-no-pdb", "No PodDisruptionBudget", Category.RELIABILITY, Severity.LOW, live_only=False, risk=True)
def no_pdb(ctx: Context) -> Iterator[Finding]:
    """Without a PDB, node drains during upgrades can evict all replicas at once."""
    for obj, _ in templates(ctx):
        if obj.get("kind") not in ("Deployment", "StatefulSet") or (obj.get("spec") or {}).get("replicas", 1) < 2:
            continue
        labels = (((obj.get("spec") or {}).get("template") or {}).get("metadata") or {}).get("labels") or {}
        if not any(label_selector_matches((p.get("spec") or {}).get("selector") or {}, labels)
                   for p in ctx.snap.in_namespace("pdbs", meta(obj).get("namespace"))):
            yield ctx.finding("reliability-no-pdb", obj, "Has several replicas but no PodDisruptionBudget.",
                              remediation="Add a PDB with maxUnavailable: 1 so drains keep the service up.")


@check("pdb-blocks-drain", "PodDisruptionBudget allows no disruptions", Category.UPGRADE, Severity.HIGH, risk=True)
def pdb_blocks(ctx: Context) -> Iterator[Finding]:
    """disruptionsAllowed=0 makes node drains hang: node upgrades and autoscaler scale-down get stuck."""
    for pdb in ctx.snap.all("pdbs"):
        st = pdb.get("status") or {}
        if st.get("expectedPods", 0) > 0 and st.get("disruptionsAllowed", 1) == 0:
            healthy, want = st.get("currentHealthy", 0), st.get("desiredHealthy", 0)
            yield ctx.finding(
                "pdb-blocks-drain", pdb,
                f"Allows 0 disruptions ({healthy} healthy, {want} required): draining nodes that run these pods will hang.",
                evidence=[f"spec: {pdb.get('spec', {}).get('minAvailable') or pdb.get('spec', {}).get('maxUnavailable')}"],
                remediation="Use maxUnavailable: 1 instead of minAvailable equal to the replica count, or add replicas; "
                            "if pods are unhealthy, fix them first.")


@check("rbac-cluster-admin-sa", "Service account has cluster-admin", Category.SECURITY, Severity.HIGH, risk=True)
def cluster_admin_sa(ctx: Context) -> Iterator[Finding]:
    """A pod token with cluster-admin means anyone who compromises the pod owns the cluster."""
    for b in ctx.snap.all("clusterrolebindings"):
        if (b.get("roleRef") or {}).get("name") != "cluster-admin":
            continue
        for s in b.get("subjects") or []:
            if s.get("kind") == "ServiceAccount" and not ctx.is_system(s.get("namespace")):
                yield ctx.finding("rbac-cluster-admin-sa", b,
                                  f"ServiceAccount {s.get('namespace')}/{s.get('name')} is bound to cluster-admin.",
                                  remediation="Grant only the verbs and resources it needs with a dedicated (Cluster)Role.")
