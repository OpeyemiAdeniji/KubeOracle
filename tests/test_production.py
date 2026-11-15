"""Behaviour that matters when KubeSift runs as a long-lived service or on big clusters."""

import time
from collections import defaultdict
from datetime import timedelta

from fixtures import container, deployment, event, m, pod, rs

from kubesift.analysis.engine import investigate
from kubesift.cluster.snapshot import Snapshot


def big_cluster(namespaces=100, per_ns=50) -> Snapshot:
    o = defaultdict(list)
    for i in range(namespaces):
        ns = f"team-{i}"
        for j in range(per_ns // 5):
            name, labels = f"svc{j}", {"app": f"svc{j}"}
            c = [container(ports=[{"containerPort": 8080}])]
            o["deployments"].append(deployment(name, 5, 5, labels, c, ns=ns))
            r = rs(f"{name}-1", name, 1, c, 600, labels)
            r["metadata"]["namespace"] = ns
            o["replicasets"].append(r)
            o["services"].append({"kind": "Service", "metadata": m(name, ns),
                                  "spec": {"selector": labels, "ports": [{"port": 80, "targetPort": 8080}]}})
            for k in range(5):
                p = pod(f"{name}-1-{k}", f"{name}-1", labels, c, ns=ns, node=f"node-{k}")
                o["pods"].append(p)
                o["events"].append(event("Pod", p["metadata"]["name"], "BackOff", "Back-off restarting", ns=ns))
    snap = Snapshot(context="big", live=True, server_version="v1.34.0")
    snap.objects = o
    snap.readable = set(o) | {"endpointslices", "secrets", "configmaps"}
    return snap


def test_large_cluster_is_fast(settings):
    snap = big_cluster()
    assert len(snap.all("pods")) == 5000
    start = time.perf_counter()
    report = investigate(settings, snap, use_llm=False)
    elapsed = time.perf_counter() - start
    assert report.findings
    assert elapsed < 30, f"5000-pod analysis took {elapsed:.1f}s"


def test_history_retention(settings, snap):
    from kubesift.store import Store

    store = Store(settings.data_dir)
    report = investigate(settings, snap, use_llm=False)
    old_id = store.save_report(report)
    store._exec("UPDATE reports SET created = ? WHERE id = ?", ((report.collected_at - timedelta(days=40)).isoformat(), old_id))
    new_id = store.save_report(report)
    assert store.prune(30) == 1
    assert store.get_report(old_id) is None and store.get_report(new_id) is not None


def test_server_cache_and_alert_cooldown(settings, snap, monkeypatch):
    from fastapi.testclient import TestClient

    from kubesift.server import app as server_app

    calls = []

    def fake_snapshot(*a, **k):
        calls.append(a)
        return snap

    monkeypatch.setattr(server_app, "snapshot_for", fake_snapshot)
    monkeypatch.setattr(server_app, "_alert_investigation", lambda *a: None)
    client = TestClient(server_app.create_app(settings))
    for _ in range(3):
        assert client.post("/api/v1/diagnose", json={"llm": False}).status_code == 200
    assert len(calls) == 1  # one cluster read served three requests

    alert = {"alerts": [{"status": "firing", "labels": {"alertname": "X", "namespace": "shop"}}]}
    assert client.post("/api/v1/webhooks/alertmanager", json=alert).json()["investigating"] == ["shop"]
    assert client.post("/api/v1/webhooks/alertmanager", json=alert).json()["investigating"] == []

    assert client.post("/api/v1/ask", json={"question": "x" * 5000}).status_code == 422
