"""Auto-rollback: undo a rollout that just broke its Deployment, then verify. Off unless you list namespaces.

Narrow on purpose. All of these must hold:
- `allow_writes` is on and the namespace is in `auto_rollback_namespaces` ("*" = every non-system namespace);
- the incident is critical or high and its likely trigger is a rollout of that Deployment in the last 30 minutes;
- the new pods show the version itself is broken (crash loop, image pull, config error, OOM kill, stuck rollout);
- there is an earlier, different version to go back to;
- the Deployment is not managed by Argo CD or Flux (they would re-apply the broken version at the next sync).
It acts at most once per bad rollout (a claim in the shared store), dry-runs first, writes the audit log, verifies,
and the scan loop notifies the usual channels with the outcome.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from kubesift.config import Settings
from kubesift.models import Incident, Report, Severity
from kubesift.util import now

log = logging.getLogger("kubesift.autorollback")

MAX_AGE = timedelta(minutes=30)
BROKEN_VERSION = {"pod-crashloop", "pod-image-pull", "pod-config-error", "pod-oomkilled", "deployment-rollout-stuck"}


@dataclass
class Rollback:
    deployment: str          # ns/name
    trigger: str             # the rollout that caused it
    plan: str
    result: str
    verification: dict[str, Any] | None = None
    incident: Incident | None = None


def gitops_owner(deployment: dict[str, Any]) -> str | None:
    """Argo CD or Flux re-applies its own version, so a rollback by hand would be undone at the next sync."""
    md = deployment.get("metadata") or {}
    keys = set(md.get("labels") or {}) | set(md.get("annotations") or {})
    if keys & {"argocd.argoproj.io/tracking-id", "argocd.argoproj.io/instance"}:
        return "Argo CD"
    if keys & {"kustomize.toolkit.fluxcd.io/name", "helm.toolkit.fluxcd.io/name"}:
        return "Flux"
    return None


def candidates(settings: Settings, report: Report) -> list[Incident]:
    if not settings.allow_writes or not settings.auto_rollback_namespaces:
        return []
    allowed = set(settings.auto_rollback_namespaces)
    out = []
    for inc in report.incidents:
        t = inc.likely_trigger
        if inc.severity.rank < Severity.HIGH.rank or t is None or t.kind != "rollout" or not t.rollback_command:
            continue
        ns = t.resource.namespace or ""
        if t.resource.kind != "Deployment" or not ns:
            continue
        if not (ns in allowed or ("*" in allowed and ns not in settings.system_namespaces)):
            continue
        if now() - t.time > MAX_AGE:
            continue  # an old rollout is not "the change that just broke it"
        if not any(f.check in BROKEN_VERSION for f in inc.findings):
            continue
        out.append(inc)
    return out


def run(settings: Settings, store: Any, report: Report, client: Any) -> list[Rollback]:
    """Roll back each candidate once, then verify. Returns what was done (for notifications)."""
    from kubesift.cluster.client import KubeError
    from kubesift.remediate import execute, plan
    from kubesift.verify import verify

    done: list[Rollback] = []
    for inc in candidates(settings, report):
        t = inc.likely_trigger
        assert t is not None
        ns, name = t.resource.namespace or "", t.resource.name
        key = f"auto-rollback:{report.cluster}:{ns}/{name}:{t.time.isoformat()}"
        if not store.claim(key, 7 * 24 * 3600):
            continue  # already handled this rollout (here or on another replica)
        try:
            owner = gitops_owner(client.get(f"/apis/apps/v1/namespaces/{ns}/deployments/{name}"))
            if owner:
                raise ValueError(f"managed by {owner}, which would re-apply the broken version; roll back in {owner}")
            p = plan(client, "rollback", "deployment", ns, name)
            execute(client, p, True, store, actor="auto-rollback")       # server-side dry run first
            result = execute(client, p, False, store, actor="auto-rollback")
        except (KubeError, ValueError) as exc:
            log.warning("auto-rollback of %s/%s not done: %s", ns, name, exc)
            done.append(Rollback(f"{ns}/{name}", t.summary, "rollback", f"not done: {exc}", None, inc))
            continue
        try:
            v = verify(settings, client, "deployment", ns, name)
            store.audit("verify", v.target, False, f"{v.status}: {v.summary}", "auto-rollback")
            verification: dict[str, Any] | None = v.to_dict()
        except Exception as exc:
            verification = {"status": "unknown", "summary": f"could not verify: {exc}", "ok": False}
        done.append(Rollback(f"{ns}/{name}", t.summary, p.description, result, verification, inc))
    return done


def message(r: Rollback) -> str:
    v = r.verification or {}
    if r.result.startswith("not done"):
        return f"Auto-rollback of {r.deployment} {r.result}"
    outcome = "verified healthy" if v.get("ok") else "still failing after rollback"
    return f"Auto-rolled back {r.deployment}: {outcome}"
