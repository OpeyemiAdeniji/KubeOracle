from loguru import logger
from kubernetes.kubectl_executor import run_kubectl


def inspect_resources() -> dict:
    logger.info("Inspecting resource quotas and storage...")
    rq_result = run_kubectl(["get", "resourcequota", "-A", "-o", "json"], parse_json=True)
    pvc_result = run_kubectl(["get", "pvc", "-A", "-o", "json"], parse_json=True)
    lr_result = run_kubectl(["get", "limitrange", "-A", "-o", "json"], parse_json=True)
    sc_result = run_kubectl(["get", "storageclass", "-o", "json"], parse_json=True)

    quota_issues = []
    pvc_issues = []

    # Resource quotas — flag when used == limit (quota exhausted)
    for rq in (rq_result.parsed or {}).get("items", []):
        ns = rq["metadata"]["namespace"]
        name = rq["metadata"]["name"]
        rq_status = rq.get("status", {})
        hard = rq_status.get("hard", {})
        used = rq_status.get("used", {})

        for resource, limit in hard.items():
            usage = used.get(resource, "0")
            if usage == limit:
                quota_issues.append({
                    "namespace": ns,
                    "quota": name,
                    "resource": resource,
                    "used": usage,
                    "limit": limit,
                    "message": f"Resource quota '{resource}' exhausted in namespace '{ns}'",
                })

    # PVC issues
    for pvc in (pvc_result.parsed or {}).get("items", []):
        ns = pvc["metadata"]["namespace"]
        pvc_name = pvc["metadata"]["name"]
        phase = pvc.get("status", {}).get("phase", "Unknown")
        sc_name = pvc.get("spec", {}).get("storageClassName", "default")

        if phase == "Pending":
            pvc_issues.append({
                "pvc": pvc_name,
                "namespace": ns,
                "phase": phase,
                "storage_class": sc_name,
                "message": (
                    f"PVC '{pvc_name}' stuck in Pending — "
                    f"StorageClass '{sc_name}' may not exist or have no dynamic provisioner"
                ),
            })
        elif phase == "Lost":
            pvc_issues.append({
                "pvc": pvc_name,
                "namespace": ns,
                "phase": phase,
                "message": f"PVC '{pvc_name}' is Lost — underlying PV may have been deleted",
            })

    # LimitRange summary
    limit_ranges = [
        {
            "name": lr["metadata"]["name"],
            "namespace": lr["metadata"]["namespace"],
            "limits": lr.get("spec", {}).get("limits", []),
        }
        for lr in (lr_result.parsed or {}).get("items", [])
    ]

    # Available storage classes
    storage_classes = [
        sc["metadata"]["name"]
        for sc in (sc_result.parsed or {}).get("items", [])
    ]

    total_pvcs = len((pvc_result.parsed or {}).get("items", []))

    return {
        "success": True,
        "total_pvcs": total_pvcs,
        "available_storage_classes": storage_classes,
        "quota_issues": quota_issues,
        "pvc_issues": pvc_issues,
        "limit_ranges": limit_ranges,
        "total_issues": len(quota_issues) + len(pvc_issues),
    }
