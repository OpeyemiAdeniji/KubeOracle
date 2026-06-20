from loguru import logger
from kubernetes.kubectl_executor import run_kubectl


def inspect_network() -> dict:
    logger.info("Inspecting network and services...")
    svc_result = run_kubectl(["get", "svc", "-A", "-o", "json"], parse_json=True)
    ep_result = run_kubectl(["get", "endpoints", "-A", "-o", "json"], parse_json=True)
    ingress_result = run_kubectl(["get", "ingress", "-A", "-o", "json"], parse_json=True)

    if not svc_result.success or not svc_result.parsed:
        return {"success": False, "error": svc_result.stderr}

    # Build endpoints map: (namespace, name) -> has ready addresses
    endpoints_map: dict[tuple, bool] = {}
    for ep in (ep_result.parsed or {}).get("items", []):
        ns = ep["metadata"]["namespace"]
        name = ep["metadata"]["name"]
        subsets = ep.get("subsets", [])
        has_ready = any(s.get("addresses") for s in subsets)
        endpoints_map[(ns, name)] = has_ready

    service_issues = []

    for svc in svc_result.parsed.get("items", []):
        ns = svc["metadata"]["namespace"]
        name = svc["metadata"]["name"]
        spec = svc.get("spec", {})
        selector = spec.get("selector", {})

        # Skip kubernetes internal service
        if name == "kubernetes":
            continue

        # Services with selectors but no ready endpoints
        if selector:
            has_ready = endpoints_map.get((ns, name), False)
            if not has_ready:
                service_issues.append({
                    "type": "no_ready_endpoints",
                    "service": name,
                    "namespace": ns,
                    "selector": selector,
                    "message": (
                        f"Service '{name}' has selector {selector} but no ready endpoints "
                        "— pods may not match the selector or are not ready"
                    ),
                })

        # Port/targetPort mismatch (log as info)
        for port in spec.get("ports", []):
            port_num = port.get("port")
            target_port = port.get("targetPort")
            protocol = port.get("protocol", "TCP")
            if target_port and port_num and str(target_port) != str(port_num):
                service_issues.append({
                    "type": "port_targetport_mismatch",
                    "service": name,
                    "namespace": ns,
                    "message": (
                        f"Service '{name}' port {port_num} → targetPort {target_port} ({protocol}) "
                        "— verify the container listens on the target port"
                    ),
                })

    # Ingress issues
    ingress_issues = []
    for ingress in (ingress_result.parsed or {}).get("items", []):
        ns = ingress["metadata"]["namespace"]
        name = ingress["metadata"]["name"]
        lb_ingress = ingress.get("status", {}).get("loadBalancer", {}).get("ingress", [])
        if not lb_ingress:
            ingress_issues.append({
                "ingress": name,
                "namespace": ns,
                "message": (
                    f"Ingress '{name}' has no load balancer IP/hostname assigned "
                    "— ingress controller may not be running"
                ),
            })

    return {
        "success": True,
        "total_services": len(svc_result.parsed.get("items", [])),
        "total_ingresses": len((ingress_result.parsed or {}).get("items", [])),
        "service_issues": service_issues,
        "ingress_issues": ingress_issues,
        "total_issues": len(service_issues) + len(ingress_issues),
    }
