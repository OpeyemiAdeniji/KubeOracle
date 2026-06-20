from loguru import logger
from kubernetes.kubectl_executor import run_kubectl

CRITICAL_REASONS = {
    "FailedScheduling",
    "BackOff",
    "FailedMount",
    "FailedPull",
    "ErrImagePull",
    "Unhealthy",
    "NodeNotReady",
    "Evicted",
    "FailedCreate",
    "NetworkNotReady",
    "OOMKilling",
    "SystemOOM",
    "FailedAttachVolume",
    "FailedDetachVolume",
    "FailedBinding",
}


def analyze_events() -> dict:
    logger.info("Analyzing cluster events...")
    result = run_kubectl(["get", "events", "-A", "-o", "json"], parse_json=True)

    if not result.success or not result.parsed:
        return {"success": False, "error": result.stderr}

    critical_events = []
    all_warnings = []
    reason_counts: dict[str, int] = {}

    for event in result.parsed.get("items", []):
        if event.get("type", "Normal") != "Warning":
            continue

        reason = event.get("reason", "Unknown")
        message = event.get("message", "")
        obj = event.get("involvedObject", {})
        ns = obj.get("namespace") or event.get("metadata", {}).get("namespace", "")
        count = event.get("count", 1)

        reason_counts[reason] = reason_counts.get(reason, 0) + count

        entry = {
            "reason": reason,
            "namespace": ns,
            "object": f"{obj.get('kind', '')}/{obj.get('name', '')}",
            "message": message[:300],
            "count": count,
        }

        all_warnings.append(entry)

        if reason in CRITICAL_REASONS:
            critical_events.append(entry)

    return {
        "success": True,
        "total_warning_events": len(all_warnings),
        "critical_events": critical_events,
        "reason_summary": reason_counts,
        "unique_warning_reasons": list(reason_counts.keys()),
    }
