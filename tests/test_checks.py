import pytest

from kubesift.checks import run_checks


def by_check(findings):
    out = {}
    for f in findings:
        out.setdefault(f.check, []).append(f)
    return out


@pytest.fixture
def found(snap, settings):
    return by_check(run_checks(snap, settings))


@pytest.mark.parametrize("check,resource", [
    ("pod-crashloop", "checkout-7c9a-x1"),
    ("pod-oomkilled", "api-55-a"),
    ("pod-image-pull", "worker"),
    ("service-no-endpoints", "web"),
    ("pod-pending", "batch-big"),
    ("pod-config-error", "reports"),
    ("config-ref-missing", "reports"),
    ("node-not-ready", "node-2"),
    ("certificate-expiring", "shop-tls"),
    ("ingress-backend-missing", "shop"),
    ("ingress-nginx-retired", "nginx"),
    ("pdb-blocks-drain", "checkout"),
    ("netpol-blocks-dns", "lockdown"),
    ("webhook-unavailable", "policy-webhook"),
    ("platform-warning", "checkout-7c9a"),
    ("deployment-unavailable", "checkout"),
    ("api-removed", "legacy"),
    ("node-containerd-1x", "node-3"),
    ("node-version-skew", "node-3"),
])
def test_detects(found, check, resource):
    assert check in found, f"{check} did not fire; got {sorted(found)}"
    names = {f.resource.name for f in found[check]}
    assert resource in names, f"{check} fired on {names}, expected {resource}"


def test_crashloop_explains_missing_env(found):
    f = found["pod-crashloop"][0]
    assert "DATABASE_URL" in f.message
    assert any("exit code 1" in e for e in f.evidence)
    assert f.owner and f.owner.kind == "Deployment" and f.owner.name == "checkout"


def test_oom_is_critical_when_crashlooping(found):
    f = found["pod-oomkilled"][0]
    assert f.severity.value == "critical"
    assert "64Mi" in f.message
    assert any("set resources" in c for c in f.commands)


def test_rate_limit_hint(found):
    assert "rate limit" in found["pod-image-pull"][0].message.lower()


def test_selector_typo_suggests_labels(found):
    f = found["service-no-endpoints"][0]
    assert "matches no pods" in f.message
    assert any("app=web" in e for e in f.evidence)


def test_scheduler_reason(found):
    assert "Not enough free cpu" in found["pod-pending"][0].message


def test_certificate_days(found):
    f = found["certificate-expiring"][0]
    assert f.severity.value == "critical"
    assert "day" in f.message


def test_crashloop_not_duplicated_for_oom(found):
    assert all(f.resource.name != "api-55-a" for f in found["pod-crashloop"])


def test_namespace_filter(snap, settings):
    snap.namespace = "ingress-nginx"
    findings = run_checks(snap, settings)
    assert all(f.resource.namespace in ("ingress-nginx", None) for f in findings)


def test_broken_check_does_not_break_scan(snap, settings, monkeypatch):
    from kubesift.checks.base import REGISTRY

    def boom(ctx):
        raise RuntimeError("bad check")
        yield  # pragma: no cover

    monkeypatch.setattr(REGISTRY["pod-evicted"], "fn", boom)
    findings = run_checks(snap, settings)
    assert findings
    assert any("pod-evicted" in w for w in snap.warnings)


def test_snapshot_roundtrip(snap, settings):
    from kubesift.cluster.snapshot import Snapshot

    again = Snapshot.from_dict(snap.to_dict())
    assert {f.fingerprint for f in run_checks(again, settings)} == {f.fingerprint for f in run_checks(snap, settings)}


def test_secret_values_never_kept(snap):
    import json
    text = json.dumps(snap.to_dict(), default=str)
    assert "c2VjcmV0" not in text  # tls.key value
    assert "BEGIN CERTIFICATE" not in text
    assert all("data" not in s for s in snap.all("secrets"))


def test_gke_managed_and_scaled_to_zero_services_are_quiet(snap, settings):
    """A fresh GKE cluster raised three false alarms from Google Managed Prometheus (gmp-system)."""
    from fixtures import container, m

    addon = {"addonmanager.kubernetes.io/mode": "Reconcile"}
    gke = {"components.gke.io/layer": "addon"}
    ns = "gmp-system"
    snap.objects["services"] += [
        # a workload kept at 0 replicas: no endpoints is expected (user-owned, so only the replica rule applies)
        {"kind": "Service", "metadata": m("alertmanager", ns=ns),
         "spec": {"selector": {"app": "alertmanager"}, "ports": [{"port": 9093}]}},
        # provider add-ons the user cannot edit: no workload at all, and a leftover named port
        {"kind": "Service", "metadata": m("rule-evaluator", ns=ns, labels=addon, annotations=gke),
         "spec": {"selector": {"app": "rule-evaluator"}, "ports": [{"port": 19092}]}},
        {"kind": "Service", "metadata": m("gmp-operator", ns=ns, labels=addon),
         "spec": {"selector": {"app": "gmp-operator"}, "ports": [{"name": "legacy", "port": 8443, "targetPort": "webhook"}]}},
    ]
    snap.objects["statefulsets"] = snap.objects.get("statefulsets", []) + [
        {"kind": "StatefulSet", "metadata": m("alertmanager", ns=ns), "spec": {"replicas": 0, "template": {
            "metadata": {"labels": {"app": "alertmanager"}}, "spec": {"containers": [container()]}}}}]
    snap.objects["pods"] += [
        {"kind": "Pod", "metadata": m("gmp-operator-1", ns=ns, labels={"app": "gmp-operator"}),
         "spec": {"containers": [container(ports=[{"name": "web", "containerPort": 10250}])]},
         "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]}}]

    found = by_check(run_checks(snap, settings))
    noisy = [f.resource.name for c in ("service-no-endpoints", "service-port-mismatch")
             for f in found.get(c, []) if f.resource.namespace == ns]
    assert noisy == []
    assert [f.resource.name for f in found["service-no-endpoints"]] == ["web"]  # the real typo still fires
