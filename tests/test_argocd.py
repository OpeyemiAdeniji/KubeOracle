"""Argo CD: syncs are found from a namespace scan and linked to the failures they cause."""

from fixtures import m, ts

from kubesift.analysis import engine
from kubesift.analysis.changes import detect_changes


def _app(name: str, dest: str, resources: list[dict], minutes_ago: float) -> dict:
    return {"kind": "Application", "apiVersion": "argoproj.io/v1alpha1", "metadata": m(name, ns="argocd"),
            "spec": {"destination": {"namespace": dest}},
            "status": {"resources": resources, "operationState": {
                "phase": "Succeeded", "finishedAt": ts(minutes_ago=minutes_ago),
                "syncResult": {"revision": "4f2a9c1e7b3d0000"}}}}


def _with_apps(snap):
    snap.objects["argoapps"] = [
        _app("shop", "shop", [{"kind": "Deployment", "name": "checkout", "namespace": "shop"}], minutes_ago=31),
        _app("billing", "billing", [{"kind": "Deployment", "name": "invoices", "namespace": "billing"}], minutes_ago=20),
    ]
    snap.readable.add("argoapps")
    return snap


def test_namespace_scan_sees_the_app_that_deploys_into_it(snap):
    snap = _with_apps(snap)
    snap.namespace = "shop"
    argo = [c for c in detect_changes(snap) if c.kind == "argocd"]
    assert [c.resource.name for c in argo] == ["shop"]  # lives in argocd, deploys into shop; billing is left out
    assert argo[0].resource.namespace == "argocd"
    assert "deploys to: shop" in argo[0].details
    assert "4f2a9c1e7b" in argo[0].summary


def test_sync_is_linked_to_the_workload_it_manages(snap, settings):
    report = engine.investigate(settings, _with_apps(snap), use_llm=False)
    crash = next(f for f in report.findings if f.check == "pod-crashloop" and f.owner and f.owner.name == "checkout")
    assert any(c.kind == "argocd" and c.resource.name == "shop" for c in crash.related_changes)
    others = [f for f in report.findings if f.kind == "problem" and (f.owner or f.resource).name != "checkout"]
    assert not any(c.kind == "argocd" for f in others for c in f.related_changes)  # only what the app manages

