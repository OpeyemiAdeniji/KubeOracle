from loguru import logger
from kubernetes.kubectl_executor import run_kubectl


def inspect_scheduling() -> dict:
    logger.info("Inspecting scheduling constraints...")
    pods_result = run_kubectl(["get", "pods", "-A", "-o", "json"], parse_json=True)
    nodes_result = run_kubectl(["get", "nodes", "-o", "json"], parse_json=True)
    events_result = run_kubectl(["get", "events", "-A", "-o", "json"], parse_json=True)
    pdb_result = run_kubectl(["get", "pdb", "-A", "-o", "json"], parse_json=True)

    if not pods_result.success or not pods_result.parsed:
        return {"success": False, "error": pods_result.stderr}

    # Collect FailedScheduling events
    scheduling_failures = []
    for event in (events_result.parsed or {}).get("items", []):
        if event.get("reason") == "FailedScheduling":
            obj = event.get("involvedObject", {})
            scheduling_failures.append({
                "pod": obj.get("name", "unknown"),
                "namespace": obj.get("namespace", "unknown"),
                "message": event.get("message", ""),
                "count": event.get("count", 1),
            })

    # Collect all node taints for cross-referencing
    node_taints: list[dict] = []
    for node in (nodes_result.parsed or {}).get("items", []):
        for taint in node.get("spec", {}).get("taints", []):
            if taint.get("effect") in ("NoSchedule", "NoExecute"):
                node_taints.append({
                    "node": node["metadata"]["name"],
                    "key": taint.get("key", ""),
                    "effect": taint.get("effect", ""),
                })

    # Analyse pending pods
    pending_pod_analysis = []
    for pod in pods_result.parsed.get("items", []):
        if pod.get("status", {}).get("phase") != "Pending":
            continue

        ns = pod["metadata"]["namespace"]
        pod_name = pod["metadata"]["name"]
        spec = pod.get("spec", {})
        issues = []

        # Node affinity
        affinity = spec.get("affinity", {})
        if affinity.get("nodeAffinity"):
            issues.append("Node affinity configured — verify matching nodes exist")
        if affinity.get("podAntiAffinity"):
            issues.append("Pod anti-affinity configured — may be blocking co-scheduling")

        # Taint tolerance check
        pod_toleration_keys = {t.get("key") for t in spec.get("tolerations", [])}
        pod_toleration_keys.add("")  # empty key = tolerate all

        for taint in node_taints:
            if taint["key"] not in pod_toleration_keys:
                issues.append(
                    f"Node taint '{taint['key']}:{taint['effect']}' "
                    f"on node '{taint['node']}' is not tolerated by this pod"
                )

        # Node selector
        node_selector = spec.get("nodeSelector", {})
        if node_selector:
            issues.append(f"nodeSelector {node_selector} — verify nodes have matching labels")

        pending_pod_analysis.append({
            "pod": pod_name,
            "namespace": ns,
            "issues": issues or ["Pending — cause unknown, check events for FailedScheduling"],
        })

    # PodDisruptionBudget blocks
    pdb_issues = []
    for pdb in (pdb_result.parsed or {}).get("items", []):
        pdb_status = pdb.get("status", {})
        if pdb_status.get("disruptionsAllowed", 1) == 0:
            pdb_issues.append({
                "name": pdb["metadata"]["name"],
                "namespace": pdb["metadata"]["namespace"],
                "message": "PodDisruptionBudget allows 0 disruptions — may block evictions and node drains",
            })

    return {
        "success": True,
        "scheduling_failures": scheduling_failures,
        "pending_pods": pending_pod_analysis,
        "pdb_issues": pdb_issues,
        "total_issues": len(scheduling_failures) + len(pending_pod_analysis),
    }
