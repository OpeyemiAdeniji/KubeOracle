"""
Prompt builder for the Kubernetes SRE AI agent.

Converts the full 11-domain investigation payload into a structured,
token-efficient prompt that drives cross-domain root cause correlation.
"""

SYSTEM_PROMPT = """You are a Senior Kubernetes SRE with 10+ years of production experience.

Your role: analyze automated cluster investigation data and identify the TRUE root cause — not list symptoms.

Diagnostic rules:
- CrashLoopBackOff is a SYMPTOM. Find the cause: missing Secret, bad image, probe mis-config, OOM, RBAC, etc.
- Cross-correlate across ALL domains. Pod crash + missing Secret + log error = one root cause, not three.
- Always name the exact pod, namespace, Secret, ConfigMap, or Deployment from the data.
- kubectl commands must be copy-pasteable. Use real resource names and namespaces from the evidence.
- If cluster data is unavailable (kubectl errors), explain what to investigate and provide a general remediation plan.
- Order kubectl commands: fix the root cause first, then secondary issues.

You MUST respond with ONLY valid JSON. No markdown, no code blocks, no text outside the JSON object."""


def build_prompt(
    investigation: dict,
    baseline_confidence: int,
    evidence_summary: list[str],
) -> str:
    lines: list[str] = []

    lines.append("=== BASELINE CONFIDENCE ASSESSMENT ===")
    lines.append(f"Score: {baseline_confidence}/100 (rule-based, before AI correlation)")
    for item in evidence_summary:
        lines.append(f"  • {item}")
    lines.append("")

    # ── Pods ──────────────────────────────────────────────────────────────────
    pods = investigation.get("pods", {})
    lines.append("=== POD STATUS ===")
    if pods.get("success"):
        lines.append(
            f"Total: {pods.get('total_pods', 0)}  |  "
            f"Healthy: {pods.get('healthy_count', 0)}  |  "
            f"Problematic: {pods.get('problematic_count', 0)}"
        )
        for pod in pods.get("problematic_pods", [])[:15]:
            lines.append(
                f"  {pod['namespace']}/{pod['name']}  "
                f"status={pod['status']}  restarts={pod['restart_count']}"
            )
            for issue in pod.get("issues", [])[:5]:
                lines.append(f"    • {issue}")
    else:
        lines.append(f"  UNAVAILABLE — {pods.get('error', 'kubectl error')[:120]}")
    lines.append("")

    # ── Logs ──────────────────────────────────────────────────────────────────
    logs = investigation.get("logs", {})
    lines.append("=== LOG ERRORS ===")
    if logs.get("success"):
        pod_logs = logs.get("pod_logs", [])
        if pod_logs:
            for entry in pod_logs[:10]:
                lines.append(f"  {entry['namespace']}/{entry['pod']}  (status: {entry['status']})")
                errors = entry.get("relevant_errors", [])
                shown = errors[:8] if errors else entry.get("raw_tail", [])[:5]
                for line in shown:
                    lines.append(f"    LOG: {line.strip()[:200]}")
                if entry.get("note"):
                    lines.append(f"    NOTE: {entry['note']}")
        else:
            lines.append("  No log errors (no problematic pods or logs empty)")
    else:
        lines.append("  UNAVAILABLE")
    lines.append("")

    # ── Events ────────────────────────────────────────────────────────────────
    events = investigation.get("events", {})
    lines.append("=== CLUSTER EVENTS ===")
    if events.get("success"):
        critical = events.get("critical_events", [])
        if critical:
            for ev in critical[:15]:
                lines.append(
                    f"  [{ev['reason']}] {ev['namespace']}/{ev['object']}  "
                    f"count={ev['count']}  msg: {ev['message'][:150]}"
                )
        else:
            lines.append("  No critical warning events")
        reason_summary = events.get("reason_summary", {})
        if reason_summary:
            lines.append(f"  All warning reasons: {reason_summary}")
    else:
        lines.append("  UNAVAILABLE")
    lines.append("")

    # ── Deployments ───────────────────────────────────────────────────────────
    deployments = investigation.get("deployments", {})
    lines.append("=== DEPLOYMENT HEALTH ===")
    if deployments.get("success"):
        unhealthy = deployments.get("unhealthy_deployments", [])
        if unhealthy:
            for dep in unhealthy[:10]:
                lines.append(
                    f"  {dep['namespace']}/{dep['name']}  "
                    f"available={dep['available_replicas']}/{dep['desired_replicas']}"
                )
                for issue in dep.get("issues", [])[:5]:
                    lines.append(f"    • {issue}")
        else:
            lines.append("  All deployments healthy")
        for hpa in deployments.get("hpa_issues", [])[:5]:
            lines.append(f"  HPA: {hpa['namespace']}/{hpa['hpa']} — {hpa['message'][:120]}")
    else:
        lines.append("  UNAVAILABLE")
    lines.append("")

    # ── Network ───────────────────────────────────────────────────────────────
    network = investigation.get("network", {})
    lines.append("=== NETWORKING ===")
    if network.get("success"):
        svc_issues = network.get("service_issues", [])
        ing_issues = network.get("ingress_issues", [])
        if svc_issues or ing_issues:
            for issue in (svc_issues + ing_issues)[:10]:
                lines.append(f"  [{issue.get('type', 'issue')}] {issue.get('message', '')[:160]}")
        else:
            lines.append("  No networking issues detected")
    else:
        lines.append("  UNAVAILABLE")
    lines.append("")

    # ── Nodes ─────────────────────────────────────────────────────────────────
    nodes = investigation.get("nodes", {})
    lines.append("=== NODE HEALTH ===")
    if nodes.get("success"):
        lines.append(
            f"  Total: {nodes.get('total_nodes', 0)}  |  "
            f"Healthy: {nodes.get('healthy_nodes', 0)}  |  "
            f"Unhealthy: {nodes.get('unhealthy_nodes', 0)}"
        )
        for node in nodes.get("nodes", []):
            if node.get("issues"):
                lines.append(f"  Node {node['name']}:")
                for issue in node["issues"][:4]:
                    lines.append(f"    • {issue}")
    else:
        lines.append("  UNAVAILABLE")
    lines.append("")

    # ── Resources & Storage ───────────────────────────────────────────────────
    resources = investigation.get("resources", {})
    lines.append("=== RESOURCES & STORAGE ===")
    if resources.get("success"):
        quota_issues = resources.get("quota_issues", [])
        pvc_issues = resources.get("pvc_issues", [])
        if quota_issues or pvc_issues:
            for issue in quota_issues[:5]:
                lines.append(f"  [quota-exhausted] {issue['namespace']}/{issue['quota']} {issue['resource']} {issue['used']}/{issue['limit']}")
            for issue in pvc_issues[:5]:
                lines.append(f"  [pvc] {issue['message'][:160]}")
        else:
            lines.append("  No resource or storage issues")
        sc = resources.get("available_storage_classes", [])
        if sc:
            lines.append(f"  Storage classes: {', '.join(sc)}")
    else:
        lines.append("  UNAVAILABLE")
    lines.append("")

    # ── ConfigMaps & Secrets ──────────────────────────────────────────────────
    config_secrets = investigation.get("config_secrets", {})
    lines.append("=== CONFIGMAP & SECRET REFERENCES ===")
    if config_secrets.get("success"):
        issues = config_secrets.get("issues", [])
        if issues:
            for issue in issues[:15]:
                lines.append(f"  [{issue['type']}] {issue['namespace']}/{issue['pod']}: {issue['message']}")
        else:
            lines.append("  No missing ConfigMap or Secret references")
    else:
        lines.append("  UNAVAILABLE")
    lines.append("")

    # ── Probes ────────────────────────────────────────────────────────────────
    probes = investigation.get("probes", {})
    lines.append("=== PROBE HEALTH ===")
    if probes.get("success"):
        probe_pods = probes.get("pods", [])
        if probe_pods:
            for pod_info in probe_pods[:10]:
                for container in pod_info.get("containers", []):
                    if container.get("issues"):
                        lines.append(
                            f"  {pod_info['namespace']}/{pod_info['pod']} "
                            f"container={container['container']}"
                        )
                        for issue in container["issues"][:3]:
                            lines.append(f"    • {issue}")
        else:
            lines.append("  All probes correctly configured")
    else:
        lines.append("  UNAVAILABLE")
    lines.append("")

    # ── RBAC ──────────────────────────────────────────────────────────────────
    rbac = investigation.get("rbac", {})
    lines.append("=== RBAC ===")
    if rbac.get("success"):
        rbac_issues = rbac.get("issues", [])
        if rbac_issues:
            for issue in rbac_issues[:10]:
                lines.append(f"  [{issue['type']}] {issue['message']}")
        else:
            lines.append("  No RBAC issues")
    else:
        lines.append("  UNAVAILABLE")
    lines.append("")

    # ── Scheduling ────────────────────────────────────────────────────────────
    scheduling = investigation.get("scheduling", {})
    lines.append("=== SCHEDULING ===")
    if scheduling.get("success"):
        failures = scheduling.get("scheduling_failures", [])
        pending = scheduling.get("pending_pods", [])
        pdbs = scheduling.get("pdb_issues", [])
        if failures or pending or pdbs:
            for f in failures[:5]:
                lines.append(f"  [FailedScheduling] {f['namespace']}/{f['pod']} count={f['count']}: {f['message'][:150]}")
            for p in pending[:5]:
                lines.append(f"  [Pending] {p['namespace']}/{p['pod']}")
                for issue in p.get("issues", [])[:3]:
                    lines.append(f"    • {issue}")
            for pdb in pdbs[:5]:
                lines.append(f"  [PDB] {pdb['namespace']}/{pdb['name']}: {pdb['message'][:120]}")
        else:
            lines.append("  No scheduling issues")
    else:
        lines.append("  UNAVAILABLE")
    lines.append("")

    evidence_block = "\n".join(lines)

    return f"""Analyze this Kubernetes cluster investigation. Correlate findings across all domains to identify the root cause.

{evidence_block}
=== REQUIRED OUTPUT ===

Respond with exactly this JSON structure (no other text):

{{
  "root_cause": "One precise sentence identifying the specific root cause (name the exact resource)",
  "explanation": "2-4 sentences: what is failing, why it is failing, which domains confirm it",
  "fix": "Short summary of the resolution steps",
  "kubectl_commands": ["kubectl command 1", "kubectl command 2"],
  "prevention": "1-2 sentences on preventing this class of issue",
  "contributing_factors": ["factor 1", "factor 2", "factor 3"],
  "severity": "critical",
  "confidence": 85
}}

Severity values: critical (service down / data loss risk) | high (major degradation) | medium (partial impact) | low (risk / warning only)
Confidence guide: 85-100 = multiple domains confirm same root cause | 65-84 = strong single-domain evidence | 40-64 = partial data | <40 = insufficient data"""
