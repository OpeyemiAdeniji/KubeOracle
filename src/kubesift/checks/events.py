"""Warning events that point at platform problems no other check sees."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import timedelta

from kubesift.checks.base import Context, check
from kubesift.cluster.snapshot import event_time
from kubesift.models import Category, Finding, ResourceRef, Severity
from kubesift.util import age, parse_time

PATTERNS: list[tuple[str, re.Pattern[str], str, Severity, str]] = [
    ("webhook", re.compile(r"failed calling webhook \"?([\w.-]+)", re.I),
     "Admission webhook {0} is failing, so matching creates/updates are rejected (deploys and scaling can stall).",
     Severity.CRITICAL,
     "Check the webhook's service and pods. If the webhook is down and has failurePolicy: Fail, it blocks the API; "
     "fix it or temporarily set failurePolicy: Ignore."),
    ("pod-security", re.compile(r"violates PodSecurity \"?([\w:.-]+)", re.I),
     "Pod Security Admission ({0}) rejects pods: the controller cannot create them.",
     Severity.HIGH,
     "Set the securityContext fields named in the message (runAsNonRoot, drop ALL capabilities, seccompProfile) "
     "or change the namespace's pod-security label."),
    ("quota", re.compile(r"exceeded quota: ([\w.-]+)", re.I),
     "ResourceQuota {0} is exhausted, so new pods are rejected.",
     Severity.HIGH, "Raise the quota or lower requests/replicas in that namespace."),
    ("ip-exhaustion", re.compile(r"failed to assign an IP address|no available IP|IP address.*exhaust|InsufficientFreeAddressesInSubnet|failed to allocate for range", re.I),
     "The CNI cannot give pods an IP address: the subnet or IP pool is exhausted.",
     Severity.CRITICAL,
     "Add subnet capacity (secondary CIDR, prefix delegation on EKS) or reduce pods per node."),
    ("sandbox", re.compile(r"Failed to create pod sandbox|failed to setup network for sandbox", re.I),
     "Pod sandboxes cannot be created: the CNI or container runtime on the node is failing.",
     Severity.HIGH, "Check the CNI DaemonSet pods (aws-node, calico, cilium...) on the affected nodes."),
    ("cron-missed", re.compile(r"too many missed start times|Cannot determine if job needs to be started", re.I),
     "A CronJob missed so many schedules that the controller stopped starting it.",
     Severity.HIGH, "Set startingDeadlineSeconds (e.g. 200) on the CronJob so missed runs are skipped instead of blocking it."),
    ("certificate", re.compile(r"x509: certificate has expired|certificate has expired or is not yet valid", re.I),
     "An expired certificate is breaking a connection.",
     Severity.CRITICAL, "Find which certificate expired (webhook CA bundle, kubelet serving cert, ingress TLS) and rotate it."),
]


@check("platform-warning", "Platform problem reported in events", Category.PLATFORM, Severity.HIGH)
def platform_warnings(ctx: Context) -> Iterator[Finding]:
    """Matches recent Warning events against known platform failure patterns."""
    hits: dict[tuple[str, str], list[dict]] = {}
    for e in ctx.snap.all("events"):
        if e.get("type") != "Warning":
            continue
        a = age(event_time(e))
        if a is not None and a > timedelta(hours=6):
            continue
        msg = e.get("message") or e.get("note") or ""
        for key, pattern, *_ in PATTERNS:
            m = pattern.search(msg)
            if m:
                hits.setdefault((key, m.group(1) if m.groups() and m.group(1) else ""), []).append(e)
                break
    for (key, arg), events in hits.items():
        _, _, template, sev, fix = next(p for p in PATTERNS if p[0] == key)
        events.sort(key=event_time, reverse=True)
        inv = events[0].get("involvedObject") or events[0].get("regarding") or {}
        ref = ResourceRef(kind=inv.get("kind", "Event"), name=inv.get("name", "?"), namespace=inv.get("namespace"))
        count = sum(e.get("count", 1) or 1 for e in events)
        yield ctx.finding(
            "platform-warning", ref,
            template.format(arg or "?") if "{0}" in template else template,
            severity=sev,
            title={"webhook": "Admission webhook is failing", "pod-security": "Pod Security Admission rejects pods",
                   "quota": "ResourceQuota exhausted", "ip-exhaustion": "Pod IP addresses exhausted",
                   "sandbox": "Pod sandbox creation failing", "certificate": "Expired certificate",
                   "cron-missed": "CronJob stopped scheduling"}[key],
            evidence=[f"{count} event(s), latest: {events[0].get('message', '')[:300]}"],
            remediation=fix,
            since=parse_time(event_time(events[-1])),
        )
