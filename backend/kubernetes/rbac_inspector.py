from loguru import logger
from kubernetes.kubectl_executor import run_kubectl


def inspect_rbac() -> dict:
    logger.info("Inspecting RBAC and service accounts...")
    sa_result = run_kubectl(["get", "serviceaccounts", "-A", "-o", "json"], parse_json=True)
    rb_result = run_kubectl(["get", "rolebindings", "-A", "-o", "json"], parse_json=True)
    crb_result = run_kubectl(["get", "clusterrolebindings", "-o", "json"], parse_json=True)
    pods_result = run_kubectl(["get", "pods", "-A", "-o", "json"], parse_json=True)

    if not pods_result.success or not pods_result.parsed:
        return {"success": False, "error": pods_result.stderr}

    # Existing service accounts: (namespace, name)
    existing_sas: set[tuple] = set()
    for sa in (sa_result.parsed or {}).get("items", []):
        ns = sa["metadata"]["namespace"]
        name = sa["metadata"]["name"]
        existing_sas.add((ns, name))

    # Service accounts that have at least one binding
    bound_sas: set[tuple] = set()
    for rb in (rb_result.parsed or {}).get("items", []):
        for subject in rb.get("subjects", []):
            if subject.get("kind") == "ServiceAccount":
                bound_sas.add((subject.get("namespace", ""), subject.get("name", "")))
    for crb in (crb_result.parsed or {}).get("items", []):
        for subject in crb.get("subjects", []):
            if subject.get("kind") == "ServiceAccount":
                bound_sas.add((subject.get("namespace", ""), subject.get("name", "")))

    issues = []

    for pod in pods_result.parsed.get("items", []):
        ns = pod["metadata"]["namespace"]
        pod_name = pod["metadata"]["name"]
        sa_name = pod.get("spec", {}).get("serviceAccountName", "default")

        # Skip default SA — always exists, bindings are optional
        if sa_name == "default":
            continue

        if (ns, sa_name) not in existing_sas:
            issues.append({
                "type": "missing_service_account",
                "pod": pod_name,
                "namespace": ns,
                "service_account": sa_name,
                "message": (
                    f"Pod '{pod_name}' references ServiceAccount '{sa_name}' "
                    f"which does not exist in namespace '{ns}'"
                ),
            })
        elif (ns, sa_name) not in bound_sas:
            issues.append({
                "type": "unbound_service_account",
                "pod": pod_name,
                "namespace": ns,
                "service_account": sa_name,
                "message": (
                    f"ServiceAccount '{sa_name}' in namespace '{ns}' has no RoleBinding "
                    "or ClusterRoleBinding — API access may be denied"
                ),
            })

    return {
        "success": True,
        "total_service_accounts": len(existing_sas),
        "total_role_bindings": (
            len((rb_result.parsed or {}).get("items", []))
            + len((crb_result.parsed or {}).get("items", []))
        ),
        "issues_found": len(issues) > 0,
        "issues": issues,
    }
