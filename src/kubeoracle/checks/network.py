"""Services, endpoints, Ingress, Gateway API, DNS and NetworkPolicies."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from kubeoracle.checks.base import Context, check
from kubeoracle.models import Category, Finding, ResourceRef, Severity
from kubeoracle.util import age, containers_of, human_duration, label_selector_matches, meta, selector_matches


def _pods_for(ctx: Context, ns: str | None, selector: dict[str, str]) -> list[dict[str, Any]]:
    return [p for p in ctx.snap.in_namespace("pods", ns) if selector_matches(selector, meta(p).get("labels"))
            and (p.get("status") or {}).get("phase") not in ("Succeeded", "Failed")]


def _ready(pod: dict[str, Any]) -> bool:
    return any(c.get("type") == "Ready" and c.get("status") == "True"
               for c in (pod.get("status") or {}).get("conditions") or [])


def _ready_endpoints(ctx: Context, svc: dict[str, Any]) -> int | None:
    if not ctx.snap.can_read("endpointslices"):
        return None
    ns, name = meta(svc).get("namespace"), meta(svc).get("name")
    count = 0
    for es in ctx.snap.in_namespace("endpointslices", ns):
        if (meta(es).get("labels") or {}).get("kubernetes.io/service-name") != name:
            continue
        for ep in es.get("endpoints") or []:
            if (ep.get("conditions") or {}).get("ready") is not False:
                count += 1
    return count


@check("service-no-endpoints", "Service has no ready backends", Category.NETWORK, Severity.HIGH)
def service_no_endpoints(ctx: Context) -> Iterator[Finding]:
    """Traffic to the Service fails: its selector matches no ready pods."""
    for svc in ctx.snap.all("services"):
        spec = svc.get("spec") or {}
        selector = spec.get("selector")
        if not selector or spec.get("type") == "ExternalName":
            continue
        if (a := age(meta(svc).get("creationTimestamp"))) is not None and a < timedelta(minutes=2):
            continue
        ns, name = meta(svc).get("namespace"), meta(svc).get("name", "")
        ready_eps = _ready_endpoints(ctx, svc)
        pods = _pods_for(ctx, ns, selector)
        if ready_eps is None:
            ready_eps = sum(1 for p in pods if _ready(p))
        if ready_eps > 0:
            continue
        sel = ",".join(f"{k}={v}" for k, v in selector.items())
        if not pods:
            near = _closest_labels(ctx, ns, selector)
            msg = f"Selector {sel} matches no pods in {ns}, so the Service has no endpoints."
            evidence = [f"selector: {sel}"] + ([f"pods with similar labels: {near}"] if near else [])
            fix = "Make the Service selector equal the pod template labels (a typo or a renamed label is the usual cause)."
            sev = Severity.CRITICAL
        else:
            msg = f"{len(pods)} pod(s) match {sel} but none is ready, so the Service sends no traffic."
            evidence = [f"{meta(p).get('name')}: phase={(p.get('status') or {}).get('phase')}" for p in pods[:5]]
            fix = "Fix the pods' readiness (see the pod findings)."
            sev = Severity.HIGH
        yield ctx.finding(
            "service-no-endpoints", svc, msg, severity=sev, evidence=evidence, remediation=fix,
            commands=[f"kubectl get endpointslices -n {ns} -l kubernetes.io/service-name={name}",
                      f"kubectl get pods -n {ns} --show-labels"],
        )


def _closest_labels(ctx: Context, ns: str | None, selector: dict[str, str]) -> str:
    from difflib import SequenceMatcher

    best, best_score = None, 0.5
    for p in ctx.snap.in_namespace("pods", ns):
        labels = meta(p).get("labels") or {}
        score = sum(SequenceMatcher(None, str(v), str(labels[k])).ratio() for k, v in selector.items() if k in labels)
        score /= max(len(selector), 1)
        if score > best_score:
            best, best_score = labels, score
    if not best:
        return ""
    return ",".join(f"{k}={v}" for k, v in list(best.items())[:6])


@check("service-port-mismatch", "Service targetPort does not match any container port", Category.NETWORK,
       Severity.HIGH, live_only=False)
def service_port_mismatch(ctx: Context) -> Iterator[Finding]:
    """targetPort refers to a named or numbered port the selected pods do not expose."""
    for svc in ctx.snap.all("services"):
        selector = (svc.get("spec") or {}).get("selector")
        if not selector:
            continue
        ns = meta(svc).get("namespace")
        specs = [p.get("spec") or {} for p in _pods_for(ctx, ns, selector)] if ctx.snap.live else []
        if not specs:
            for w in ctx.snap.workloads():
                tmpl = ((w.get("spec") or {}).get("template") or {})
                if meta(w).get("namespace") == ns and selector_matches(selector, (tmpl.get("metadata") or {}).get("labels")):
                    specs.append(tmpl.get("spec") or {})
        if not specs:
            continue
        names, numbers = set(), set()
        for s in specs:
            for c in containers_of(s):
                for p in c.get("ports") or []:
                    if p.get("name"):
                        names.add(p["name"])
                    numbers.add(p.get("containerPort"))
        if not numbers:
            continue  # ports not declared; nothing to compare
        for port in (svc.get("spec") or {}).get("ports") or []:
            target = port.get("targetPort", port.get("port"))
            bad = (isinstance(target, str) and not target.isdigit() and target not in names) or (
                (isinstance(target, int) or str(target).isdigit()) and int(target) not in numbers)
            if bad:
                yield ctx.finding(
                    "service-port-mismatch", svc,
                    f"Port {port.get('port')} forwards to targetPort {target}, but the pods expose "
                    f"{sorted(n for n in numbers if n)}{' / ' + ', '.join(sorted(names)) if names else ''}.",
                    remediation="Set targetPort to the port the container actually listens on.",
                    evidence=[f"service port {port.get('name') or ''} {port.get('port')} -> {target}"],
                )


@check("ingress-backend-missing", "Ingress points to a missing Service or port", Category.NETWORK, Severity.HIGH)
def ingress_backend(ctx: Context) -> Iterator[Finding]:
    """An Ingress rule routes to a Service (or port) that does not exist, which returns 503/404."""
    for ing in ctx.snap.all("ingresses"):
        ns = meta(ing).get("namespace")
        spec = ing.get("spec") or {}
        backends = []
        if spec.get("defaultBackend"):
            backends.append(("default", spec["defaultBackend"]))
        for rule in spec.get("rules") or []:
            for path in (rule.get("http") or {}).get("paths") or []:
                backends.append((f"{rule.get('host', '*')}{path.get('path', '/')}", path.get("backend") or {}))
        problems = []
        for where, b in backends:
            svc_ref = b.get("service")
            if not svc_ref:
                continue
            svc = ctx.snap.get("services", ns, svc_ref.get("name", ""))
            if svc is None:
                problems.append(f"{where} -> Service '{svc_ref.get('name')}' does not exist")
                continue
            port = svc_ref.get("port") or {}
            ports = (svc.get("spec") or {}).get("ports") or []
            if port.get("number") and all(p.get("port") != port["number"] for p in ports):
                problems.append(f"{where} -> Service '{svc_ref.get('name')}' has no port {port['number']}")
            if port.get("name") and all(p.get("name") != port["name"] for p in ports):
                problems.append(f"{where} -> Service '{svc_ref.get('name')}' has no port named {port['name']}")
        for tls in spec.get("tls") or []:
            secret = tls.get("secretName")
            if secret and ctx.snap.can_read("secrets") and ctx.snap.get("secrets", ns, secret) is None:
                problems.append(f"TLS secret '{secret}' for {', '.join(tls.get('hosts') or [])} does not exist")
        if problems:
            yield ctx.finding(
                "ingress-backend-missing", ing, problems[0] + ".", evidence=problems[:6],
                remediation="Point the Ingress at an existing Service and port in the same namespace "
                            "(or create the TLS Secret, e.g. via cert-manager).",
            )


INGRESS_NGINX_IMAGES = ("ingress-nginx/controller", "k8s.gcr.io/ingress-nginx", "registry.k8s.io/ingress-nginx")


@check("ingress-nginx-retired", "ingress-nginx controller is retired", Category.UPGRADE, Severity.HIGH, risk=True)
def ingress_nginx_retired(ctx: Context) -> Iterator[Finding]:
    """The community ingress-nginx project was retired in March 2026: no more fixes or security patches."""
    controllers = []
    for pod in ctx.snap.all("pods"):
        for c in containers_of(pod.get("spec")):
            if any(s in c.get("image", "") for s in INGRESS_NGINX_IMAGES):
                controllers.append((pod, c.get("image")))
                break
    classes = [ic for ic in ctx.snap.all("ingressclasses")
               if (ic.get("spec") or {}).get("controller") == "k8s.io/ingress-nginx"]
    if not controllers and not classes:
        return
    class_names = {meta(ic).get("name") for ic in classes} or {"nginx"}
    using = [i for i in ctx.snap.all("ingresses")
             if (i.get("spec") or {}).get("ingressClassName") in class_names
             or (meta(i).get("annotations") or {}).get("kubernetes.io/ingress.class") in class_names]
    annotated = sum(1 for i in using if any(k.startswith("nginx.ingress.kubernetes.io/")
                                            for k in (meta(i).get("annotations") or {})))
    image = controllers[0][1] if controllers else "unknown"
    ref = ResourceRef(kind="IngressClass", name=sorted(class_names)[0]) if classes else ResourceRef(
        kind="Pod", name=meta(controllers[0][0]).get("name", ""), namespace=meta(controllers[0][0]).get("namespace"))
    yield ctx.finding(
        "ingress-nginx-retired", ref,
        f"The cluster runs the community ingress-nginx controller ({image}), which was retired in March 2026 "
        f"and gets no security fixes. {len(using)} Ingress object(s) use it, {annotated} with nginx-specific annotations "
        "that need translating.",
        evidence=[f"controller image: {image}", f"ingresses using it: {len(using)}",
                  f"with nginx.ingress.kubernetes.io/* annotations: {annotated}"],
        remediation="Plan a migration to Gateway API (or another maintained controller). Convert manifests with "
                    "ingress2gateway, run both paths side by side, then shift traffic gradually.",
        commands=["kubectl get ingress -A -o wide"],
        docs="https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/",
    )


@check("ingress-class-missing", "Ingress uses a class with no controller", Category.NETWORK, Severity.HIGH)
def ingress_class_missing(ctx: Context) -> Iterator[Finding]:
    """ingressClassName names an IngressClass that does not exist, so no controller serves it."""
    if not ctx.snap.can_read("ingressclasses") or not ctx.snap.all("ingressclasses"):
        return
    names = {meta(ic).get("name") for ic in ctx.snap.all("ingressclasses")}
    for ing in ctx.snap.all("ingresses"):
        cls = (ing.get("spec") or {}).get("ingressClassName")
        if cls and cls not in names:
            yield ctx.finding(
                "ingress-class-missing", ing,
                f"ingressClassName '{cls}' does not exist (available: {', '.join(sorted(n for n in names if n))}). "
                "No controller will route this Ingress.",
                remediation="Use one of the installed IngressClasses or install the matching controller.",
            )


@check("loadbalancer-pending", "LoadBalancer has no external address", Category.NETWORK, Severity.HIGH)
def lb_pending(ctx: Context) -> Iterator[Finding]:
    """A type=LoadBalancer Service has waited 5+ minutes without an IP/hostname."""
    for svc in ctx.snap.all("services"):
        spec = svc.get("spec") or {}
        if spec.get("type") != "LoadBalancer":
            continue
        if ((svc.get("status") or {}).get("loadBalancer") or {}).get("ingress"):
            continue
        a = age(meta(svc).get("creationTimestamp"))
        if a is not None and a < timedelta(minutes=5):
            continue
        events = [e.get("message", "") for e in ctx.snap.events_for("Service", meta(svc).get("namespace"),
                                                                     meta(svc).get("name", ""))
                  if e.get("type") == "Warning"]
        yield ctx.finding(
            "loadbalancer-pending", svc,
            f"No external address after {human_duration(a)}." + (f" Cloud controller says: {events[0][:200]}" if events else
                                                                   " No cloud load-balancer controller may be installed (common on kind/bare metal)."),
            evidence=events[:3],
            remediation="Check the cloud controller/load-balancer controller logs, subnet tags and quotas; on bare metal install MetalLB or use an Ingress.",
        )


@check("dns-unhealthy", "Cluster DNS is unhealthy", Category.PLATFORM, Severity.CRITICAL)
def dns_unhealthy(ctx: Context) -> Iterator[Finding]:
    """CoreDNS/kube-dns pods are not ready: every service-name lookup in the cluster may fail."""
    pods = [p for p in ctx.snap.in_namespace("pods", "kube-system")
            if (meta(p).get("labels") or {}).get("k8s-app") == "kube-dns"]
    if not pods:
        return
    ready = [p for p in pods if _ready(p)]
    if len(ready) == len(pods):
        return
    yield ctx.finding(
        "dns-unhealthy", ResourceRef(kind="Deployment", name="coredns", namespace="kube-system"),
        f"{len(ready)}/{len(pods)} DNS pods are ready." + (" DNS is down for the whole cluster." if not ready else
                                                          " Lookups may intermittently fail."),
        severity=Severity.CRITICAL if not ready else Severity.HIGH,
        evidence=[f"{meta(p).get('name')}: {(p.get('status') or {}).get('phase')}" for p in pods],
        commands=["kubectl -n kube-system logs -l k8s-app=kube-dns --tail=50",
                  "kubectl -n kube-system get configmap coredns -o yaml"],
        remediation="Check the CoreDNS logs (loop detection, upstream resolver errors, OOM) and the coredns ConfigMap.",
    )


@check("netpol-blocks-dns", "NetworkPolicy blocks DNS", Category.NETWORK, Severity.HIGH, live_only=False)
def netpol_blocks_dns(ctx: Context) -> Iterator[Finding]:
    """An egress NetworkPolicy selects pods but does not allow port 53, so name lookups time out."""
    for np in ctx.snap.all("networkpolicies"):
        spec = np.get("spec") or {}
        if "Egress" not in (spec.get("policyTypes") or []):
            continue
        egress = spec.get("egress") or []
        allows_dns = False
        for rule in egress:
            ports, peers = rule.get("ports"), rule.get("to")
            if ports and any(str(p.get("port")) in ("53", "dns", "dns-tcp") for p in ports) or not ports and (not peers or any(_peer_maybe_dns(peer) for peer in peers)):
                allows_dns = True
        if allows_dns:
            continue
        ns = meta(np).get("namespace")
        selected = [p for p in ctx.snap.in_namespace("pods", ns)
                    if label_selector_matches(spec.get("podSelector") or {}, meta(p).get("labels"))]
        if ctx.snap.live and not selected:
            continue
        yield ctx.finding(
            "netpol-blocks-dns", np,
            f"This policy restricts egress for {len(selected) if ctx.snap.live else 'its'} pod(s) but allows no traffic to port 53; "
            "DNS lookups from those pods will fail.",
            remediation="Add an egress rule to kube-system pods with label k8s-app=kube-dns on UDP and TCP port 53.",
            evidence=[f"podSelector: {spec.get('podSelector')}"],
        )


def _peer_maybe_dns(peer: dict[str, Any]) -> bool:
    if peer.get("ipBlock"):
        return True
    ns_sel = (peer.get("namespaceSelector") or {}).get("matchLabels") or {}
    return peer.get("namespaceSelector") == {} or ns_sel.get("kubernetes.io/metadata.name") == "kube-system"


@check("gateway-not-accepted", "Gateway or HTTPRoute not accepted", Category.NETWORK, Severity.HIGH)
def gateway_status(ctx: Context) -> Iterator[Finding]:
    """Gateway API objects whose Accepted/Programmed/ResolvedRefs condition is False."""
    for gw in ctx.snap.all("gateways"):
        for c in (gw.get("status") or {}).get("conditions") or []:
            if c.get("type") in ("Accepted", "Programmed") and c.get("status") == "False":
                yield ctx.finding("gateway-not-accepted", gw,
                                  f"Gateway {c.get('type')}=False ({c.get('reason')}): {c.get('message', '')[:200]}")
                break
    for route in ctx.snap.all("httproutes"):
        for parent in (route.get("status") or {}).get("parents") or []:
            bad = next((c for c in parent.get("conditions") or []
                        if c.get("type") in ("Accepted", "ResolvedRefs") and c.get("status") == "False"), None)
            if bad:
                yield ctx.finding("gateway-not-accepted", route,
                                  f"HTTPRoute {bad.get('type')}=False on {(parent.get('parentRef') or {}).get('name')} "
                                  f"({bad.get('reason')}): {bad.get('message', '')[:200]}",
                                  remediation="Check the parentRef name/namespace, allowedRoutes on the Gateway listener, "
                                              "and that backendRefs exist (cross-namespace refs need a ReferenceGrant).")
                break
