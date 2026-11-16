"""Tell apart the many things "Forbidden" / "access denied" can mean, from the message itself.

Only one of them is Kubernetes RBAC. Calling every 403 an RBAC problem sends people to fix the wrong thing, so each
cause is named only when the message proves it, and anything unrecognised is reported as "a service refused" without
guessing why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_Q = r'\\?"'  # a quote, possibly escaped (the API's JSON error body as printed by curl or a client library)

_RBAC = re.compile(
    r"(?P<what>[\w.-]+) is forbidden: User " + _Q + r"system:serviceaccount:(?P<sans>[\w-]+):(?P<sa>[\w.-]+)" + _Q
    + r" cannot (?P<verb>[\w-]+) resource " + _Q + r"(?P<resource>[\w.-]+)" + _Q
    + r"(?: in API group " + _Q + r"(?P<group>[\w.-]*)" + _Q + r")?"
    + r"(?: in the namespace " + _Q + r"(?P<ns>[\w-]+)" + _Q + r")?",
    re.I,
)
_POD_SECURITY = re.compile(r"violates PodSecurity " + _Q + r"?(?P<level>[\w:.-]+)", re.I)
_QUOTA = re.compile(r"exceeded quota: (?P<quota>[\w.-]+)", re.I)
_LIMIT_RANGE = re.compile(r"(?:maximum|minimum) (?:cpu|memory|ephemeral-storage) usage per (?:Container|Pod) is", re.I)
_WEBHOOK_DENIED = re.compile(r"admission webhook " + _Q + r"(?P<hook>[\w.-]+)" + _Q + r" denied the request", re.I)
_NS_TERMINATING = re.compile(r"because it is being terminated", re.I)
# (detects the cloud, names the missing permission when the message has it)
_AWS = (re.compile(r"\b(?:AccessDenied(?:Exception)?|UnauthorizedOperation)\b|is not authorized to perform: "),
        re.compile(r"is not authorized to perform: ([\w:*-]+)"))
_GCP = (re.compile(r"\bPERMISSION_DENIED\b|does not have [\w.]+ access|Permission '[\w.]+' denied"),
        re.compile(r"does not have ([\w.]+) access|Permission '([\w.]+)' denied"))
_AZURE = (re.compile(r"\bAuthorizationFailed\b|does not have authorization to perform action '"),
          re.compile(r"authorization to perform action '([^']+)'"))
_HTTP_403 = re.compile(r"\b403 Forbidden\b|status(?: code)?[\s:=\"]*403\b|HTTP/\d(?:\.\d)? 403\b|\"code\":\s*403\b|"
                       r"response code[\s:=]*403\b", re.I)
_URL = re.compile(r"https?://[\w.-]+(?::\d+)?")


@dataclass
class Denial:
    kind: str  # rbac | pod-security | quota | limit-range | policy | namespace-terminating | cloud-iam | http-403
    explanation: str
    remediation: str
    line: str = ""
    commands: list[str] = field(default_factory=list)


def _line(text: str, start: int) -> str:
    begin = text.rfind("\n", 0, start) + 1
    end = text.find("\n", start)
    return text[begin:end if end != -1 else None].strip()[:300]


def classify_denial(text: str) -> Denial | None:
    """The first refusal in `text` (log lines or an event message), or None when nothing was refused."""
    if m := _RBAC.search(text):
        sa, sans, verb, resource = m["sa"], m["sans"], m["verb"], m["resource"]
        res = f"{resource}.{m['group']}" if m["group"] else resource
        scope = f"-n {m['ns']}" if m["ns"] else "(cluster-wide)"
        can_i = f"kubectl auth can-i {verb} {res} --as=system:serviceaccount:{sans}:{sa}" + (f" -n {m['ns']}" if m["ns"] else "")
        bind = ("a Role and RoleBinding in that namespace" if m["ns"]
                else "a ClusterRole and ClusterRoleBinding (the resource is cluster-scoped or listed across namespaces)")
        return Denial(
            "rbac",
            f"Kubernetes RBAC: ServiceAccount {sans}/{sa} is not allowed to {verb} {res} {scope}.",
            f"Grant exactly that permission with {bind} for ServiceAccount {sa}, or make the app stop needing it.",
            _line(text, m.start()), [can_i],
        )
    if m := _POD_SECURITY.search(text):
        return Denial("pod-security", f"Pod Security Admission ({m['level']}) rejects the pods it creates; this is not RBAC.",
                      "Set the securityContext fields named in the message (runAsNonRoot, drop ALL capabilities, "
                      "seccompProfile) or change the namespace's pod-security label.", _line(text, m.start()))
    if m := _QUOTA.search(text):
        return Denial("quota", f"ResourceQuota {m['quota']} is full, so new objects are refused; this is not RBAC.",
                      "Raise the quota or lower requests/replicas in that namespace.", _line(text, m.start()),
                      ["kubectl describe resourcequota -A"])
    if m := _LIMIT_RANGE.search(text):
        return Denial("limit-range", "A LimitRange in the namespace refuses the requested CPU/memory; this is not RBAC.",
                      "Set requests and limits inside the LimitRange's bounds, or change the LimitRange.",
                      _line(text, m.start()), ["kubectl describe limitrange -A"])
    if m := _WEBHOOK_DENIED.search(text):
        return Denial("policy", f"Admission policy webhook {m['hook']} denied the request (a policy such as Kyverno or "
                                "Gatekeeper, not RBAC).",
                      "Read the reason after 'denied the request' and change the manifest to meet the policy, or ask "
                      "the policy owner for an exception.", _line(text, m.start()))
    if m := _NS_TERMINATING.search(text):
        return Denial("namespace-terminating", "The namespace is being deleted, so nothing new can be created in it.",
                      "Wait for the deletion to finish (see namespace-stuck-terminating) or deploy to another namespace.",
                      _line(text, m.start()))
    for (pattern, detail), cloud, identity in (
        (_AWS, "AWS IAM", "the pod's IAM role (EKS IRSA or Pod Identity)"),
        (_GCP, "Google Cloud IAM", "the Google service account the pod uses (Workload Identity)"),
        (_AZURE, "Azure RBAC", "the managed identity the pod uses (Azure Workload Identity)"),
    ):
        if m := pattern.search(text):
            d = detail.search(text)
            what = next((g for g in d.groups() if g), "") if d else ""
            return Denial("cloud-iam", f"{cloud} refused the request{f' ({what})' if what else ''}: a cloud permission, "
                                       "not Kubernetes RBAC.",
                          f"Grant the missing permission to {identity}, and check the ServiceAccount is annotated "
                          "with that identity.", _line(text, m.start()))
    if m := _HTTP_403.search(text):
        line = _line(text, m.start())
        host = _URL.search(line)
        where = f" ({host.group(0)})" if host else ""
        return Denial("http-403", f"A service refused the app with 403 Forbidden{where}. The cause is that service's "
                                  "own access rules, not Kubernetes RBAC.",
                      "Check the credentials or token the app uses for that service and what they are allowed to do.",
                      line)
    return None
