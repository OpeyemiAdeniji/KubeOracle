"""A laptop's Docker (or a server's node) restarting is one event, not a crash loop in every system pod.

Recreates a real kind cluster on a Mac after Docker Desktop restarted: every container stopped at the same second
with reason Unknown (exit 255), all are Running again, and local-path-provisioner failed once 50 s later while the
API server came back. Only the genuinely broken app (a bad image tag) is a problem.
"""

from fixtures import pod, ts

from kubesift.checks import run_checks

NODE = "kubernetes-cluster-control-plane"


def back_up(name, restarts, minutes_ago=9.5, reason="Unknown", code=255, ns="kube-system"):
    return pod(name, ns=ns, node=NODE, statuses=[{
        "name": name.split("-")[0], "ready": True, "restartCount": restarts,
        "state": {"running": {"startedAt": ts(minutes_ago=minutes_ago - 0.2)}},
        "lastState": {"terminated": {"exitCode": code, "reason": reason, "finishedAt": ts(minutes_ago=minutes_ago)}}}])


def laptop_after_docker_restart():
    from test_features import make

    system = [back_up("coredns-a", 11), back_up("coredns-b", 11), back_up("kindnet-x", 14),
              back_up("kube-controller-manager", 21), back_up("kube-proxy-y", 11), back_up("kube-scheduler", 22),
              back_up("local-path-provisioner-z", 22, minutes_ago=8.7, reason="Error", code=1, ns="local-path-storage")]
    broken = pod("imagepull-test-1", ns="default", node=NODE, statuses=[{
        "name": "app", "ready": False, "restartCount": 0,
        "state": {"waiting": {"reason": "ImagePullBackOff", "message": "tag does not exist"}}}])
    return make(pods=[*system, broken], nodes=[{"kind": "Node", "metadata": {"name": NODE}}])


def test_a_docker_restart_is_one_note_not_seven_crash_loops(settings):
    findings = run_checks(laptop_after_docker_restart(), settings)
    checks = {(f.check, f.resource.name) for f in findings}
    assert not [c for c in checks if c[0] in ("pod-crashloop", "pod-restarting")], checks
    restarted = [f for f in findings if f.check == "node-restarted"]
    assert len(restarted) == 1 and "6 containers" in restarted[0].message
    assert restarted[0].severity.value == "low"
    assert ("pod-image-pull", "imagepull-test-1") in checks  # the real problem is still found


def test_real_crash_loops_are_still_caught(settings):

    crashing = pod("api-1", node=NODE, statuses=[{
        "name": "app", "ready": False, "restartCount": 6,
        "state": {"waiting": {"reason": "CrashLoopBackOff"}},
        "lastState": {"terminated": {"exitCode": 1, "reason": "Error", "finishedAt": ts(minutes_ago=1)}}}])
    snap = laptop_after_docker_restart()
    snap.objects["pods"].append(crashing)
    assert ("pod-crashloop", "api-1") in {(f.check, f.resource.name) for f in run_checks(snap, settings)}


def test_one_container_stopping_with_unknown_is_not_a_node_restart(settings):
    from test_features import make

    snap = make(pods=[back_up("worker-1", 4, minutes_ago=2, ns="shop")], nodes=[{"kind": "Node", "metadata": {"name": NODE}}])
    found = {f.check for f in run_checks(snap, settings)}
    assert "node-restarted" not in found and "pod-crashloop" in found


def test_a_node_restart_does_not_take_the_apps_problem_with_it(settings):
    """On the Mac, the bad image tag showed up as "Node/kubernetes-cluster-control-plane: Image cannot be pulled"."""
    from kubesift.analysis.correlate import build_incidents

    snap = laptop_after_docker_restart()
    incidents = build_incidents(snap, run_checks(snap, settings))
    image = next(i for i in incidents if any(f.check == "pod-image-pull" for f in i.findings))
    assert image.root.kind != "Node" and image.layer != "infra", image.title
    assert not any(f.check == "node-restarted" for f in image.findings)
    note = next(i for i in incidents if any(f.check == "node-restarted" for f in i.findings))
    assert [f.check for f in note.findings] == ["node-restarted"] and note.severity.value == "low"
    assert incidents[0] is image  # the real problem comes first


def test_a_broken_node_still_explains_its_pods(settings):
    from kubesift.analysis.correlate import build_incidents

    snap = laptop_after_docker_restart()
    snap.objects["nodes"][0]["status"] = {"conditions": [{"type": "Ready", "status": "False", "reason": "KubeletNotReady",
                                                          "lastTransitionTime": "2026-01-01T00:00:00Z"}]}
    incidents = build_incidents(snap, run_checks(snap, settings))
    node = [i for i in incidents if i.root.kind == "Node" and i.layer == "infra"]
    assert node and any(f.check == "pod-image-pull" for f in node[0].findings), [i.title for i in incidents]
