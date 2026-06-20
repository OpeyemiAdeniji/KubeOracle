import httpx
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
from core.config import settings


def publish_step(session_id: str, step: str, status: str) -> None:
    if not session_id or not settings.insforge_anon_key:
        return
    try:
        with httpx.Client(timeout=3.0) as client:
            client.post(
                f"{settings.insforge_url}/rest/v1/investigation_progress",
                headers={
                    "apikey": settings.insforge_anon_key,
                    "Authorization": f"Bearer {settings.insforge_anon_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                json={"session_id": session_id, "step": step, "status": status},
            )
    except Exception as exc:
        logger.warning(f"Progress publish failed for step={step}: {exc}")


def run_full_investigation(session_id: str = "", context: str = "") -> dict:
    set_context(context)  # propagates to all run_kubectl() calls in this thread
    logger.info("=" * 60)
    logger.info(f"Starting full Kubernetes investigation{f'  context={context}' if context else ''}")
    logger.info("=" * 60)

    logger.info("[1/11] Pods")
    publish_step(session_id, "pods", "active")
    pods = _safe("pods", inspect_pods)
    publish_step(session_id, "pods", "complete" if pods.get("success") else "failed")

    problematic_pods = pods.get("problematic_pods", []) if pods.get("success") else []

    logger.info("[2/11] Logs")
    publish_step(session_id, "logs", "active")
    logs = _safe("logs", lambda: collect_logs(problematic_pods))
    publish_step(session_id, "logs", "complete" if logs.get("success") else "failed")

    logger.info("[3/11] Events")
    publish_step(session_id, "events", "active")
    events = _safe("events", analyze_events)
    publish_step(session_id, "events", "complete" if events.get("success") else "failed")

    logger.info("[4/11] Deployments")
    publish_step(session_id, "deployments", "active")
    deployments = _safe("deployments", inspect_deployments)
    publish_step(session_id, "deployments", "complete" if deployments.get("success") else "failed")

    logger.info("[5/11] Network")
    publish_step(session_id, "network", "active")
    network = _safe("network", inspect_network)
    publish_step(session_id, "network", "complete" if network.get("success") else "failed")

    logger.info("[6/11] Nodes")
    publish_step(session_id, "nodes", "active")
    nodes = _safe("nodes", inspect_nodes)
    publish_step(session_id, "nodes", "complete" if nodes.get("success") else "failed")

    logger.info("[7/11] Resources & Storage")
    publish_step(session_id, "resources", "active")
    resources = _safe("resources", inspect_resources)
    publish_step(session_id, "resources", "complete" if resources.get("success") else "failed")

    logger.info("[8/11] ConfigMaps & Secrets")
    publish_step(session_id, "config_secrets", "active")
    config_secrets = _safe("config_secrets", inspect_config_secrets)
    publish_step(session_id, "config_secrets", "complete" if config_secrets.get("success") else "failed")

    logger.info("[9/11] Probes")
    publish_step(session_id, "probes", "active")
    probes = _safe("probes", inspect_probes)
    publish_step(session_id, "probes", "complete" if probes.get("success") else "failed")

    logger.info("[10/11] RBAC")
    publish_step(session_id, "rbac", "active")
    rbac = _safe("rbac", inspect_rbac)
    publish_step(session_id, "rbac", "complete" if rbac.get("success") else "failed")

    logger.info("[11/11] Scheduling")
    publish_step(session_id, "scheduling", "active")
    scheduling = _safe("scheduling", inspect_scheduling)
    publish_step(session_id, "scheduling", "complete" if scheduling.get("success") else "failed")

    logger.info("Investigation complete.")

    return {
        "pods": pods,
        "logs": logs,
        "events": events,
        "deployments": deployments,
        "network": network,
        "nodes": nodes,
        "resources": resources,
        "config_secrets": config_secrets,
        "probes": probes,
        "rbac": rbac,
        "scheduling": scheduling,
    }


def _safe(name: str, fn) -> dict:
    try:
        return fn()
    except Exception as e:
        logger.error(f"Inspector '{name}' raised an exception: {e}")
        return {"success": False, "error": str(e)}
