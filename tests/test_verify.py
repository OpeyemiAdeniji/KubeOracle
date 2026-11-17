"""A fix is only done when the workload is healthy again: verify waits, watches and re-checks."""

import pytest
from fixtures import container, deployment, pod, rs, ts

from kubesift.cluster.client import KubeError
from kubesift.verify import FAILING, RESOLVED, ROLLING, UNKNOWN, rollout_state, verify, workload_of_pod

LABELS = {"app": "web"}


def dep(generation=2, observed=2, replicas=2, updated=2, available=2, total=2, conditions=None):
    d = deployment("web", replicas, available, LABELS, [container()])
    d["metadata"]["generation"] = generation
    d["status"] = {"observedGeneration": observed, "replicas": total, "updatedReplicas": updated,
                   "availableReplicas": available, "readyReplicas": available, "conditions": conditions or []}
    return d


def healthy_pod(name, restarts=0):
    return pod(name, owner_rs="web-2", labels=LABELS, statuses=[
        {"name": "app", "ready": True, "restartCount": restarts, "state": {"running": {"startedAt": ts(minutes_ago=5)}}}])


def crashing_pod(name):
    return pod(name, owner_rs="web-2", labels=LABELS, statuses=[
        {"name": "app", "ready": False, "restartCount": 6, "state": {"waiting": {"reason": "CrashLoopBackOff"}},
         "lastState": {"terminated": {"exitCode": 1, "reason": "Error", "finishedAt": ts(minutes_ago=1)}}}],
        conditions=[{"type": "Ready", "status": "False", "lastTransitionTime": ts(minutes_ago=10)}])


class FakeClient:
    """Answers GETs from a script: each path maps to a list of responses, the last one repeats."""

    def __init__(self, responses):
        self.responses = {k: list(v) for k, v in responses.items()}

    def get(self, path, **query):
        seq = self.responses[path]
        item = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(item, Exception):
            raise item
        return item


DEP_PATH = "/apis/apps/v1/namespaces/shop/deployments/web"
PODS_PATH = "/api/v1/namespaces/shop/pods"


def run(monkeypatch, settings, client, snapshot_pods, timeout=60):
    from test_features import make

    snap = make(deployments=[dep()], replicasets=[rs("web-2", "web", 2, [container()], 10, LABELS)],
                pods=snapshot_pods)
    monkeypatch.setattr("kubesift.cluster.snapshot.collect", lambda *a, **k: snap)
    t = [0.0]

    def sleep(s):
        t[0] += s
    return verify(settings, client, "deploy", "shop", "web", timeout=timeout, settle=20, interval=5,
                  sleep=sleep, clock=lambda: t[0])


@pytest.mark.parametrize("kwargs,finished,failed", [
    ({"observed": 1}, False, False),                                      # controller hasn't seen the change
    ({"updated": 1, "available": 1, "total": 3}, False, False),          # old pods still around
    ({}, True, False),                                                    # done
    ({"replicas": 0, "updated": 0, "available": 0, "total": 0}, True, False),  # scaled to zero
    ({"updated": 1, "conditions": [{"type": "Progressing", "reason": "ProgressDeadlineExceeded"}]}, True, True),
])
def test_deployment_rollout_state(kwargs, finished, failed):
    done, bad, detail = rollout_state("deployments", dep(**kwargs))
    assert (done, bad) == (finished, failed), detail


def test_statefulset_and_daemonset_rollout_state():
    sts = {"metadata": {"generation": 1}, "spec": {"replicas": 3},
           "status": {"observedGeneration": 1, "updatedReplicas": 3, "readyReplicas": 3,
                      "currentRevision": "a", "updateRevision": "b"}}
    assert rollout_state("statefulsets", sts)[0] is False   # revisions differ: still rolling
    sts["status"]["currentRevision"] = "b"
    assert rollout_state("statefulsets", sts)[0] is True
    ds = {"metadata": {"generation": 1}, "status": {"observedGeneration": 1, "desiredNumberScheduled": 4,
                                                    "updatedNumberScheduled": 4, "numberAvailable": 3}}
    assert rollout_state("daemonsets", ds)[0] is False


def test_resolved_when_rolled_out_stable_and_clean(monkeypatch, settings):
    pods = [healthy_pod("web-2-a"), healthy_pod("web-2-b")]
    client = FakeClient({DEP_PATH: [dep(updated=1, available=1, total=3), dep()], PODS_PATH: [{"items": pods}]})
    v = run(monkeypatch, settings, client, pods)
    assert v.status == RESOLVED and v.ok, v
    assert "healthy" in v.summary and v.problems == []


def test_still_failing_when_pods_restart_while_watching(monkeypatch, settings):
    before = {"items": [healthy_pod("web-2-a", 0)]}
    after = {"items": [healthy_pod("web-2-a", 2)]}   # ready at first, then crashed twice
    client = FakeClient({DEP_PATH: [dep(replicas=1, updated=1, available=1, total=1)], PODS_PATH: [before, after]})
    v = run(monkeypatch, settings, client, [healthy_pod("web-2-a", 2)])
    assert v.status == FAILING and "restarted 2 time(s)" in v.summary


def test_still_failing_when_the_checks_still_find_the_problem(monkeypatch, settings):
    client = FakeClient({DEP_PATH: [dep()], PODS_PATH: [{"items": []}]})
    v = run(monkeypatch, settings, client, [crashing_pod("web-2-a")])
    assert v.status == FAILING
    assert any(p.startswith("pod-crashloop") for p in v.problems)


def test_rolling_out_when_the_timeout_passes_without_problems(monkeypatch, settings):
    client = FakeClient({DEP_PATH: [dep(updated=1, available=1, total=3)], PODS_PATH: [{"items": []}]})
    v = run(monkeypatch, settings, client, [healthy_pod("web-2-a")], timeout=30)
    assert v.status == ROLLING and "kubesift verify deploy/web -n shop" in v.summary
    assert v.waited_seconds >= 30


def test_deadline_exceeded_is_a_failure(monkeypatch, settings):
    stuck = dep(updated=1, conditions=[{"type": "Progressing", "reason": "ProgressDeadlineExceeded"}])
    client = FakeClient({DEP_PATH: [stuck], PODS_PATH: [{"items": []}]})
    v = run(monkeypatch, settings, client, [healthy_pod("web-2-a")])
    assert v.status == FAILING and "progress deadline" in v.summary


def test_deleted_workload_is_unknown(monkeypatch, settings):
    client = FakeClient({DEP_PATH: [KubeError(404, "not found")]})
    assert run(monkeypatch, settings, client, []).status == UNKNOWN


def test_deleted_pod_is_verified_through_its_deployment():
    client = FakeClient({
        "/api/v1/namespaces/shop/pods/web-2-a": [healthy_pod("web-2-a")],
        "/apis/apps/v1/namespaces/shop/replicasets/web-2": [rs("web-2", "web", 2, [container()], 10, LABELS)],
    })
    assert workload_of_pod(client, "shop", "web-2-a") == ("deployment", "web")
    bare = pod("lonely")
    with pytest.raises(ValueError, match="no controller"):
        workload_of_pod(FakeClient({"/api/v1/namespaces/shop/pods/lonely": [bare]}), "shop", "lonely")


def test_bad_input_is_refused(settings):
    with pytest.raises(ValueError):
        verify(settings, FakeClient({}), "cronjob", "shop", "web")
    with pytest.raises(ValueError):
        verify(settings, FakeClient({}), "deploy", "shop", "../etc")


# ---------------------------------------------------------------- entry points

def _fake_verification(status):
    from kubesift.verify import Verification
    return Verification(status, "Deployment/shop/web", f"Deployment/shop/web: {status}",
                        ["pod-crashloop: crash"] if status == FAILING else [])


@pytest.mark.parametrize("status,code", [(RESOLVED, 0), (FAILING, 3), (ROLLING, 3)])
def test_cli_verify_exit_codes_and_audit(monkeypatch, settings, status, code):
    from typer.testing import CliRunner

    from kubesift import cli
    from kubesift.store import Store

    monkeypatch.setattr("kubesift.verify.verify", lambda *a, **k: _fake_verification(status))
    monkeypatch.setattr("kubesift.cluster.client.KubeClient", lambda **k: object())
    monkeypatch.setattr(cli, "_store", lambda: Store(settings.data_dir))
    monkeypatch.setattr(cli, "load_settings", lambda **k: settings)
    result = CliRunner().invoke(cli.app, ["verify", "deploy/web", "-n", "shop", "-o", "json"])
    assert result.exit_code == code, result.output
    assert f'"status": "{status}"' in result.output
    assert Store(settings.data_dir).list_audit()[0]["action"] == "verify"


def test_api_verify_runs_in_the_background(monkeypatch, settings):
    import time

    from fastapi.testclient import TestClient

    from kubesift.server import app as server_app

    monkeypatch.setattr("kubesift.verify.verify", lambda *a, **k: _fake_verification(RESOLVED))
    monkeypatch.setattr(server_app, "KubeClient", lambda **k: object())
    client = TestClient(server_app.create_app(settings))
    r = client.post("/api/v1/verify", json={"kind": "deployment", "namespace": "shop", "name": "web"})
    assert r.status_code == 202
    vid = r.json()["id"]
    for _ in range(50):
        body = client.get(f"/api/v1/verify/{vid}").json()
        if body["status"] != "running":
            break
        time.sleep(0.05)
    assert body["status"] == RESOLVED and body["ok"] is True
    assert client.post("/api/v1/verify", json={"kind": "cronjob", "namespace": "shop", "name": "web"}).status_code == 400
    assert client.post("/api/v1/verify", json={"kind": "deploy", "namespace": "shop", "name": "../x"}).status_code == 400
    assert client.get("/api/v1/verify/nope").status_code == 404


def test_slack_posts_whether_the_applied_fix_worked(settings, monkeypatch):
    from kubesift.server import app as server_app
    from kubesift.store import Store

    class P:
        description, kubectl = "restart", "kubectl rollout restart"

    posts = []
    monkeypatch.setattr("kubesift.remediate.plan", lambda *a, **k: P())
    monkeypatch.setattr("kubesift.remediate.execute", lambda *a, **k: "applied")
    monkeypatch.setattr("kubesift.verify.verify", lambda *a, **k: _fake_verification(FAILING))
    monkeypatch.setattr(server_app, "KubeClient", lambda **k: object())
    monkeypatch.setattr(server_app, "_slack_post", lambda url, blocks, title: posts.append((title, blocks)))
    settings.allow_writes, settings.slack_allowed_users = True, ["U1"]
    value = {"action": "restart", "kind": "deployment", "namespace": "shop", "name": "web", "dry_run": False}
    server_app._slack_fix(settings, Store(settings.data_dir), value, "U1", "https://hooks.slack.com/x")
    assert [t for t, _ in posts] == ["KubeSift fix", "KubeSift verification"]
    assert ":x:" in posts[1][1][0]["text"]["text"]


def test_mcp_has_a_read_only_verify_tool(settings):
    import asyncio

    from kubesift.mcp_server import build_server

    tools = {t.name: t for t in asyncio.run(build_server(settings).list_tools())}
    assert tools["verify_fix"].annotations.read_only_hint is True


# ------------------------------------------------- rollback goes to a different version, not a restart of this one

GOOD = [container(env=[{"name": "API_URL", "value": "http://api"}])]
BAD = [container()]


def _rs(revision, containers, restarted=False):
    r = rs(f"web-{revision}", "web", revision, containers, 60 - revision, LABELS)
    r["metadata"]["ownerReferences"][0]["uid"] = "uid-web"
    r["spec"]["template"]["metadata"]["labels"]["pod-template-hash"] = f"h{revision}"
    if restarted:
        r["spec"]["template"]["metadata"]["annotations"] = {"kubectl.kubernetes.io/restartedAt": ts(minutes_ago=1)}
    return r


def _history():
    # 1 good -> 2 bad (removed API_URL) -> 3 restart of 2 (`kubectl rollout restart`)
    return [_rs(1, GOOD), _rs(2, BAD), _rs(3, BAD, restarted=True)]


def test_rollback_skips_revisions_that_only_restarted_the_current_version():
    from kubesift.remediate import plan

    client = FakeClient({DEP_PATH: [dep()], "/apis/apps/v1/namespaces/shop/replicasets": [{"items": _history()}]})
    p = plan(client, "rollback", "deployment", "shop", "web")
    assert "revision 1" in p.description and p.kubectl.endswith("--to-revision=1")
    p = plan(client, "rollback", "deployment", "shop", "web", to_revision=2)   # explicit choice wins
    assert p.kubectl.endswith("--to-revision=2")
    with pytest.raises(ValueError, match="not a previous revision"):
        plan(client, "rollback", "deployment", "shop", "web", to_revision=9)
    only_restarts = [_rs(1, BAD), _rs(2, BAD, restarted=True)]
    client = FakeClient({DEP_PATH: [dep()], "/apis/apps/v1/namespaces/shop/replicasets": [{"items": only_restarts}]})
    with pytest.raises(ValueError, match="only a restart"):
        plan(client, "rollback", "deployment", "shop", "web")


def test_a_restart_reads_as_a_restart_and_points_at_the_change_that_brought_this_version():
    from datetime import timedelta

    from test_features import make

    from kubesift.analysis.changes import detect_changes

    c = next(c for c in detect_changes(make(replicasets=_history()), timedelta(hours=2)) if c.kind == "rollout")
    assert c.summary.startswith("Deployment web restarted (revision 3); this version came in revision 2")
    assert "env removed API_URL" in c.summary
    assert c.rollback_command.endswith("--to-revision=1")


def test_pods_shutting_down_after_the_rollout_do_not_count(monkeypatch, settings):
    old = crashing_pod("web-1-old")
    old["metadata"]["deletionTimestamp"] = ts(minutes_ago=0)
    client = FakeClient({DEP_PATH: [dep()], PODS_PATH: [{"items": []}]})
    v = run(monkeypatch, settings, client, [old, healthy_pod("web-2-a")])
    assert v.status == RESOLVED, v
