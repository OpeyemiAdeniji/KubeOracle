"""Right-sizing and waste: compares what workloads request with what they actually use.

Usage comes from Prometheus (7-day p95 CPU and peak memory) when `prometheus_url` is set,
otherwise from metrics-server (a single sample; treat as a hint).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

import httpx

from kubeoracle.cluster.snapshot import Snapshot
from kubeoracle.config import Settings
from kubeoracle.util import age, containers_of, fmt_bytes, fmt_cpu, meta, parse_quantity

HOURS_PER_MONTH = 730
LB_MONTHLY = 18.0
STORAGE_GB_MONTH = 0.08


def _prometheus(settings: Settings) -> dict[str, dict[str, float]] | None:
    if not settings.prometheus_url:
        return None
    headers = {"Authorization": f"Bearer {settings.prometheus_token}"} if settings.prometheus_token else {}
    queries = {
        "cpu": 'quantile_over_time(0.95, sum by (namespace, pod, container) '
               '(rate(container_cpu_usage_seconds_total{container!="",container!="POD"}[5m]))[7d:10m])',
        "memory": 'max_over_time(container_memory_working_set_bytes{container!="",container!="POD"}[7d])',
    }
    out: dict[str, dict[str, float]] = defaultdict(dict)
    with httpx.Client(timeout=60, headers=headers) as http:
        for res, q in queries.items():
            r = http.get(f"{settings.prometheus_url.rstrip('/')}/api/v1/query", params={"query": q})
            r.raise_for_status()
            for row in (r.json().get("data") or {}).get("result") or []:
                m = row.get("metric") or {}
                key = f"{m.get('namespace')}/{m.get('pod')}/{m.get('container')}"
                out[key][res] = max(out[key].get(res, 0.0), float(row["value"][1]))
    return out


def cost_report(snap: Snapshot, settings: Settings) -> dict[str, Any]:
    warnings: list[str] = []
    usage: dict[str, dict[str, float]] | None = None
    source = "prometheus (7d p95 CPU, 7d peak memory)"
    try:
        usage = _prometheus(settings)
    except Exception as exc:
        warnings.append(f"Prometheus query failed ({exc}); falling back to metrics-server.")
    if usage is None:
        usage = snap.metrics.get("pods") or {}
        source = "metrics-server (current usage only; run with --prometheus for 7-day data)"
        if not usage:
            warnings.append("No usage data: install metrics-server or set KUBEORACLE_PROMETHEUS_URL.")

    # aggregate per workload + container
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for pod in snap.all("pods"):
        if (pod.get("status") or {}).get("phase") != "Running":
            continue
        ns, pname = meta(pod).get("namespace"), meta(pod).get("name")
        if ns in settings.system_namespaces:
            continue
        owner = str(snap.owner_of(pod))
        for c in containers_of(pod.get("spec")):
            u = usage.get(f"{ns}/{pname}/{c.get('name')}")
            if not u:
                continue
            req = (c.get("resources") or {}).get("requests") or {}
            row = agg.setdefault((owner, c.get("name", "")), {
                "workload": owner, "container": c.get("name"), "replicas": 0,
                "cpu_request": parse_quantity(req.get("cpu")), "mem_request": parse_quantity(req.get("memory")),
                "cpu_used": 0.0, "mem_used": 0.0})
            row["replicas"] += 1
            row["cpu_used"] = max(row["cpu_used"], u.get("cpu", 0.0))
            row["mem_used"] = max(row["mem_used"], u.get("memory", 0.0))

    items = []
    total_cpu_waste = total_mem_waste = 0.0
    for row in agg.values():
        rec_cpu = max(row["cpu_used"] * 1.2, 0.01)
        rec_mem = max(row["mem_used"] * 1.2, 32 * 2**20)
        cpu_waste = max(row["cpu_request"] - rec_cpu, 0) * row["replicas"]
        mem_waste = max(row["mem_request"] - rec_mem, 0) * row["replicas"]
        monthly = (cpu_waste * settings.cost_cpu_hour + mem_waste / 2**30 * settings.cost_gb_hour) * HOURS_PER_MONTH
        under = row["mem_request"] and row["mem_used"] > row["mem_request"]
        if monthly < 1 and not under:
            continue
        total_cpu_waste += cpu_waste
        total_mem_waste += mem_waste
        items.append({
            **row,
            "cpu_request_h": fmt_cpu(row["cpu_request"]) if row["cpu_request"] else "none",
            "mem_request_h": fmt_bytes(row["mem_request"]) if row["mem_request"] else "none",
            "cpu_used_h": fmt_cpu(row["cpu_used"]), "mem_used_h": fmt_bytes(row["mem_used"]),
            "recommend_cpu": fmt_cpu(rec_cpu), "recommend_memory": fmt_bytes(rec_mem),
            "monthly_savings": round(monthly, 2),
            "note": "uses more memory than requested: risk of eviction" if under else "",
        })
    items.sort(key=lambda x: -x["monthly_savings"])

    waste: list[dict[str, Any]] = []
    used_claims = {(meta(p).get("namespace"), v["persistentVolumeClaim"]["claimName"])
                   for p in snap.all("pods") for v in (p.get("spec") or {}).get("volumes") or []
                   if v.get("persistentVolumeClaim")}
    for pv in snap.all("pvs"):
        if (pv.get("status") or {}).get("phase") == "Released":
            gb = parse_quantity(((pv.get("spec") or {}).get("capacity") or {}).get("storage")) / 2**30
            waste.append({"type": "released-volume", "resource": f"PersistentVolume/{meta(pv).get('name')}",
                          "detail": f"{gb:.0f}Gi released but not deleted (reclaimPolicy Retain)",
                          "monthly_cost": round(gb * STORAGE_GB_MONTH, 2)})
    for pvc in snap.all("pvcs"):
        ns, name = meta(pvc).get("namespace"), meta(pvc).get("name")
        a = age(meta(pvc).get("creationTimestamp"))
        if (ns, name) not in used_claims and (pvc.get("status") or {}).get("phase") == "Bound" and a and a > timedelta(days=7):
            gb = parse_quantity(((pvc.get("status") or {}).get("capacity") or {}).get("storage")) / 2**30
            waste.append({"type": "unused-claim", "resource": f"PersistentVolumeClaim/{ns}/{name}",
                          "detail": f"{gb:.0f}Gi bound but no pod mounts it", "monthly_cost": round(gb * STORAGE_GB_MONTH, 2)})
    for svc in snap.all("services"):
        if (svc.get("spec") or {}).get("type") != "LoadBalancer" or not (svc.get("spec") or {}).get("selector"):
            continue
        ns = meta(svc).get("namespace")
        sel = (svc.get("spec") or {}).get("selector") or {}
        if not any(all((meta(p).get("labels") or {}).get(k) == v for k, v in sel.items())
                   for p in snap.in_namespace("pods", ns)):
            waste.append({"type": "idle-load-balancer", "resource": f"Service/{ns}/{meta(svc).get('name')}",
                          "detail": "LoadBalancer with no backing pods", "monthly_cost": LB_MONTHLY})
    finished = [j for j in snap.all("jobs") if (j.get("status") or {}).get("completionTime")
                and (j.get("spec") or {}).get("ttlSecondsAfterFinished") is None and not meta(j).get("ownerReferences")]
    if len(finished) > 20:
        waste.append({"type": "finished-jobs", "resource": f"{len(finished)} Jobs",
                      "detail": "completed Jobs without ttlSecondsAfterFinished pile up in etcd", "monthly_cost": 0})

    nodes_cpu = sum(parse_quantity(((n.get("status") or {}).get("allocatable") or {}).get("cpu")) for n in snap.all("nodes"))
    nodes_mem = sum(parse_quantity(((n.get("status") or {}).get("allocatable") or {}).get("memory")) for n in snap.all("nodes"))
    return {
        "source": source,
        "prices": {"cpu_hour": settings.cost_cpu_hour, "gb_hour": settings.cost_gb_hour, "gpu_hour": settings.cost_gpu_hour},
        "cluster": {"nodes": len(snap.all("nodes")), "cpu": fmt_cpu(nodes_cpu), "memory": fmt_bytes(nodes_mem),
                    "monthly_compute_estimate": round((nodes_cpu * settings.cost_cpu_hour + nodes_mem / 2**30
                                                       * settings.cost_gb_hour) * HOURS_PER_MONTH, 2)},
        "rightsizing": items[:50],
        "waste": waste,
        "totals": {"overrequested_cpu": fmt_cpu(total_cpu_waste), "overrequested_memory": fmt_bytes(total_mem_waste),
                   "monthly_savings": round(sum(i["monthly_savings"] for i in items) + sum(w["monthly_cost"] for w in waste), 2)},
        "warnings": warnings,
    }
