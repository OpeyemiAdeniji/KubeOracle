"""
Rule-based baseline confidence scorer.

Runs before the LLM call to quantify how much evidence is available.
The score and evidence summary are injected into the prompt so the LLM
can calibrate its own confidence against observable data strength.
"""


_DOMAIN_KEYS = [
    "pods", "logs", "events", "deployments", "network",
    "nodes", "resources", "config_secrets", "probes", "rbac", "scheduling",
]


def calculate_baseline_confidence(investigation: dict) -> tuple[int, list[str]]:
    """
    Return (score 0-90, evidence_summary list).

    Caps at 90 — perfect confidence requires cross-domain LLM correlation.
    """
    score = 0
    evidence: list[str] = []

    available = [k for k in _DOMAIN_KEYS if investigation.get(k, {}).get("success", False)]
    unavailable = [k for k in _DOMAIN_KEYS if not investigation.get(k, {}).get("success", False)]

    if not available:
        return 0, ["No cluster data — all kubectl calls failed (cluster unreachable or no kubeconfig)"]

    # Base: up to 25 points for domain availability
    score += min(len(available) * 3, 25)
    evidence.append(f"{len(available)}/11 investigation domains returned data")
    if unavailable:
        evidence.append(f"Unavailable domains: {', '.join(unavailable)}")

    # Pods — up to 20 points
    pods = investigation.get("pods", {})
    if pods.get("problematic_pods"):
        count = pods["problematic_count"]
        score += min(count * 5, 20)
        evidence.append(f"{count} problematic pod(s) detected")

        max_restarts = max(
            (p.get("restart_count", 0) for p in pods["problematic_pods"]), default=0
        )
        if max_restarts > 5:
            score += 5
            evidence.append(f"High restart count (max {max_restarts}) — persistent failure")

    # Logs — 15 points if error patterns found
    logs = investigation.get("logs", {})
    error_pods = [e for e in logs.get("pod_logs", []) if e.get("relevant_errors")]
    if error_pods:
        score += 15
        evidence.append(f"Error patterns found in logs for {len(error_pods)} pod(s)")

    # Events — up to 10 points
    events = investigation.get("events", {})
    critical = events.get("critical_events", [])
    if critical:
        score += min(len(critical) * 2, 10)
        evidence.append(f"{len(critical)} critical cluster event(s) found")

    # ConfigMap/Secret issues — 10 points (direct cause of crashes)
    config_secrets = investigation.get("config_secrets", {})
    if config_secrets.get("issues"):
        score += 10
        evidence.append(f"{len(config_secrets['issues'])} missing ConfigMap/Secret reference(s)")

    # RBAC issues — 5 points
    rbac = investigation.get("rbac", {})
    if rbac.get("issues"):
        score += 5
        evidence.append(f"{len(rbac['issues'])} RBAC issue(s)")

    # Scheduling failures — 5 points
    scheduling = investigation.get("scheduling", {})
    if scheduling.get("scheduling_failures") or scheduling.get("pending_pods"):
        score += 5
        evidence.append("Scheduling failures or pending pods detected")

    return min(score, 90), evidence
