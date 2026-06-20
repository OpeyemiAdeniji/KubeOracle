from loguru import logger
from kubernetes.kubectl_executor import run_kubectl


def inspect_deployments() -> dict:
    logger.info("Inspecting deployments...")
    dep_result = run_kubectl(["get", "deployments", "-A", "-o", "json"], parse_json=True)
    hpa_result = run_kubectl(["get", "hpa", "-A", "-o", "json"], parse_json=True)

    if not dep_result.success or not dep_result.parsed:
        return {"success": False, "error": dep_result.stderr}

    unhealthy = []
    healthy_count = 0

    for dep in dep_result.parsed.get("items", []):
        ns = dep["metadata"]["namespace"]
        name = dep["metadata"]["name"]
        spec = dep.get("spec", {})
        status = dep.get("status", {})

        desired = spec.get("replicas", 1)
        available = status.get("availableReplicas", 0)
        unavailable = status.get("unavailableReplicas", 0)
        ready = status.get("readyReplicas", 0)

        issues = []

        if unavailable > 0:
            issues.append(f"{unavailable}/{desired} replicas unavailable")

        if available == 0 and desired > 0:
            issues.append("Zero available replicas")

        for cond in status.get("conditions", []):
            ctype = cond.get("type", "")
            if ctype == "Available" and cond.get("status") != "True":
                issues.append(f"Not Available: {cond.get('message', '')}")
            if ctype == "Progressing" and cond.get("reason") == "ProgressDeadlineExceeded":
                issues.append("Progress deadline exceeded — rollout stuck")

        pod_spec = spec.get("template", {}).get("spec", {})
        for container in pod_spec.get("containers", []):
            cname = container.get("name", "")
            image = container.get("image", "")

            if image.endswith(":latest") or (":" not in image.split("/")[-1]):
                issues.append(f"Container '{cname}' uses ':latest' or unpinned tag")

            resources = container.get("resources", {})
            if not resources.get("limits"):
                issues.append(f"Container '{cname}' has no resource limits")
            if not resources.get("requests"):
                issues.append(f"Container '{cname}' has no resource requests")

        if issues:
            unhealthy.append({
                "name": name,
                "namespace": ns,
                "desired_replicas": desired,
                "available_replicas": available,
                "ready_replicas": ready,
                "unavailable_replicas": unavailable,
                "issues": issues,
            })
        else:
            healthy_count += 1

    # HPA issues
    hpa_issues = []
    for hpa in (hpa_result.parsed or {}).get("items", []):
        for cond in hpa.get("status", {}).get("conditions", []):
            if cond.get("status") != "True" and cond.get("type") in ("AbleToScale", "ScalingActive"):
                hpa_issues.append({
                    "hpa": hpa["metadata"]["name"],
                    "namespace": hpa["metadata"]["namespace"],
                    "message": cond.get("message", "HPA scaling issue"),
                })

    return {
        "success": True,
        "total_deployments": healthy_count + len(unhealthy),
        "healthy_count": healthy_count,
        "unhealthy_count": len(unhealthy),
        "unhealthy_deployments": unhealthy,
        "hpa_issues": hpa_issues,
    }
