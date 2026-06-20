"""
Targeted investigation service — runs only the inspectors relevant to the
classified intent, making focused queries much faster than a full scan.
"""

from loguru import logger

from kubernetes.kubectl_executor import set_context
from kubernetes.pod_inspector import inspect_pods
from kubernetes.logs_collector import collect_logs
from kubernetes.events_analyzer import analyze_events
from kubernetes.deployment_inspector import inspect_deployments
from kubernetes.network_inspector import inspect_network
from kubernetes.node_inspector import inspect_nodes
from kubernetes.resource_inspector import inspect_resources
from kubernetes.config_secret_inspector import inspect_config_secrets
from kubernetes.probe_inspector import inspect_probes
from kubernetes.rbac_inspector import inspect_rbac
from kubernetes.scheduling_inspector import inspect_scheduling

# Domain → inspector callable (pods/logs handled separately due to dependency)
_DOMAIN_INSPECTORS: dict = {
    "events": analyze_events,
    "deployments": inspect_deployments,
    "network": inspect_network,
    "nodes": inspect_nodes,
    "resources": inspect_resources,
    "config_secrets": inspect_config_secrets,
    "probes": inspect_probes,
    "rbac": inspect_rbac,
    "scheduling": inspect_scheduling,
}


def run_targeted_investigation(
    relevant_domains: list[str],
    context: str = "",
    target_resource: str | None = None,
    target_namespace: str = "all",
) -> dict:
    """
    Run only the inspectors needed for the given domains.
    Returns an investigation dict in the same shape as run_full_investigation().
    """
    set_context(context)
    logger.info(
        f"Targeted investigation — domains={relevant_domains}  "
        f"resource={target_resource}  namespace={target_namespace}"
    )

    result: dict = {}
    pods_data: dict | None = None

    needs_pods = "pods" in relevant_domains or "logs" in relevant_domains

    if needs_pods:
        logger.info("Running pods inspector")
        pods_data = _safe("pods", inspect_pods)
        result["pods"] = pods_data

    if "logs" in relevant_domains:
        logger.info("Running logs collector")
        problematic: list = []
        if pods_data and pods_data.get("success"):
            problematic = pods_data.get("problematic_pods", [])
            # Narrow to target resource when one is specified
            if target_resource:
                problematic = [
                    p for p in problematic
                    if target_resource.lower() in p.get("name", "").lower()
                ]
        result["logs"] = _safe("logs", lambda: collect_logs(problematic))

    for domain in relevant_domains:
        if domain in ("pods", "logs"):
            continue
        fn = _DOMAIN_INSPECTORS.get(domain)
        if fn is None:
            continue
        logger.info(f"Running {domain} inspector")
        result[domain] = _safe(domain, fn)

    logger.info(f"Targeted investigation complete — ran {len(result)} domain(s)")
    return result


def _safe(name: str, fn) -> dict:
    try:
        return fn()
    except Exception as exc:
        logger.error(f"Inspector '{name}' raised an exception: {exc}")
        return {"success": False, "error": str(exc)}
