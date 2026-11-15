"""Upgrade readiness: what will break when moving the cluster to a target Kubernetes version."""

from __future__ import annotations

from typing import Any

from kubeoracle.checks.base import run_checks
from kubeoracle.checks.nodes import minor
from kubeoracle.cluster.snapshot import Snapshot
from kubeoracle.config import Settings
from kubeoracle.models import Category

UPGRADE_CHECKS = ["api-removed", "pdb-blocks-drain", "node-version-skew", "node-containerd-1x", "ingress-nginx-retired",
                  "security-gitrepo-volume", "kube-proxy-ipvs", "service-external-ips"]

NOTES: list[tuple[int, str]] = [
    (35, "cgroup v1: from 1.35 the kubelet refuses to start on cgroup v1 nodes unless failCgroupV1=false. "
         "Check each node image (`stat -fc %T /sys/fs/cgroup` prints cgroup2fs on v2)."),
    (35, "kube-proxy IPVS mode is deprecated from 1.35; plan a move to nftables."),
    (36, "containerd 1.x is no longer supported from 1.36; nodes need containerd 2.x."),
    (36, "gitRepo volumes are disabled from 1.36 with no way to re-enable them."),
    (36, "Service spec.externalIPs is deprecated from 1.36."),
]


def upgrade_report(snap: Snapshot, settings: Settings, target: str) -> dict[str, Any]:
    current = minor(snap.server_version)
    tgt = minor(target)
    if tgt is None:
        raise ValueError(f"invalid target version {target!r}; use e.g. 1.36")
    findings = run_checks(snap, settings, only=UPGRADE_CHECKS, target_version=target)
    findings += [f for f in run_checks(snap, settings, categories=[Category.UPGRADE], target_version=target)
                 if f.check not in UPGRADE_CHECKS]
    notes = [text for v, text in NOTES if (current is None or current < v) and v <= tgt]
    steps = []
    if current is not None and tgt - current > 1:
        steps.append(f"Control planes upgrade one minor version at a time: {' -> '.join(f'1.{v}' for v in range(current, tgt + 1))}.")
    blockers = [f for f in findings if f.severity.rank >= 3]
    return {
        "current": snap.server_version,
        "target": f"1.{tgt}",
        "ready": not blockers,
        "blockers": [f.model_dump(mode="json") for f in blockers],
        "warnings": [f.model_dump(mode="json") for f in findings if f.severity.rank < 3],
        "notes": notes,
        "steps": steps + [
            "Back up etcd (or confirm your managed provider's backups) before the control-plane upgrade.",
            "Upgrade the control plane, then node pools (drain respects PodDisruptionBudgets).",
            "Upgrade add-ons (CNI, CSI drivers, ingress/gateway controllers, cert-manager) to versions that support the target.",
        ],
    }
