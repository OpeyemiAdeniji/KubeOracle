"""Node health, capacity and version skew."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from kubeoracle.checks.base import Context, check
from kubeoracle.models import Category, Finding, Severity
from kubeoracle.util import age, containers_of, human_duration, meta, parse_quantity, parse_time


def minor(version: str | None) -> int | None:
    m = re.match(r"v?1\.(\d+)", version or "")
    return int(m.group(1)) if m else None


def _cond(node: dict[str, Any], ctype: str) -> dict[str, Any]:
    return next((c for c in (node.get("status") or {}).get("conditions") or [] if c.get("type") == ctype), {})


def pods_on(ctx: Context, node: str) -> list[dict[str, Any]]:
    return [p for p in ctx.snap.all("pods") if (p.get("spec") or {}).get("nodeName") == node
            and (p.get("status") or {}).get("phase") not in ("Succeeded", "Failed")]


@check("node-not-ready", "Node is not ready", Category.NODE, Severity.CRITICAL)
def node_not_ready(ctx: Context) -> Iterator[Finding]:
    """The kubelet stopped reporting or reports itself unhealthy; pods on it are being lost."""
    for node in ctx.snap.all("nodes"):
        ready = _cond(node, "Ready")
        if not ready or ready.get("status") == "True":
            continue
        name = meta(node).get("name", "")
        affected = pods_on(ctx, name)
        yield ctx.finding(
            "node-not-ready", node,
            f"Node has been NotReady for {human_duration(age(ready.get('lastTransitionTime')))} "
            f"({ready.get('reason')}: {ready.get('message', '')[:160]}). {len(affected)} pod(s) run on it.",
            evidence=[f"Ready={ready.get('status')} reason={ready.get('reason')}"] + [
                f"{c.get('type')}={c.get('status')}" for c in (node.get("status") or {}).get("conditions") or []
                if c.get("type") != "Ready" and c.get("status") == "True"],
            remediation="Check the node in your cloud console (instance stopped, disk full, network). On the node: "
                        "`systemctl status kubelet containerd` and `journalctl -u kubelet`. Replace it if it does not recover.",
            commands=[f"kubectl describe node {name}", f"kubectl get pods -A -o wide --field-selector spec.nodeName={name}"],
            since=parse_time(ready.get("lastTransitionTime")),
            node=name,
        )


@check("node-pressure", "Node is under resource pressure", Category.NODE, Severity.HIGH)
def node_pressure(ctx: Context) -> Iterator[Finding]:
    """MemoryPressure / DiskPressure / PIDPressure: the kubelet is evicting pods."""
    for node in ctx.snap.all("nodes"):
        for ctype, advice in (
            ("MemoryPressure", "Pods use more memory than the node has. Set memory requests near real usage."),
            ("DiskPressure", "Node disk is almost full: images, container logs or emptyDir. Prune images, cap log size, "
                             "add ephemeral-storage limits."),
            ("PIDPressure", "A process is forking too many processes/threads."),
        ):
            c = _cond(node, ctype)
            if c.get("status") == "True":
                name = meta(node).get("name", "")
                yield ctx.finding(
                    "node-pressure", node, f"{ctype}: {c.get('message', '')[:200]}",
                    title=f"Node has {ctype}", evidence=[f"since {c.get('lastTransitionTime')}"],
                    remediation=advice, commands=[f"kubectl describe node {name}", "kubectl top pods -A --sort-by=memory | head"],
                    node=name, since=parse_time(c.get("lastTransitionTime")),
                )


@check("node-cordoned", "Node is cordoned", Category.NODE, Severity.LOW, risk=True)
def node_cordoned(ctx: Context) -> Iterator[Finding]:
    """spec.unschedulable is true; often left behind after maintenance."""
    for node in ctx.snap.all("nodes"):
        if (node.get("spec") or {}).get("unschedulable"):
            name = meta(node).get("name", "")
            yield ctx.finding("node-cordoned", node, "New pods cannot be scheduled on this node (cordoned).",
                              remediation="Uncordon it if maintenance is finished.",
                              commands=[f"kubectl uncordon {name}"], node=name)


@check("node-overcommitted", "Node requests are near capacity", Category.RESOURCES, Severity.MEDIUM)
def node_overcommitted(ctx: Context) -> Iterator[Finding]:
    """CPU or memory requests above 95% of allocatable: new or rescheduled pods will not fit."""
    nodes = ctx.snap.all("nodes")
    full = []
    for node in nodes:
        name = meta(node).get("name", "")
        alloc = (node.get("status") or {}).get("allocatable") or {}
        for res in ("cpu", "memory"):
            cap = parse_quantity(alloc.get(res))
            if not cap:
                continue
            req = sum(parse_quantity(((c.get("resources") or {}).get("requests") or {}).get(res))
                      for p in pods_on(ctx, name) for c in containers_of(p.get("spec")))
            if req / cap >= 0.95:
                full.append((node, res, req / cap))
    if nodes and len({id(n) for n, _, _ in full}) == len(nodes):
        node, res, ratio = full[0]
        yield ctx.finding(
            "node-overcommitted", node,
            f"Every node has more than 95% of its {res} requested; any new pod will stay Pending.",
            severity=Severity.HIGH,
            evidence=[f"{meta(n).get('name')}: {r} {ratio:.0%}" for n, r, ratio in full[:8]],
            remediation="Add capacity (or enable the cluster autoscaler), or right-size over-requested workloads (`kubeoracle cost`).",
        )


@check("node-version-skew", "Kubelet version too far behind the control plane", Category.UPGRADE, Severity.MEDIUM, risk=True)
def version_skew(ctx: Context) -> Iterator[Finding]:
    """Kubelets may be at most 3 minor versions older than the API server."""
    server = minor(ctx.snap.server_version)
    if server is None:
        return
    for node in ctx.snap.all("nodes"):
        info = (node.get("status") or {}).get("nodeInfo") or {}
        kubelet = minor(info.get("kubeletVersion"))
        if kubelet is None:
            continue
        if kubelet > server or server - kubelet > 3:
            yield ctx.finding(
                "node-version-skew", node,
                f"Kubelet {info.get('kubeletVersion')} vs API server {ctx.snap.server_version}: outside the supported skew.",
                severity=Severity.HIGH, remediation="Upgrade (or replace) the node before upgrading the control plane further.",
                node=meta(node).get("name"),
            )
        elif server - kubelet == 3:
            yield ctx.finding(
                "node-version-skew", node,
                f"Kubelet {info.get('kubeletVersion')} is 3 minors behind {ctx.snap.server_version}; "
                "the next control-plane upgrade will break support for this node.",
                remediation="Upgrade node pools before the next control-plane upgrade.", node=meta(node).get("name"),
            )


@check("node-containerd-1x", "Node runs containerd 1.x", Category.UPGRADE, Severity.MEDIUM, risk=True)
def containerd_1x(ctx: Context) -> Iterator[Finding]:
    """Kubernetes 1.36 dropped support for containerd 1.x; nodes must move to containerd 2.x."""
    old = [n for n in ctx.snap.all("nodes")
           if re.match(r"containerd://1\.", ((n.get("status") or {}).get("nodeInfo") or {}).get("containerRuntimeVersion", ""))]
    if not old:
        return
    runtime = ((old[0].get("status") or {}).get("nodeInfo") or {}).get("containerRuntimeVersion")
    yield ctx.finding(
        "node-containerd-1x", old[0],
        f"{len(old)} node(s) run {runtime}. Kubernetes 1.36+ requires containerd 2.x; upgrade the node image first.",
        severity=Severity.HIGH if (minor(ctx.snap.server_version) or 0) >= 35 else Severity.MEDIUM,
        evidence=[meta(n).get("name", "") for n in old[:10]],
        remediation="Roll node pools to an OS image with containerd 2.x (check cgroup v2 at the same time).",
    )


@check("gpu-idle", "GPU capacity is idle", Category.COST, Severity.MEDIUM, risk=True)
def gpu_idle(ctx: Context) -> Iterator[Finding]:
    """GPU nodes with no pod requesting their GPUs. GPUs are the most expensive idle resource."""
    gpu_keys = ("nvidia.com/gpu", "amd.com/gpu", "gpu.intel.com/i915")
    idle = []
    total = 0.0
    for node in ctx.snap.all("nodes"):
        alloc = (node.get("status") or {}).get("allocatable") or {}
        gpus = sum(parse_quantity(alloc.get(k)) for k in gpu_keys)
        if not gpus:
            continue
        total += gpus
        name = meta(node).get("name", "")
        used = sum(parse_quantity(((c.get("resources") or {}).get("limits") or {}).get(k))
                   for p in pods_on(ctx, name) for c in containers_of(p.get("spec")) for k in gpu_keys)
        if used < gpus:
            idle.append((name, gpus - used, gpus))
    if not idle:
        return
    free = sum(i[1] for i in idle)
    monthly = free * ctx.settings.cost_gpu_hour * 730
    yield ctx.finding(
        "gpu-idle", ctx.snap.all("nodes")[0] if len(idle) > 1 else next(n for n in ctx.snap.all("nodes") if meta(n).get("name") == idle[0][0]),
        f"{free:.0f} of {total:.0f} GPUs are not requested by any pod (about ${monthly:,.0f}/month at "
        f"${ctx.settings.cost_gpu_hour}/GPU-hour).",
        evidence=[f"{n}: {f:.0f}/{t:.0f} GPUs free" for n, f, t in idle[:10]],
        remediation="Scale GPU node pools to zero when idle (cluster autoscaler / Karpenter), share GPUs with time-slicing or MIG, "
                    "and move notebooks and batch jobs to on-demand GPU pools.",
    )
