from loguru import logger
from kubernetes.kubectl_executor import run_kubectl

CRASH_STATES = {
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "Error",
    "OOMKilled",
    "InvalidImageName",
    "CreateContainerError",
    "CreateContainerConfigError",
    "RunContainerError",
}


def inspect_pods() -> dict:
    logger.info("Inspecting pods...")
    result = run_kubectl(["get", "pods", "-A", "-o", "json"], parse_json=True)

    if not result.success or not result.parsed:
        return {
            "success": False,
            "error": result.stderr,
            "healthy": False,
            "total_pods": 0,
            "problematic_pods": [],
        }

    items = result.parsed.get("items", [])
    problematic_pods = []

    for pod in items:
        meta = pod.get("metadata", {})
        spec = pod.get("spec", {})
        status = pod.get("status", {})

        name = meta.get("name", "unknown")
        namespace = meta.get("namespace", "default")
        phase = status.get("phase", "Unknown")
        deletion_ts = meta.get("deletionTimestamp")

        issues = []
        pod_status = phase
        max_restarts = 0
        init_containers_ready = True

        # Container states
        for cs in status.get("containerStatuses", []):
            restarts = cs.get("restartCount", 0)
            max_restarts = max(max_restarts, restarts)

            waiting_reason = cs.get("state", {}).get("waiting", {}).get("reason", "")
            terminated_reason = cs.get("state", {}).get("terminated", {}).get("reason", "")
            reason = waiting_reason or terminated_reason

            if reason in CRASH_STATES:
                pod_status = reason
                issues.append(f"Container in state: {reason}")

            if terminated_reason == "OOMKilled":
                issues.append("OOMKilled — container exceeded memory limit")

        if max_restarts > 5:
            issues.append(f"High restart count: {max_restarts}")

        # Init containers
        for ics in status.get("initContainerStatuses", []):
            if not ics.get("ready", False):
                init_containers_ready = False
                reason = (
                    ics.get("state", {}).get("waiting", {}).get("reason")
                    or ics.get("state", {}).get("terminated", {}).get("reason")
                    or "Unknown"
                )
                issues.append(f"Init container not ready: {reason}")

        # Stuck terminating
        if deletion_ts:
            pod_status = "Terminating"
            issues.append("Pod stuck in Terminating state")

        # Pending
        if phase == "Pending" and not deletion_ts:
            unschedulable = any(
                c.get("reason") == "Unschedulable"
                for c in status.get("conditions", [])
            )
            if unschedulable:
                issues.append("Unschedulable — check node resources or taints")
            else:
                issues.append("Pod is Pending")

        # Missing resource limits/requests
        for container in spec.get("containers", []):
            cname = container.get("name", "")
            resources = container.get("resources", {})
            if not resources.get("limits"):
                issues.append(f"Container '{cname}' has no resource limits")
            if not resources.get("requests"):
                issues.append(f"Container '{cname}' has no resource requests")

        if issues:
            problematic_pods.append({
                "name": name,
                "namespace": namespace,
                "status": pod_status,
                "phase": phase,
                "restart_count": max_restarts,
                "init_containers_ready": init_containers_ready,
                "issues": issues,
            })

    healthy_count = len(items) - len(problematic_pods)

    return {
        "success": True,
        "healthy": len(problematic_pods) == 0,
        "total_pods": len(items),
        "healthy_count": healthy_count,
        "problematic_count": len(problematic_pods),
        "problematic_pods": problematic_pods,
    }
