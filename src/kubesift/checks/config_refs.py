"""References from pods to ConfigMaps, Secrets and pull secrets that don't exist."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from kubesift.checks.base import Context, check
from kubesift.models import Category, Finding, Severity
from kubesift.util import containers_of, meta


def _refs(pod_spec: dict[str, Any]) -> Iterator[tuple[str, str, str | None, str]]:
    """Yield (kind, name, key, where) for every non-optional reference."""
    for c in containers_of(pod_spec, include_init=True):
        cname = c.get("name", "")
        for ef in c.get("envFrom") or []:
            for kind, field in (("ConfigMap", "configMapRef"), ("Secret", "secretRef")):
                ref = ef.get(field)
                if ref and not ref.get("optional"):
                    yield kind, ref.get("name", ""), None, f"envFrom in container {cname}"
        for env in c.get("env") or []:
            vf = env.get("valueFrom") or {}
            for kind, field in (("ConfigMap", "configMapKeyRef"), ("Secret", "secretKeyRef")):
                ref = vf.get(field)
                if ref and not ref.get("optional"):
                    yield kind, ref.get("name", ""), ref.get("key"), f"env {env.get('name')} in container {cname}"
    for v in pod_spec.get("volumes") or []:
        if v.get("configMap") and not v["configMap"].get("optional"):
            yield "ConfigMap", v["configMap"].get("name", ""), None, f"volume {v.get('name')}"
        if v.get("secret") and not v["secret"].get("optional"):
            yield "Secret", v["secret"].get("secretName", ""), None, f"volume {v.get('name')}"
        for src in (v.get("projected") or {}).get("sources") or []:
            if src.get("configMap") and not src["configMap"].get("optional"):
                yield "ConfigMap", src["configMap"].get("name", ""), None, f"projected volume {v.get('name')}"
            if src.get("secret") and not src["secret"].get("optional"):
                yield "Secret", src["secret"].get("name", ""), None, f"projected volume {v.get('name')}"


@check("config-ref-missing", "Referenced ConfigMap or Secret does not exist", Category.CONFIG, Severity.CRITICAL)
def missing_refs(ctx: Context) -> Iterator[Finding]:
    """A pod references a ConfigMap/Secret (or a key in one) that is missing from its namespace."""
    snap = ctx.snap
    for pod in snap.all("pods"):
        if (pod.get("status") or {}).get("phase") in ("Succeeded", "Failed"):
            continue
        ns = meta(pod).get("namespace")
        problems: list[str] = []
        for kind, name, key, where in _refs(pod.get("spec") or {}):
            store = "configmaps" if kind == "ConfigMap" else "secrets"
            if not snap.can_read(store):
                continue
            obj = snap.get(store, ns, name)
            if obj is None:
                problems.append(f"{kind} '{name}' (used by {where}) does not exist in namespace {ns}")
            elif key and key not in (obj.get("keys") or []):
                problems.append(f"{kind} '{name}' has no key '{key}' (used by {where}); keys: {', '.join(obj.get('keys') or []) or 'none'}")
        if problems:
            yield ctx.finding(
                "config-ref-missing", pod, problems[0] + ".",
                evidence=problems[:6],
                remediation="Create the object/key in the same namespace (names are case-sensitive), "
                            "fix the reference, or mark it optional: true if the app can run without it.",
                commands=[f"kubectl get configmaps,secrets -n {ns}"],
            )


@check("image-pull-secret-missing", "imagePullSecret does not exist", Category.CONFIG, Severity.HIGH)
def missing_pull_secret(ctx: Context) -> Iterator[Finding]:
    """The pod lists an imagePullSecret that is not in its namespace."""
    if not ctx.snap.can_read("secrets"):
        return
    for pod in ctx.snap.all("pods"):
        ns = meta(pod).get("namespace")
        for ref in (pod.get("spec") or {}).get("imagePullSecrets") or []:
            name = ref.get("name", "")
            if ctx.snap.get("secrets", ns, name) is None:
                yield ctx.finding(
                    "image-pull-secret-missing", pod,
                    f"imagePullSecret '{name}' does not exist in namespace {ns}; private images will fail to pull.",
                    remediation="Create the docker-registry Secret in this namespace (pull secrets are not shared across namespaces).",
                    commands=[f"kubectl create secret docker-registry {name} -n {ns} --docker-server=<registry> "
                              "--docker-username=<user> --docker-password=<token>"],
                )
