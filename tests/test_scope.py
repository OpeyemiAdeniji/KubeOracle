"""Which namespaces a KubeSift covers: an exclude pattern, or one namespace only."""

import pytest
from fixtures import m, pod

from kubesift.config import ScopeError


def test_restrict_namespace_turns_all_into_one_and_refuses_others(settings):
    settings.restrict_namespace = "shop"
    assert settings.scope(None) == "shop" and settings.scope("shop") == "shop"
    with pytest.raises(ScopeError, match="only namespace shop"):
        settings.scope("payments")
    settings.restrict_namespace = None
    assert settings.scope(None) is None and settings.scope("payments") == "payments"


def test_excluded_namespaces_are_left_out_of_all_namespace_scans(settings, monkeypatch):
    from test_features import make

    from kubesift.analysis import engine

    snap = make(pods=[pod("a", ns="shop"), pod("b", ns="dev-alice"), pod("c", ns="kube-system")],
                namespaces=[{"kind": "Namespace", "metadata": m(n, None)} for n in ("shop", "dev-alice", "kube-system")],
                nodes=[{"kind": "Node", "metadata": m("node-1", None)}])
    snap.logs = {"dev-alice/b/app": {"errors": []}, "shop/a/app": {"errors": []}}
    monkeypatch.setattr(engine, "collect", lambda *a, **k: snap)
    monkeypatch.setattr(engine, "KubeClient", lambda **k: None)
    settings.namespace_exclude = r"^(dev-|kube-)"
    out = engine.snapshot_for(settings, None)
    assert [p["metadata"]["name"] for p in out.objects["pods"]] == ["a"]
    assert [n["metadata"]["name"] for n in out.objects["namespaces"]] == ["shop"]
    assert out.objects["nodes"] and list(out.logs) == ["shop/a/app"]   # cluster-scoped objects stay


def test_the_api_answers_403_outside_the_namespace(settings, snap, monkeypatch):
    from fastapi.testclient import TestClient

    from kubesift.server import app as server_app

    settings.restrict_namespace = "shop"
    monkeypatch.setattr(server_app, "snapshot_for", lambda *a, **k: snap)
    c = TestClient(server_app.create_app(settings))
    assert c.post("/api/v1/diagnose", json={"namespace": "payments", "llm": False}).status_code == 403
    assert c.post("/api/v1/diagnose", json={"namespace": "shop", "llm": False}).status_code == 200
    assert c.post("/api/v1/verify", json={"kind": "deployment", "namespace": "payments", "name": "x"}).status_code == 403
    assert c.get("/api/v1/logs", params={"namespace": "payments", "pod": "x"}).status_code == 403
    assert c.post("/api/v1/fix", json={"action": "restart", "kind": "deployment", "namespace": "payments",
                                       "name": "x"}).status_code == 403
