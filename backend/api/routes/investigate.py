from typing import Optional

from fastapi import APIRouter

from models import InvestigateRequest
from services.investigation_service import run_full_investigation, publish_step
from ai.root_cause_analyzer import analyze

router = APIRouter()


def _is_healthy(investigation: dict) -> bool:
    """True when the three most important health signals are all clear."""
    return (
        not investigation.get("pods", {}).get("problematic_pods")
        and not investigation.get("events", {}).get("critical_events")
        and not investigation.get("deployments", {}).get("unhealthy_deployments")
    )


def _is_cluster_reachable(investigation: dict) -> bool:
    """True when at least the pods inspector could reach the API server."""
    return investigation.get("pods", {}).get("success", False)


@router.post("/investigate")
def investigate(request: Optional[InvestigateRequest] = None):
    """
    Full Kubernetes investigation + AI root cause analysis.

    Flow:
      1. Run 11 inspectors (pods, logs, events, deployments, network, nodes,
         resources, config/secrets, probes, RBAC, scheduling)
      2. Score evidence strength (confidence engine)
      3. Send structured prompt to LLM (OpenRouter)
      4. Return AI diagnosis with root cause, fix, kubectl commands, confidence

    Accepts optional session_id (realtime progress) and context (cluster selection).
    """
    session_id = (request.session_id if request else "") or ""
    context = (request.context if request else "") or ""

    investigation = run_full_investigation(session_id=session_id, context=context)

    # Short-circuit: if kubectl can't reach the cluster, skip the LLM call
    if not _is_cluster_reachable(investigation):
        publish_step(session_id, "ai_reasoning", "failed")
        ctx_label = f" '{context}'" if context else ""
        ctx_flag = f" --context={context}" if context else ""
        return {
            "status": "success",
            "diagnosis": {
                "root_cause": f"kubectl cannot connect to cluster{ctx_label}",
                "explanation": (
                    "The kubectl client cannot reach the Kubernetes API server. "
                    "The cluster may be stopped, the kubeconfig context may be wrong, "
                    "or the kubeconfig file may not be mounted in Docker."
                ),
                "fix": (
                    "Mount your kubeconfig in docker-compose.yml, verify the cluster is running, "
                    "and confirm kubectl works locally."
                ),
                "kubectl_commands": [
                    f"kubectl cluster-info{ctx_flag}",
                    f"kubectl get nodes{ctx_flag}",
                    f"kubectl config get-contexts",
                ],
                "prevention": "Monitor cluster API server availability with a health check.",
                "contributing_factors": [
                    "kubectl cannot reach API server",
                    "Cluster may not be running",
                    "kubeconfig not mounted in Docker or incorrect path",
                ],
                "severity": "critical",
                "confidence": 90,
            },
            "session_id": session_id,
            "healthy": False,
            "cluster_reachable": False,
        }

    publish_step(session_id, "ai_reasoning", "active")
    diagnosis = analyze(investigation)
    publish_step(session_id, "ai_reasoning", "complete")
    publish_step(session_id, "diagnosis", "complete")

    return {
        "status": "success",
        "diagnosis": diagnosis,
        "session_id": session_id,
        "healthy": _is_healthy(investigation),
        "cluster_reachable": True,
    }
