from loguru import logger
from kubernetes.kubectl_executor import run_kubectl

PRESSURE_CONDITIONS = ("MemoryPressure", "DiskPressure", "PIDPressure")


def inspect_nodes() -> dict:
    logger.info("Inspecting nodes...")
    result = run_kubectl(["get", "nodes", "-o", "json"], parse_json=True)

    if not result.success or not result.parsed:
        return {"success": False, "error": result.stderr}

    node_summaries = []
    unhealthy_nodes = []

    for node in result.parsed.get("items", []):
        meta = node.get("metadata", {})
        spec = node.get("spec", {})
        status = node.get("status", {})

        name = meta.get("name", "unknown")
        conditions = {c["type"]: c for c in status.get("conditions", [])}
        issues = []

        # Readiness
        ready_cond = conditions.get("Ready", {})
        is_ready = ready_cond.get("status") == "True"
        if not is_ready:
            issues.append(f"Node NotReady: {ready_cond.get('message', 'unknown reason')}")

        # Pressure conditions
        for pressure in PRESSURE_CONDITIONS:
            if conditions.get(pressure, {}).get("status") == "True":
                issues.append(f"{pressure} detected")

        # Cordoned
        if spec.get("unschedulable"):
            issues.append("Node is cordoned (manually marked unschedulable)")

        # Taints that block scheduling
        taints = spec.get("taints", [])
        blocking_taints = [
            f"{t.get('key')}:{t.get('effect')}"
            for t in taints
            if t.get("effect") in ("NoSchedule", "NoExecute")
        ]
        if blocking_taints:
            issues.append(f"Blocking taints: {', '.join(blocking_taints)}")

        capacity = status.get("capacity", {})
        allocatable = status.get("allocatable", {})

        summary = {
            "name": name,
            "ready": is_ready,
            "cordoned": bool(spec.get("unschedulable")),
            "capacity": {
                "cpu": capacity.get("cpu", "unknown"),
                "memory": capacity.get("memory", "unknown"),
                "pods": capacity.get("pods", "unknown"),
            },
            "allocatable": {
                "cpu": allocatable.get("cpu", "unknown"),
                "memory": allocatable.get("memory", "unknown"),
                "pods": allocatable.get("pods", "unknown"),
            },
            "taints": [{"key": t.get("key"), "effect": t.get("effect")} for t in taints],
            "issues": issues,
        }

        node_summaries.append(summary)
        if issues:
            unhealthy_nodes.append(summary)

    total = len(node_summaries)
    healthy_count = total - len(unhealthy_nodes)

    return {
        "success": True,
        "total_nodes": total,
        "healthy_nodes": healthy_count,
        "unhealthy_nodes": len(unhealthy_nodes),
        "nodes": node_summaries,
    }
