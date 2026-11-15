"""Things that break later: removed APIs, expiring certificates, quotas, deprecated features."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

from kubeoracle.checks.base import Context, check
from kubeoracle.checks.nodes import minor
from kubeoracle.cluster.snapshot import APPLIED_API
from kubeoracle.models import Category, Finding, ResourceRef, Severity
from kubeoracle.util import meta, now, parse_quantity, parse_time

# (apiVersion, kind) -> (removed in minor version, replacement)
REMOVED_APIS: dict[tuple[str, str], tuple[int, str]] = {
    ("extensions/v1beta1", "Ingress"): (22, "networking.k8s.io/v1"),
    ("networking.k8s.io/v1beta1", "Ingress"): (22, "networking.k8s.io/v1"),
    ("networking.k8s.io/v1beta1", "IngressClass"): (22, "networking.k8s.io/v1"),
    ("extensions/v1beta1", "Deployment"): (16, "apps/v1"),
    ("apps/v1beta1", "Deployment"): (16, "apps/v1"),
    ("apps/v1beta2", "Deployment"): (16, "apps/v1"),
    ("extensions/v1beta1", "DaemonSet"): (16, "apps/v1"),
    ("apps/v1beta2", "StatefulSet"): (16, "apps/v1"),
    ("apiextensions.k8s.io/v1beta1", "CustomResourceDefinition"): (22, "apiextensions.k8s.io/v1"),
    ("admissionregistration.k8s.io/v1beta1", "ValidatingWebhookConfiguration"): (22, "admissionregistration.k8s.io/v1"),
    ("admissionregistration.k8s.io/v1beta1", "MutatingWebhookConfiguration"): (22, "admissionregistration.k8s.io/v1"),
    ("rbac.authorization.k8s.io/v1beta1", "ClusterRole"): (22, "rbac.authorization.k8s.io/v1"),
    ("rbac.authorization.k8s.io/v1beta1", "ClusterRoleBinding"): (22, "rbac.authorization.k8s.io/v1"),
    ("rbac.authorization.k8s.io/v1beta1", "Role"): (22, "rbac.authorization.k8s.io/v1"),
    ("rbac.authorization.k8s.io/v1beta1", "RoleBinding"): (22, "rbac.authorization.k8s.io/v1"),
    ("scheduling.k8s.io/v1beta1", "PriorityClass"): (22, "scheduling.k8s.io/v1"),
    ("storage.k8s.io/v1beta1", "StorageClass"): (22, "storage.k8s.io/v1"),
    ("storage.k8s.io/v1beta1", "CSIDriver"): (22, "storage.k8s.io/v1"),
    ("storage.k8s.io/v1beta1", "CSIStorageCapacity"): (27, "storage.k8s.io/v1"),
    ("batch/v1beta1", "CronJob"): (25, "batch/v1"),
    ("discovery.k8s.io/v1beta1", "EndpointSlice"): (25, "discovery.k8s.io/v1"),
    ("events.k8s.io/v1beta1", "Event"): (25, "events.k8s.io/v1"),
    ("autoscaling/v2beta1", "HorizontalPodAutoscaler"): (25, "autoscaling/v2"),
    ("autoscaling/v2beta2", "HorizontalPodAutoscaler"): (26, "autoscaling/v2"),
    ("policy/v1beta1", "PodDisruptionBudget"): (25, "policy/v1"),
    ("policy/v1beta1", "PodSecurityPolicy"): (25, "Pod Security Admission"),
    ("node.k8s.io/v1beta1", "RuntimeClass"): (25, "node.k8s.io/v1"),
    ("flowcontrol.apiserver.k8s.io/v1beta1", "FlowSchema"): (26, "flowcontrol.apiserver.k8s.io/v1"),
    ("flowcontrol.apiserver.k8s.io/v1beta2", "FlowSchema"): (29, "flowcontrol.apiserver.k8s.io/v1"),
    ("flowcontrol.apiserver.k8s.io/v1beta3", "FlowSchema"): (32, "flowcontrol.apiserver.k8s.io/v1"),
    ("flowcontrol.apiserver.k8s.io/v1beta1", "PriorityLevelConfiguration"): (26, "flowcontrol.apiserver.k8s.io/v1"),
    ("flowcontrol.apiserver.k8s.io/v1beta2", "PriorityLevelConfiguration"): (29, "flowcontrol.apiserver.k8s.io/v1"),
    ("flowcontrol.apiserver.k8s.io/v1beta3", "PriorityLevelConfiguration"): (32, "flowcontrol.apiserver.k8s.io/v1"),
    ("resource.k8s.io/v1alpha2", "ResourceClaim"): (31, "resource.k8s.io/v1"),
}


def _target_minor(ctx: Context) -> int | None:
    return minor(ctx.target_version) or minor(ctx.snap.server_version)


@check("api-removed", "Manifest uses a removed or soon-removed API", Category.UPGRADE, Severity.HIGH,
       live_only=False, risk=True)
def removed_apis(ctx: Context) -> Iterator[Finding]:
    """apiVersions that the target Kubernetes version no longer serves (manifests, Helm releases, last-applied)."""
    target = _target_minor(ctx)
    uses: list[tuple[str, str, ResourceRef | dict, str]] = []
    for items in ctx.snap.objects.values():
        for obj in items:
            api = obj.get("apiVersion", "") if not ctx.snap.live else (meta(obj).get("annotations") or {}).get(APPLIED_API)
            if api:
                uses.append((api, obj.get("kind", ""), obj, "kubectl apply" if ctx.snap.live else "manifest"))
    for rel in ctx.snap.helm_releases:
        for r in rel.get("resources") or []:
            uses.append((r["apiVersion"], r["kind"],
                         ResourceRef(kind=r["kind"], name=r["name"], namespace=rel.get("namespace")),
                         f"Helm release {rel.get('namespace')}/{rel.get('name')} ({rel.get('chart', '')})"))
    for api, kind, ref, source in uses:
        hit = REMOVED_APIS.get((api, kind))
        if not hit:
            continue
        removed_in, replacement = hit
        if target is not None and target < removed_in - 2:
            continue
        gone = target is None or target >= removed_in
        yield ctx.finding(
            "api-removed", ref,
            f"{kind} uses {api}, {'removed' if gone else 'to be removed'} in Kubernetes 1.{removed_in}. Use {replacement}.",
            severity=Severity.CRITICAL if gone and ctx.snap.live else Severity.HIGH,
            evidence=[f"source: {source}"],
            remediation=(f"Update the manifest/chart to {replacement}. For Helm releases whose stored manifest has the old "
                         "API, upgrade the chart; if upgrades fail, use the helm-mapkubeapis plugin."),
        )


@check("certificate-expiring", "TLS certificate expires soon", Category.SECURITY, Severity.HIGH)
def cert_expiring(ctx: Context) -> Iterator[Finding]:
    """TLS Secrets whose certificate expires within 30 days (or already expired)."""
    for s in ctx.snap.all("secrets"):
        tls = s.get("tls") or {}
        not_after = parse_time(tls.get("not_after"))
        if not not_after:
            continue
        left = not_after - now()
        if left > timedelta(days=30):
            continue
        managed = bool((meta(s).get("annotations") or {}).get("cert-manager.io/certificate-name"))
        days = left.days
        if left.total_seconds() <= 0:
            msg, sev = f"Certificate expired on {not_after:%Y-%m-%d}: HTTPS clients will reject it.", Severity.CRITICAL
        else:
            msg = f"Certificate expires in {days} day(s) ({not_after:%Y-%m-%d})."
            sev = Severity.CRITICAL if days < 7 else Severity.HIGH if days < 14 else Severity.MEDIUM
        if managed:
            msg += " It is managed by cert-manager, so renewal is failing."
        yield ctx.finding(
            "certificate-expiring", s, msg, severity=sev,
            evidence=[f"subject: {tls.get('subject')}", f"dns names: {', '.join(tls.get('dns_names') or [])}"],
            remediation="Check the cert-manager Certificate/Order/Challenge status." if managed
            else "Renew the certificate and update the Secret, or automate it with cert-manager.",
            commands=[f"kubectl describe certificate -n {meta(s).get('namespace')}"] if managed else [],
        )


@check("certificate-not-ready", "cert-manager Certificate not ready", Category.SECURITY, Severity.HIGH)
def cert_manager_not_ready(ctx: Context) -> Iterator[Finding]:
    """cert-manager cannot issue or renew a Certificate (DNS/HTTP challenge, issuer or rate-limit problems)."""
    for c in ctx.snap.all("certificates"):
        ready = next((x for x in (c.get("status") or {}).get("conditions") or [] if x.get("type") == "Ready"), {})
        if ready.get("status") == "False":
            yield ctx.finding("certificate-not-ready", c, f"Not ready ({ready.get('reason')}): {ready.get('message', '')[:250]}",
                              commands=[f"kubectl describe certificate {meta(c).get('name')} -n {meta(c).get('namespace')}",
                                        f"kubectl get certificaterequests,orders,challenges -n {meta(c).get('namespace')}"],
                              remediation="Follow the chain Certificate -> CertificateRequest -> Order -> Challenge to see which step fails.")


@check("quota-near-limit", "ResourceQuota almost used up", Category.RESOURCES, Severity.MEDIUM, risk=True)
def quota_near_limit(ctx: Context) -> Iterator[Finding]:
    """At 90%+ of a quota, the next deploy or scale-up will be rejected."""
    for q in ctx.snap.all("resourcequotas"):
        st = q.get("status") or {}
        hard, used = st.get("hard") or {}, st.get("used") or {}
        full = []
        for res, limit in hard.items():
            h, u = parse_quantity(limit), parse_quantity(used.get(res))
            if h > 0 and u / h >= 0.9:
                full.append(f"{res}: {used.get(res)}/{limit}")
        if full:
            yield ctx.finding("quota-near-limit", q, f"Quota nearly exhausted: {'; '.join(full[:4])}.",
                              severity=Severity.HIGH if any(parse_quantity(used.get(r)) >= parse_quantity(h)
                                                            for r, h in hard.items() if parse_quantity(h) > 0) else Severity.MEDIUM,
                              remediation="Raise the quota or lower requests; rollouts need headroom for surge pods.")


@check("kube-proxy-ipvs", "kube-proxy IPVS mode is deprecated", Category.UPGRADE, Severity.LOW, risk=True)
def kube_proxy_ipvs(ctx: Context) -> Iterator[Finding]:
    """IPVS mode was deprecated in Kubernetes 1.35; nftables is the successor."""
    if (ctx.snap.metrics.get("cluster") or {}).get("kube_proxy", {}).get("mode_ipvs"):
        yield ctx.finding("kube-proxy-ipvs", ResourceRef(kind="ConfigMap", name="kube-proxy", namespace="kube-system"),
                          "kube-proxy runs in IPVS mode, deprecated since Kubernetes 1.35.",
                          remediation="Plan a move to nftables mode (or an eBPF dataplane) and test it in staging.")


@check("service-external-ips", "Service uses externalIPs", Category.SECURITY, Severity.LOW, live_only=False, risk=True)
def external_ips(ctx: Context) -> Iterator[Finding]:
    """spec.externalIPs is deprecated (1.36) and was the vector for CVE-2020-8554."""
    for svc in ctx.snap.all("services"):
        if (svc.get("spec") or {}).get("externalIPs"):
            yield ctx.finding("service-external-ips", svc, "Uses spec.externalIPs, deprecated in Kubernetes 1.36.",
                              remediation="Use a LoadBalancer Service or Gateway instead.")


@check("webhook-unavailable", "Admission webhook has no backend", Category.PLATFORM, Severity.CRITICAL)
def webhook_unavailable(ctx: Context) -> Iterator[Finding]:
    """A webhook with failurePolicy Fail points at a Service with no ready pods: matching API requests are rejected."""
    if not ctx.snap.can_read("endpointslices"):
        return
    for key in ("validatingwebhooks", "mutatingwebhooks"):
        for cfg in ctx.snap.all(key):
            for wh in cfg.get("webhooks") or []:
                svc = (wh.get("clientConfig") or {}).get("service")
                if not svc or wh.get("failurePolicy", "Fail") != "Fail":
                    continue
                ns, name = svc.get("namespace"), svc.get("name", "")
                if ctx.snap.namespace and ns != ctx.snap.namespace and ctx.snap.get("services", ns, name) is None:
                    continue  # service is outside the scanned namespace
                exists = ctx.snap.get("services", ns, name) is not None
                ready = sum(1 for es in ctx.snap.in_namespace("endpointslices", ns)
                            if (meta(es).get("labels") or {}).get("kubernetes.io/service-name") == name
                            for ep in es.get("endpoints") or [] if (ep.get("conditions") or {}).get("ready") is not False)
                if exists and ready:
                    continue
                yield ctx.finding(
                    "webhook-unavailable", cfg,
                    f"Webhook {wh.get('name')} calls Service {ns}/{name}, which "
                    f"{'has no ready endpoints' if exists else 'does not exist'}. With failurePolicy Fail, "
                    "every matching create/update in the cluster is rejected.",
                    evidence=[f"rules: {wh.get('rules')}"[:300], f"namespaceSelector: {wh.get('namespaceSelector')}"[:200]],
                    remediation="Bring the webhook's pods back, or delete the stale webhook configuration if its "
                                "operator was uninstalled. Temporarily setting failurePolicy: Ignore unblocks the API.",
                    commands=[f"kubectl get {cfg.get('kind', '').lower()} {meta(cfg).get('name')} -o yaml",
                              f"kubectl get pods -n {ns}"],
                )
                break
