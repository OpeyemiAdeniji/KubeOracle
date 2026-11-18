"""The fixes that match KubeSift's diagnoses: Service selector, memory limit, RBAC grant, auto-rollback."""

from datetime import timedelta

import pytest
from fixtures import container, deployment, pod

from kubesift.cluster.client import KubeError
from kubesift.remediate import execute, plan, verification_target

LABELS = {"app": "web"}


class FakeClient:
    def __init__(self, responses=None):
        self.responses, self.requests = responses or {}, []

    def get(self, path, **query):
        key = path + ("?" + "&".join(f"{k}={v}" for k, v in sorted(query.items())) if query else "")
        for k in (key, path):
            if k in self.responses:
                item = self.responses[k]
                if isinstance(item, Exception):
                    raise item
                return item
        raise KubeError(404, f"{path} not found")

    def request(self, method, path, query=None, body=None, content_type=None):
        self.requests.append((method, path, query, body, content_type))
        return {}


NS = "/api/v1/namespaces/shop"
DEP = "/apis/apps/v1/namespaces/shop/deployments/web"


def web(resources=None, containers=None):
    d = deployment("web", 2, 2, LABELS, containers or [container(resources=resources if resources is not None else
                                                                 {"limits": {"memory": "256Mi"},
                                                                  "requests": {"memory": "256Mi"}})])
    return d


# ---------------------------------------------------------------- set-selector

def test_selector_copied_from_the_workload_and_checked_against_real_pods():
    c = FakeClient({DEP: web(), f"{NS}/services/web": {"spec": {"selector": {"app": "wrong"}}},
                    f"{NS}/pods?labelSelector=app=web": {"items": [pod("web-1"), pod("web-2")]}})
    p = plan(c, "set-selector", "svc", "shop", "web", match="deploy/web")
    assert p.body == [{"op": "replace", "path": "/spec/selector", "value": {"app": "web"}}]
    assert "from app=wrong to app=web (2 pod(s) match)" in p.description
    assert verification_target(c, p) == ("service", "web")


def test_selector_refused_when_no_pod_would_match_or_labels_are_bad():
    c = FakeClient({f"{NS}/services/web": {"spec": {"selector": {"app": "wrong"}}},
                    f"{NS}/pods?labelSelector=app=nothing": {"items": []}})
    with pytest.raises(ValueError, match="no pods"):
        plan(c, "set-selector", "svc", "shop", "web", selector={"app": "nothing"})
    with pytest.raises(ValueError, match="invalid label"):
        plan(c, "set-selector", "svc", "shop", "web", selector={"app": "a b"})
    with pytest.raises(ValueError, match="service"):
        plan(c, "set-selector", "deployment", "shop", "web", selector={"app": "web"})


# ---------------------------------------------------------------- set-memory

def test_memory_limit_is_raised_and_the_request_follows():
    c = FakeClient({DEP: web({"limits": {"memory": "256Mi"}, "requests": {"memory": "1Gi"}})})
    p = plan(c, "set-memory", "deployment", "shop", "web", memory="512Mi")
    ops = {o["path"]: o["value"] for o in p.body}
    assert ops["/spec/template/spec/containers/0/resources/limits/memory"] == "512Mi"
    assert ops["/spec/template/spec/containers/0/resources/requests/memory"] == "512Mi"  # request > limit is invalid
    assert "was 256Mi" in p.description and "--limits=memory=512Mi" in p.kubectl


def test_memory_only_goes_up_and_needs_the_container_when_ambiguous():
    c = FakeClient({DEP: web()})
    with pytest.raises(ValueError, match="not above"):
        plan(c, "set-memory", "deployment", "shop", "web", memory="128Mi")
    with pytest.raises(ValueError, match="--memory"):
        plan(c, "set-memory", "deployment", "shop", "web", memory="lots")
    two = FakeClient({DEP: web(containers=[container("app"), container("sidecar")])})
    with pytest.raises(ValueError, match="app, sidecar"):
        plan(two, "set-memory", "deployment", "shop", "web", memory="1Gi")
    no_limits = FakeClient({DEP: web(resources={})})
    p = plan(no_limits, "set-memory", "deployment", "shop", "web", memory="1Gi")
    assert p.body == [{"op": "add", "path": "/spec/template/spec/containers/0/resources",
                       "value": {"limits": {"memory": "1Gi"}}}]


# ---------------------------------------------------------------- grant

def test_grant_creates_a_namespaced_role_and_binding_for_one_permission():
    c = FakeClient({f"{NS}/serviceaccounts/worker": {"metadata": {"name": "worker"}}})
    p = plan(c, "grant", "sa", "shop", "worker", verbs=["list"], resource="secrets")
    role, (_, binding_path, binding, _) = p.body, p.more[0]
    assert role["kind"] == "Role" and role["rules"] == [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["list"]}]
    assert binding_path.endswith("/namespaces/shop/rolebindings")
    assert binding["subjects"] == [{"kind": "ServiceAccount", "name": "worker", "namespace": "shop"}]
    assert binding["roleRef"]["name"] == role["metadata"]["name"]
    execute(c, p, dry_run=True)
    assert [r[0] for r in c.requests] == ["POST", "POST"] and all(r[2] == {"dryRun": "All"} for r in c.requests)


@pytest.mark.parametrize("verbs,resource,group,match", [
    (["*"], "secrets", "", "verb"), (["escalate"], "roles", "", "verb"), (["list"], "*", "", "resource"),
    (["create"], "rolebindings", "", "RBAC"), (["get"], "roles", "rbac.authorization.k8s.io", "RBAC"),
    ([], "secrets", "", "verb"),
])
def test_grant_refuses_wildcards_and_rbac_itself(verbs, resource, group, match):
    c = FakeClient({f"{NS}/serviceaccounts/worker": {}})
    with pytest.raises(ValueError, match=match):
        plan(c, "grant", "sa", "shop", "worker", verbs=verbs, resource=resource, api_group=group)


def test_grant_needs_an_existing_account_and_verifies_the_workload_running_as_it():
    with pytest.raises(KubeError):
        plan(FakeClient(), "grant", "sa", "shop", "ghost", verbs=["list"], resource="secrets")
    d = web()
    d["spec"]["template"]["spec"]["serviceAccountName"] = "worker"
    c = FakeClient({f"{NS}/serviceaccounts/worker": {}, "/apis/apps/v1/namespaces/shop/deployments": {"items": [d]},
                    "/apis/apps/v1/namespaces/shop/statefulsets": {"items": []},
                    "/apis/apps/v1/namespaces/shop/daemonsets": {"items": []}})
    p = plan(c, "grant", "sa", "shop", "worker", verbs=["list"], resource="secrets")
    assert verification_target(c, p) == ("deployment", "web")


def test_the_api_refuses_grant_unless_rbac_fixes_are_enabled(settings):
    from fastapi.testclient import TestClient

    from kubesift.server import app as server_app

    settings.allow_writes = True
    client = TestClient(server_app.create_app(settings))
    body = {"action": "grant", "kind": "sa", "namespace": "shop", "name": "worker", "verbs": ["list"],
            "resource": "secrets", "dry_run": True}
    r = client.post("/api/v1/fix", json=body)
    assert r.status_code == 403 and "ALLOW_RBAC_FIXES" in r.json()["detail"]


# ---------------------------------------------------------------- Service verification

def test_service_is_verified_by_its_ready_endpoints(monkeypatch, settings):
    from test_features import make

    from kubesift.verify import FAILING, RESOLVED, verify

    slices = "/apis/discovery.k8s.io/v1/namespaces/shop/endpointslices?labelSelector=kubernetes.io/service-name=web"
    ready = {"items": [{"endpoints": [{"conditions": {"ready": True}}, {"conditions": {"ready": False}}]}]}
    monkeypatch.setattr("kubesift.cluster.snapshot.collect", lambda *a, **k: make())
    t = [0.0]
    kw = {"timeout": 30, "interval": 5, "sleep": lambda s: t.__setitem__(0, t[0] + s), "clock": lambda: t[0]}
    v = verify(settings, FakeClient({slices: ready}), "svc", "shop", "web", **kw)
    assert v.status == RESOLVED and "1 ready endpoint" in v.summary
    v = verify(settings, FakeClient({slices: {"items": []}}), "svc", "shop", "web", **kw)
    assert v.status == FAILING and t[0] >= 30


# ---------------------------------------------------------------- auto-rollback

def _broken_rollout_report(settings, minutes_ago=5, ns="shop"):
    from kubesift.models import Change, Finding, Incident, Report, ResourceRef, Severity
    from kubesift.util import now

    dep = ResourceRef(kind="Deployment", name="web", namespace=ns)
    trigger = Change(time=now() - timedelta(minutes=minutes_ago), kind="rollout", resource=dep,
                     summary="Deployment web rolled out revision 2: app: env removed API_URL",
                     rollback_command=f"kubectl rollout undo deployment/web -n {ns} --to-revision=1")
    f = Finding(check="pod-crashloop", title="Container is crash looping", severity=Severity.CRITICAL,
                category="workload", resource=ResourceRef(kind="Pod", name="web-2-a", namespace=ns), message="crash",
                owner=dep)
    inc = Incident(id="x", title="web crash looping", severity=Severity.CRITICAL, layer="app", root=dep,
                   findings=[f], likely_trigger=trigger)
    return Report(cluster="c", namespace=ns, collected_at=now(), findings=[f], incidents=[inc])


def test_auto_rollback_only_when_every_condition_holds(settings):
    from kubesift.autorollback import candidates

    report = _broken_rollout_report(settings)
    assert candidates(settings, report) == []                 # off by default
    settings.auto_rollback_namespaces = ["shop"]
    assert candidates(settings, report) == []                 # still needs allow_writes
    settings.allow_writes = True
    assert len(candidates(settings, report)) == 1
    assert candidates(settings, _broken_rollout_report(settings, minutes_ago=90)) == []   # old rollout
    assert candidates(settings, _broken_rollout_report(settings, ns="payments")) == []     # not listed
    settings.auto_rollback_namespaces = ["*"]
    assert candidates(settings, _broken_rollout_report(settings, ns="kube-system")) == []  # never system
    assert len(candidates(settings, _broken_rollout_report(settings, ns="payments"))) == 1


def test_auto_rollback_acts_once_dry_runs_first_and_verifies(settings, monkeypatch):
    from kubesift import autorollback
    from kubesift.store import Store
    from kubesift.verify import Verification

    settings.allow_writes, settings.auto_rollback_namespaces = True, ["shop"]
    store = Store(settings.data_dir)
    calls = []

    class P:
        description, target, action = "Roll back to revision 1 (current 2).", "deployment/shop/web", "rollback"

    monkeypatch.setattr("kubesift.remediate.plan", lambda *a, **k: P())
    monkeypatch.setattr("kubesift.remediate.execute", lambda c, p, dry_run, *a, **k: calls.append(dry_run) or "applied")
    monkeypatch.setattr("kubesift.verify.verify", lambda *a, **k: Verification("resolved", "Deployment/shop/web", "ok"))
    report = _broken_rollout_report(settings)
    done = autorollback.run(settings, store, report, FakeClient({DEP: web()}))
    assert calls == [True, False] and done[0].verification["ok"] is True
    assert autorollback.message(done[0]) == "Auto-rolled back shop/web: verified healthy"
    assert autorollback.run(settings, store, report, FakeClient({DEP: web()})) == []   # handled once
    assert calls == [True, False]


def test_auto_rollback_leaves_gitops_managed_deployments_to_their_tool(settings, monkeypatch):
    from kubesift import autorollback
    from kubesift.store import Store

    settings.allow_writes, settings.auto_rollback_namespaces = True, ["shop"]
    managed = web()
    managed["metadata"]["annotations"] = {"argocd.argoproj.io/tracking-id": "shop:apps/Deployment:shop/web"}
    applied = []
    monkeypatch.setattr("kubesift.remediate.execute", lambda *a, **k: applied.append(1) or "applied")
    done = autorollback.run(settings, Store(settings.data_dir), _broken_rollout_report(settings), FakeClient({DEP: managed}))
    assert applied == [] and "Argo CD" in done[0].result
    assert "roll back in Argo CD" in autorollback.message(done[0])


@pytest.mark.parametrize("short", ["deploy", "deployment", "deployments", "DEPLOY"])
def test_short_kind_names_work_like_kubectl(short):
    c = FakeClient({DEP: web()})
    assert plan(c, "set-memory", short, "shop", "web", memory="1Gi").target == "deployment/shop/web"
