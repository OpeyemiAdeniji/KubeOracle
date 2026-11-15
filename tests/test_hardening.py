"""Regression tests for issues found in the pre-release review."""

import copy
import json

import pytest
from fixtures import broken_cluster, container, deployment, m, pod

from kubesift.util import check_name


@pytest.mark.parametrize("bad", ["../kube-system", "a/b", "Shop", "x..y", "a b", "-x", "x" * 300])
def test_names_are_validated(bad):
    with pytest.raises(ValueError):
        check_name(bad)


def test_valid_names_pass():
    assert check_name("payments-api.v2") == "payments-api.v2"
    assert check_name(None) is None


def test_remediation_rejects_path_tricks():
    from kubesift.remediate import plan
    with pytest.raises(ValueError):
        plan(None, "delete-pod", "pod", "shop", "../../../namespaces/kube-system")
    with pytest.raises(ValueError):
        plan(None, "restart", "deployment", "shop/../kube-system", "x")


def test_server_rejects_bad_input(settings, snap, monkeypatch):
    from fastapi.testclient import TestClient

    from kubesift.server import app as server_app

    monkeypatch.setattr(server_app, "snapshot_for", lambda *a, **k: snap)
    client = TestClient(server_app.create_app(settings))
    assert client.get("/api/v1/changes", params={"namespace": "../x"}).status_code == 400
    assert client.get("/api/v1/changes", params={"since": "abc"}).status_code == 400
    r = client.post("/api/v1/webhooks/alertmanager", json={"alerts": [
        {"status": "firing", "labels": {"alertname": "X", "namespace": "../../etc"}}]})
    assert r.json()["namespaces"] == []


def test_previous_logs_unavailable_falls_back_to_current():
    from kubesift.cluster.snapshot import Snapshot, collect_logs

    crashing = pod("app-1", statuses=[{"name": "app", "ready": False, "restartCount": 3,
                                       "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                                       "lastState": {"terminated": {"exitCode": 1}}}])

    class FakeClient:
        def pod_logs(self, ns, pod, container, previous, tail=200):
            if previous:
                return "unable to retrieve container logs for containerd://abc123"
            return "Traceback (most recent call last):\nKeyError: 'DATABASE_URL'\n"

    snap = Snapshot()
    snap.objects["pods"] = [crashing]
    collect_logs(FakeClient(), snap)
    entry = snap.logs["shop/app-1/app"]
    assert entry["previous"] is False
    assert any("DATABASE_URL" in line for line in entry["errors"])
    assert not any("unable to retrieve" in line for line in entry["errors"])


def test_snapshot_masks_literal_secrets_in_env():
    from kubesift.cluster.snapshot import _shrink

    d = deployment("api", 1, 1, {"app": "api"}, [container(env=[
        {"name": "DB_PASSWORD", "value": "hunter2hunter2"},
        {"name": "DATABASE_URL", "value": "postgres://admin:s3cret@db:5432/app"},
        {"name": "LOG_LEVEL", "value": "debug"}])])
    _shrink(d)
    env = {e["name"]: e["value"] for e in d["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["DB_PASSWORD"] == "[REDACTED]"
    assert "s3cret" not in env["DATABASE_URL"] and "db:5432" in env["DATABASE_URL"]
    assert env["LOG_LEVEL"] == "debug"


def test_send_logs_to_llm_false_is_respected_everywhere(settings, monkeypatch):
    from kubesift.analysis import engine
    from kubesift.analysis.llm import Provider

    prompts = []

    class Fake(Provider):
        name = "fake"

        def complete_json(self, system, user, schema):
            prompts.append(user)
            return {"summary": "s", "root_causes": [], "next_checks": [], "answer": "a", "confidence": 50,
                    "evidence": [], "commands": [], "follow_up": []}

    monkeypatch.setattr(engine, "get_provider", lambda s: Fake())
    settings.send_logs_to_llm = False
    snap = broken_cluster()
    report = engine.investigate(settings, snap, use_llm=True)
    engine.ask(settings, broken_cluster(), "why is checkout-7c9a-x1 failing?", use_llm=True)
    assert prompts and all("KeyError" not in p for p in prompts)
    # the user's own report keeps its log evidence
    crash = next(f for f in report.findings if f.check == "pod-crashloop")
    assert any(e.startswith("log ") for e in crash.evidence)


def test_time_travel_has_no_false_differences_after_storage():
    from kubesift.timetravel import diff, digest

    d = digest(broken_cluster())
    stored = json.loads(json.dumps(d, default=str))
    assert diff(stored, d) == []


def test_slack_apply_needs_explicit_allowlist(settings, monkeypatch):
    from kubesift.server import app as server_app

    calls = []

    class P:
        description, kubectl = "restart", "kubectl rollout restart"

    monkeypatch.setattr("kubesift.remediate.plan", lambda *a, **k: P())
    monkeypatch.setattr("kubesift.remediate.execute", lambda client, p, dry_run, *a, **k: calls.append(dry_run) or "ok")
    monkeypatch.setattr(server_app, "KubeClient", lambda **k: None)
    monkeypatch.setattr(server_app, "_slack_post", lambda *a: None)
    settings.allow_writes = True
    value = {"action": "restart", "kind": "deployment", "namespace": "shop", "name": "x", "dry_run": False}
    server_app._slack_fix(settings, None, value, "U1", "https://hooks.slack.com/x")
    settings.slack_allowed_users = ["U1"]
    server_app._slack_fix(settings, None, value, "U1", "https://hooks.slack.com/x")
    assert calls == [True, False]  # empty allowlist -> forced dry run; listed user -> applied


@pytest.mark.parametrize("url", ["https://127.0.0.1/v2/", "https://169.254.169.254/latest/", "http://example.com/",
                                 "https://10.0.0.5/v2/", "https://[::1]/v2/", "file:///etc/passwd"])
def test_image_check_never_contacts_private_addresses(url):
    from kubesift.preflight import _public_https
    assert _public_https(url) is False


def test_image_check_skips_private_registry_without_network(monkeypatch):
    import httpx

    from kubesift import preflight

    def boom(*a, **k):
        raise AssertionError("must not make a request")

    monkeypatch.setattr(httpx.Client, "head", boom)
    assert preflight.image_exists("10.0.0.5:5000/team/app:1.0") is None
    assert preflight.image_exists("localhost:5000/app:1.0") is None


def test_preflight_quota_counts_only_the_increase(settings):
    from kubesift.lint import _docs_from_text
    from kubesift.preflight import preflight

    live = broken_cluster()
    live.objects["resourcequotas"] = [{"kind": "ResourceQuota", "metadata": m("q"), "status": {
        "hard": {"requests.cpu": "1"}, "used": {"requests.cpu": "900m"}}}]
    same = copy.deepcopy(live.get("deployments", "shop", "checkout"))
    same["spec"]["template"]["spec"]["containers"][0]["resources"] = {"requests": {"cpu": "100m", "memory": "128Mi"},
                                                                    "limits": {"memory": "256Mi"}}
    res = preflight([json.loads(json.dumps(same))], live, settings)
    assert not [f for f in res.findings if f.check == "preflight-quota-exceeded"]  # unchanged usage
    bigger = copy.deepcopy(same)
    bigger["spec"]["replicas"] = 5
    res = preflight([bigger], live, settings)
    assert [f for f in res.findings if f.check == "preflight-quota-exceeded"]
    over = copy.deepcopy(live)
    over.objects["resourcequotas"][0]["status"]["used"]["requests.cpu"] = "2"  # already over quota
    res = preflight(_docs_from_text("apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: new, namespace: shop}\n"
                                    "spec:\n  template:\n    spec:\n      containers: [{name: c, image: x:1, "
                                    "resources: {requests: {cpu: 100m}}}]\n", "x"), over, settings)
    assert [f for f in res.findings if f.check == "preflight-quota-exceeded"]


def test_preflight_leaves_cluster_scoped_kinds_alone(settings):
    from kubesift.lint import _docs_from_text
    from kubesift.preflight import preflight

    docs = _docs_from_text("apiVersion: cert-manager.io/v1\nkind: ClusterIssuer\nmetadata: {name: le}\n---\n"
                           "apiVersion: apiextensions.k8s.io/v1\nkind: CustomResourceDefinition\nmetadata: {name: x.y.z}\n",
                           "x")
    preflight(docs, broken_cluster(), settings, default_namespace="prod")
    assert all("namespace" not in d["metadata"] for d in docs)
