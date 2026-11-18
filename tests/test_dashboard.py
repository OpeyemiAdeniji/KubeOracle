"""Dashboard features: the incident map, the log viewer, live updates."""

import pytest
from fixtures import container, deployment, m, pod, rs, ts

from kubesift.topology import build_map

LABELS = {"app": "web"}


def cluster(*, selector=None, secret=True, ready=True):
    from test_features import make

    env = [{"name": "DB", "valueFrom": {"secretKeyRef": {"name": "db", "key": "password"}}}]
    statuses = [{"name": "app", "ready": True, "restartCount": 0, "state": {"running": {"startedAt": ts(minutes_ago=5)}}}] \
        if ready else [{"name": "app", "ready": False, "restartCount": 0,
                        "state": {"waiting": {"reason": "CreateContainerConfigError"}}}]
    snap = make(
        deployments=[deployment("web", 1, 1 if ready else 0, LABELS, [container(env=env)])],
        replicasets=[rs("web-1", "web", 1, [container(env=env)], 30, LABELS)],
        pods=[pod("web-1-a", owner_rs="web-1", labels=LABELS, statuses=statuses)],
        services=[{"kind": "Service", "metadata": m("web"), "spec": {"selector": selector or LABELS}}],
        endpointslices=[{"kind": "EndpointSlice", "metadata": m("web-x", labels={"kubernetes.io/service-name": "web"}),
                         "endpoints": [{"conditions": {"ready": ready}}] if (selector is None) else []}],
        ingresses=[{"kind": "Ingress", "metadata": m("web"), "spec": {"rules": [{"host": "shop.example.com", "http": {
            "paths": [{"backend": {"service": {"name": "web"}}}]}}]}}],
        secrets=[{"kind": "Secret", "metadata": m("db")}] if secret else [],
        nodes=[{"kind": "Node", "metadata": m("node-1", None),
                "status": {"conditions": [{"type": "Ready", "status": "True"}]}}],
    )
    return snap


def by_id(graph):
    return {n["id"]: n for n in graph["nodes"]}, {(e["from"], e["to"]): e for e in graph["edges"]}


def test_healthy_workload_maps_its_whole_path():
    nodes, edges = by_id(build_map(cluster(), "Deployment", "shop", "web"))
    for nid in ("Ingress/web", "Service/web", "Deployment/web", "Pod/web-1-a", "Node/node-1", "Secret/db"):
        assert nodes[nid]["status"] == "ok", nid
    assert ("Ingress/web", "Service/web") in edges and ("Service/web", "Deployment/web") in edges


def test_missing_secret_is_the_red_link():
    nodes, edges = by_id(build_map(cluster(secret=False, ready=False), "Deployment", "shop", "web"))
    assert nodes["Secret/db"]["status"] == "bad" and nodes["Secret/db"]["detail"] == "missing"
    assert edges[("Deployment/web", "Secret/db")]["status"] == "bad"
    assert nodes["Pod/web-1-a"]["detail"] == "CreateContainerConfigError"


def test_service_whose_selector_matches_nothing():
    graph = build_map(cluster(selector={"app": "wbe"}), "Service", "shop", "web")
    nodes, edges = by_id(graph)
    assert nodes["Service/web"]["status"] == "bad"
    broken = [e for e in graph["edges"] if e["status"] == "bad"]
    assert broken and broken[0]["label"] == "selector matches no pods"
    assert any(n["name"] == "no pods match app=wbe" for n in graph["nodes"])


def test_a_pod_maps_through_its_workload_and_unknown_objects_say_so():
    nodes, _ = by_id(build_map(cluster(), "Pod", "shop", "web-1-a"))
    assert "Deployment/web" in nodes
    assert "not found" in build_map(cluster(), "Deployment", "shop", "nope")["error"]


# ---------------------------------------------------------------- endpoints

@pytest.fixture
def client(settings, monkeypatch):
    from fastapi.testclient import TestClient

    from kubesift.server import app as server_app

    monkeypatch.setattr(server_app, "snapshot_for", lambda *a, **k: cluster(secret=False, ready=False))
    return TestClient(server_app.create_app(settings)), server_app


def test_map_endpoint_and_input_checks(client):
    c, _ = client
    r = c.get("/api/v1/map", params={"namespace": "shop", "kind": "deployment", "name": "web"})
    assert r.status_code == 200 and any(n["status"] == "bad" for n in r.json()["nodes"])
    assert c.get("/api/v1/map", params={"namespace": "shop", "kind": "cronjob", "name": "web"}).status_code == 400
    assert c.get("/api/v1/map", params={"namespace": "shop", "kind": "deployment", "name": "../x"}).status_code == 400


def test_logs_are_redacted_and_can_be_switched_off(client, settings, monkeypatch):
    c, server_app = client

    class FakeKube:
        def __init__(self, **k):
            pass

        def get(self, path):
            return {"spec": {"containers": [{"name": "app"}, {"name": "sidecar"}]}}

        def pod_logs(self, ns, pod, container, previous, tail=200):
            return "starting\nconnecting to postgres://admin:hunter2@db:5432/app\nKeyError: 'API_URL'"

    monkeypatch.setattr(server_app, "KubeClient", FakeKube)
    r = c.get("/api/v1/logs", params={"namespace": "shop", "pod": "web-1-a"}).json()
    assert r["container"] == "app" and r["containers"] == ["app", "sidecar"]
    assert "hunter2" not in "\n".join(r["lines"]) and r["lines"][-1] == "KeyError: 'API_URL'"
    assert c.get("/api/v1/logs", params={"namespace": "shop", "pod": "web-1-a", "container": "nope"}).status_code == 400
    settings.dashboard_logs = False
    assert c.get("/api/v1/logs", params={"namespace": "shop", "pod": "web-1-a"}).status_code == 403


def test_stream_explains_when_background_scans_are_off(client):
    c, _ = client
    r = c.get("/api/v1/stream")
    assert r.status_code == 200 and '"live": false' in r.text and "scan" in r.text


def test_live_payload_carries_what_the_page_needs(settings, snap):
    from kubesift.analysis.engine import investigate
    from kubesift.server.app import _live_payload

    p = _live_payload(investigate(settings, snap, use_llm=False))
    inc = p["incidents"][0]
    assert p["live"] is True and set(inc) >= {"id", "title", "severity", "root", "message", "trigger", "findings"}
    assert isinstance(inc["findings"], int)  # a count: the page must not treat it as a list


def test_the_dashboard_page_is_never_cached(client):
    c, _ = client
    assert c.get("/").headers["cache-control"] == "no-cache"
