from loguru import logger
from kubernetes.kubectl_executor import run_kubectl


def inspect_probes() -> dict:
    logger.info("Inspecting liveness, readiness, and startup probes...")
    result = run_kubectl(["get", "pods", "-A", "-o", "json"], parse_json=True)

    if not result.success or not result.parsed:
        return {"success": False, "error": result.stderr}

    pods_with_issues = []

    for pod in result.parsed.get("items", []):
        ns = pod["metadata"]["namespace"]
        pod_name = pod["metadata"]["name"]
        containers = pod.get("spec", {}).get("containers", [])

        container_results = []
        any_issue = False

        for container in containers:
            cname = container.get("name", "")
            issues = []
            configured = []

            liveness = container.get("livenessProbe")
            readiness = container.get("readinessProbe")
            startup = container.get("startupProbe")

            if not liveness:
                issues.append("No liveness probe — container won't restart automatically if it hangs")
            else:
                configured.append("liveness")
                delay = liveness.get("initialDelaySeconds", 0)
                timeout = liveness.get("timeoutSeconds", 1)
                if delay < 5:
                    issues.append(
                        f"Liveness initialDelaySeconds={delay}s is low "
                        "— pod may be killed before finishing startup"
                    )
                if timeout < 2:
                    issues.append(
                        f"Liveness timeoutSeconds={timeout}s is very aggressive"
                    )

            if not readiness:
                issues.append("No readiness probe — pod will receive traffic before it is ready")
            else:
                configured.append("readiness")
                delay = readiness.get("initialDelaySeconds", 0)
                if delay < 3:
                    issues.append(
                        f"Readiness initialDelaySeconds={delay}s is low"
                    )

            if startup:
                configured.append("startup")

            if issues:
                any_issue = True

            container_results.append({
                "container": cname,
                "probes_configured": configured,
                "issues": issues,
            })

        if any_issue:
            pods_with_issues.append({
                "pod": pod_name,
                "namespace": ns,
                "containers": container_results,
            })

    return {
        "success": True,
        "total_pods_checked": len(result.parsed.get("items", [])),
        "pods_with_probe_issues": len(pods_with_issues),
        "pods": pods_with_issues,
    }
