"""PersistentVolumeClaims, StorageClasses and volume mount failures."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

from kubeoracle.checks.base import Context, check
from kubeoracle.models import Category, Finding, Severity
from kubeoracle.util import age, human_duration, meta


@check("pvc-pending", "Volume claim is not bound", Category.STORAGE, Severity.HIGH)
def pvc_pending(ctx: Context) -> Iterator[Finding]:
    """A PVC is Pending or Lost, so pods using it cannot start."""
    classes = {meta(sc).get("name"): sc for sc in ctx.snap.all("storageclasses")}
    default = [n for n, sc in classes.items()
               if (meta(sc).get("annotations") or {}).get("storageclass.kubernetes.io/is-default-class") == "true"]
    for pvc in ctx.snap.all("pvcs"):
        phase = (pvc.get("status") or {}).get("phase")
        if phase not in ("Pending", "Lost"):
            continue
        a = age(meta(pvc).get("creationTimestamp"))
        if phase == "Pending" and a is not None and a < timedelta(minutes=2):
            continue
        spec = pvc.get("spec") or {}
        sc_name = spec.get("storageClassName")
        events = [e.get("message", "") for e in ctx.snap.events_for("PersistentVolumeClaim", meta(pvc).get("namespace"),
                                                                     meta(pvc).get("name", ""))]
        sc = classes.get(sc_name) if sc_name else None
        if phase == "Lost":
            msg, fix = "The bound PersistentVolume was deleted; data may be gone.", "Restore the PV from backup or recreate the claim."
        elif sc_name and ctx.snap.can_read("storageclasses") and sc is None:
            msg = f"StorageClass '{sc_name}' does not exist (available: {', '.join(sorted(classes)) or 'none'})."
            fix = "Use an existing StorageClass or install the CSI driver that provides it."
        elif not sc_name and ctx.snap.can_read("storageclasses") and not default:
            msg, fix = "No storageClassName and the cluster has no default StorageClass.", "Set storageClassName or mark a default class."
        elif sc and sc.get("volumeBindingMode") == "WaitForFirstConsumer" and any("waiting for first consumer" in e for e in events):
            continue  # normal: binds when a pod uses it
        else:
            msg = f"Pending for {human_duration(a)}." + (f" Provisioner says: {events[0][:200]}" if events else "")
            fix = "Check the CSI driver/provisioner logs, cloud quotas and that the requested size and access mode are supported."
        yield ctx.finding(
            "pvc-pending", pvc, msg, severity=Severity.CRITICAL if phase == "Lost" else Severity.HIGH,
            evidence=[e[:300] for e in events[:3]], remediation=fix,
            commands=[f"kubectl describe pvc {meta(pvc).get('name')} -n {meta(pvc).get('namespace')}"],
        )


@check("volume-mount-failed", "Volume cannot be attached or mounted", Category.STORAGE, Severity.HIGH)
def mount_failed(ctx: Context) -> Iterator[Finding]:
    """FailedMount / FailedAttachVolume events on pods that are not running."""
    for pod in ctx.snap.all("pods"):
        if (pod.get("status") or {}).get("phase") != "Pending":
            continue
        ns, name = meta(pod).get("namespace"), meta(pod).get("name", "")
        events = [e for e in ctx.snap.events_for("Pod", ns, name)
                  if e.get("reason") in ("FailedMount", "FailedAttachVolume")]
        if not events:
            continue
        msg = events[0].get("message", "")
        fix = "Check the volume and its driver."
        if "Multi-Attach" in msg:
            fix = ("A ReadWriteOnce volume is still attached to another node (usually the old pod during a rollout). "
                   "Use the Recreate strategy for single-writer volumes, or wait for the old node to release it.")
        elif "not found" in msg:
            fix = "The Secret/ConfigMap/PVC used as a volume does not exist."
        elif "timed out" in msg:
            fix = "The CSI driver did not finish in time: check the CSI node/controller pods and cloud API errors."
        yield ctx.finding("volume-mount-failed", pod, f"{events[0].get('reason')}: {msg[:250]}",
                          evidence=[e.get("message", "")[:300] for e in events[:3]], remediation=fix,
                          node=(pod.get("spec") or {}).get("nodeName"))
