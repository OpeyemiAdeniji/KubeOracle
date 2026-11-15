"""Controllers: Deployments, StatefulSets, DaemonSets, Jobs, CronJobs and HPAs."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from kubesift.checks.base import Context, check
from kubesift.models import Category, Finding, Severity
from kubesift.util import age, human_duration, meta, parse_time


def _cond(obj: dict[str, Any], ctype: str) -> dict[str, Any]:
    return next((c for c in (obj.get("status") or {}).get("conditions") or [] if c.get("type") == ctype), {})


@check("deployment-unavailable", "Deployment has fewer ready replicas than desired", Category.WORKLOAD, Severity.HIGH)
def deployment_unavailable(ctx: Context) -> Iterator[Finding]:
    """Ready replicas are below spec.replicas for more than 3 minutes."""
    for d in ctx.snap.all("deployments"):
        spec, status = d.get("spec") or {}, d.get("status") or {}
        want = spec.get("replicas", 1)
        ready = status.get("readyReplicas", 0) or 0
        if want == 0 or ready >= want or spec.get("paused"):
            continue
        avail = _cond(d, "Available")
        since = age(avail.get("lastTransitionTime")) if avail.get("status") == "False" else None
        if since is not None and since < timedelta(minutes=3):
            continue
        ns, name = meta(d).get("namespace"), meta(d).get("name")
        sev = Severity.CRITICAL if ready == 0 else Severity.HIGH
        yield ctx.finding(
            "deployment-unavailable", d,
            f"{ready}/{want} replicas ready" + (" — the app is down." if ready == 0 else "."),
            severity=sev,
            evidence=[f"updated={status.get('updatedReplicas', 0)} available={status.get('availableReplicas', 0)} "
                      f"unavailable={status.get('unavailableReplicas', 0)}",
                      *([f"Available=False since {human_duration(since)}: {avail.get('message', '')}"] if since else [])],
            remediation="The pod-level findings for this Deployment show why its pods are not ready.",
            commands=[f"kubectl rollout status deployment/{name} -n {ns}", f"kubectl get pods -n {ns} -l "
                      + ",".join(f"{k}={v}" for k, v in ((spec.get('selector') or {}).get('matchLabels') or {}).items())],
            since=parse_time(avail.get("lastTransitionTime")),
        )


@check("deployment-rollout-stuck", "Rollout is stuck", Category.WORKLOAD, Severity.HIGH)
def rollout_stuck(ctx: Context) -> Iterator[Finding]:
    """The Deployment exceeded progressDeadlineSeconds: the new version never became ready."""
    for d in ctx.snap.all("deployments"):
        prog = _cond(d, "Progressing")
        if prog.get("reason") != "ProgressDeadlineExceeded":
            continue
        ns, name = meta(d).get("namespace"), meta(d).get("name")
        yield ctx.finding(
            "deployment-rollout-stuck", d,
            f"The latest rollout did not finish: {prog.get('message', '')}",
            evidence=[f"Progressing=False reason=ProgressDeadlineExceeded at {prog.get('lastUpdateTime')}"],
            remediation="The new ReplicaSet's pods are failing. Fix them, or roll back to the last working version.",
            commands=[f"kubectl rollout undo deployment/{name} -n {ns}", f"kubectl rollout history deployment/{name} -n {ns}"],
            since=parse_time(prog.get("lastTransitionTime")),
        )


@check("statefulset-unready", "StatefulSet has unready replicas", Category.WORKLOAD, Severity.HIGH)
def statefulset_unready(ctx: Context) -> Iterator[Finding]:
    """Ready replicas below desired; StatefulSets roll one pod at a time, so one bad pod blocks the rest."""
    for s in ctx.snap.all("statefulsets"):
        want = (s.get("spec") or {}).get("replicas", 1)
        status = s.get("status") or {}
        ready = status.get("readyReplicas", 0) or 0
        if want == 0 or ready >= want:
            continue
        rolling = status.get("updateRevision") and status.get("currentRevision") != status.get("updateRevision")
        yield ctx.finding(
            "statefulset-unready", s,
            f"{ready}/{want} replicas ready" + (" during an update; the rollout waits on the unready pod." if rolling else "."),
            severity=Severity.CRITICAL if ready == 0 else Severity.HIGH,
            evidence=[f"currentRevision={status.get('currentRevision')} updateRevision={status.get('updateRevision')}"],
            remediation="Fix the unready pod (often a PVC or a peer it waits for). With OrderedReady, "
                        "a broken pod after a bad update must be deleted after reverting the template.",
            commands=[f"kubectl rollout status statefulset/{meta(s).get('name')} -n {meta(s).get('namespace')}"],
        )


@check("daemonset-unavailable", "DaemonSet pods missing or unready", Category.WORKLOAD, Severity.HIGH)
def daemonset_unavailable(ctx: Context) -> Iterator[Finding]:
    """Nodes that should run the DaemonSet don't have a ready pod (agents, CNI, log shippers)."""
    for ds in ctx.snap.all("daemonsets"):
        st = ds.get("status") or {}
        desired, ready = st.get("desiredNumberScheduled", 0), st.get("numberReady", 0)
        if desired == 0 or ready >= desired:
            continue
        system = ctx.is_system(meta(ds).get("namespace"))
        yield ctx.finding(
            "daemonset-unavailable", ds,
            f"{ready}/{desired} nodes have a ready pod" + (" (system component: node features may be broken)." if system else "."),
            severity=Severity.CRITICAL if system and ready < desired else Severity.HIGH,
            evidence=[f"unavailable={st.get('numberUnavailable', 0)} misscheduled={st.get('numberMisscheduled', 0)}"],
            remediation="Look at the DaemonSet's pods on the affected nodes; taints or node resources are common causes.",
            commands=[f"kubectl get pods -n {meta(ds).get('namespace')} -o wide | grep {meta(ds).get('name')}"],
        )


@check("job-failed", "Job failed", Category.WORKLOAD, Severity.HIGH)
def job_failed(ctx: Context) -> Iterator[Finding]:
    """A Job hit its backoffLimit or activeDeadlineSeconds."""
    for job in ctx.snap.all("jobs"):
        failed = _cond(job, "Failed")
        if failed.get("status") != "True":
            continue
        a = age(failed.get("lastTransitionTime"))
        if a is not None and a > timedelta(days=2):
            continue
        cron = next((r.get("name") for r in meta(job).get("ownerReferences") or [] if r.get("kind") == "CronJob"), None)
        yield ctx.finding(
            "job-failed", job,
            f"Job failed ({failed.get('reason', '')}): {failed.get('message', '')}"
            + (f" It was started by CronJob {cron}." if cron else ""),
            evidence=[f"failed pods: {(job.get('status') or {}).get('failed', 0)}",
                      f"backoffLimit: {(job.get('spec') or {}).get('backoffLimit', 6)}"],
            remediation="Read the logs of the job's failed pods. Set ttlSecondsAfterFinished so finished Jobs are cleaned up.",
            commands=[f"kubectl logs job/{meta(job).get('name')} -n {meta(job).get('namespace')} --tail=100"],
            since=parse_time(failed.get("lastTransitionTime")),
        )


@check("cronjob-failing", "CronJob's recent runs fail", Category.WORKLOAD, Severity.HIGH)
def cronjob_failing(ctx: Context) -> Iterator[Finding]:
    """The last scheduled run failed or there has been no success since the last schedule."""
    jobs_by_cron: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for job in ctx.snap.all("jobs"):
        for r in meta(job).get("ownerReferences") or []:
            if r.get("kind") == "CronJob":
                jobs_by_cron.setdefault((meta(job).get("namespace"), r.get("name")), []).append(job)
    for cj in ctx.snap.all("cronjobs"):
        if (cj.get("spec") or {}).get("suspend"):
            continue
        key = (meta(cj).get("namespace"), meta(cj).get("name"))
        jobs = sorted(jobs_by_cron.get(key, []), key=lambda j: meta(j).get("creationTimestamp", ""))
        if not jobs:
            continue
        latest = jobs[-1]
        if _cond(latest, "Failed").get("status") != "True":
            continue
        status = cj.get("status") or {}
        yield ctx.finding(
            "cronjob-failing", cj,
            f"The latest run ({meta(latest).get('name')}) failed. Last success: {status.get('lastSuccessfulTime') or 'never'}.",
            evidence=[f"lastScheduleTime={status.get('lastScheduleTime')}",
                      f"{sum(1 for j in jobs if _cond(j, 'Failed').get('status') == 'True')}/{len(jobs)} kept jobs failed"],
            remediation="Read the failed job's logs. Missed backups and reports often go unnoticed; alert on this.",
            commands=[f"kubectl logs job/{meta(latest).get('name')} -n {key[0]} --tail=100"],
        )


@check("hpa-maxed-out", "Autoscaler is at its maximum", Category.RESOURCES, Severity.MEDIUM)
def hpa_maxed(ctx: Context) -> Iterator[Finding]:
    """The HPA wants more replicas than maxReplicas allows, so load is not being absorbed."""
    for h in ctx.snap.all("hpas"):
        spec, st = h.get("spec") or {}, h.get("status") or {}
        mx = spec.get("maxReplicas")
        if not mx or st.get("currentReplicas", 0) < mx or st.get("desiredReplicas", 0) < mx:
            continue
        limited = _cond(h, "ScalingLimited")
        if limited.get("status") != "True" or limited.get("reason") != "TooManyReplicas":
            continue
        target = spec.get("scaleTargetRef") or {}
        yield ctx.finding(
            "hpa-maxed-out", h,
            f"Running at maxReplicas={mx} and still above its target metrics; {target.get('kind')}/{target.get('name')} is capped.",
            evidence=[f"current={st.get('currentReplicas')} desired={st.get('desiredReplicas')}",
                      limited.get("message", "")],
            remediation="Raise maxReplicas if the cluster has room, or make each replica handle more load.",
            commands=[f"kubectl describe hpa {meta(h).get('name')} -n {meta(h).get('namespace')}"],
        )


@check("hpa-cannot-scale", "Autoscaler cannot read metrics", Category.RESOURCES, Severity.HIGH)
def hpa_broken(ctx: Context) -> Iterator[Finding]:
    """ScalingActive=False: missing metrics-server, missing resource requests, or a bad custom metric."""
    for h in ctx.snap.all("hpas"):
        active = _cond(h, "ScalingActive")
        able = _cond(h, "AbleToScale")
        bad = active if active.get("status") == "False" else able if able.get("status") == "False" else None
        if not bad:
            continue
        msg = bad.get("message", "")
        hint = "Install metrics-server or fix the metrics adapter."
        if "missing request" in msg:
            hint = "The target's containers have no CPU/memory request, so utilization cannot be computed. Add requests."
        elif "not find the requested resource" in msg or "unable to get metrics" in msg:
            hint = "The metrics API is not available. Install metrics-server (or the custom metrics adapter)."
        yield ctx.finding(
            "hpa-cannot-scale", h,
            f"The autoscaler is not working ({bad.get('reason')}): {msg[:200]}",
            evidence=[msg[:400]],
            remediation=hint + " Until fixed, the workload will not scale under load.",
            commands=[f"kubectl describe hpa {meta(h).get('name')} -n {meta(h).get('namespace')}",
                      "kubectl get apiservice v1beta1.metrics.k8s.io"],
        )
