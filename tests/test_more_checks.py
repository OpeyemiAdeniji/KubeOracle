"""Smaller targeted snapshots for workload, storage, notification and runbook behaviour."""

import json
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer

from fixtures import container, deployment, event, m, pod, ts

from kubeoracle.checks import run_checks
from kubeoracle.cluster.snapshot import Snapshot


def make(**objects) -> Snapshot:
    snap = Snapshot(context="t", live=True, server_version="v1.35.0")
    snap.objects = defaultdict(list, {k: list(v) for k, v in objects.items()})
    snap.readable = set(objects) | {"storageclasses", "secrets", "configmaps", "endpointslices"}
    return snap


def checks(snap, settings):
    return {f.check: f for f in run_checks(snap, settings)}


def test_workload_controllers(settings):
    d = deployment("api", 3, 1, {"app": "api"}, [container()])
    d["status"]["conditions"].append({"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded",
                                      "message": "ReplicaSet api-1 has timed out progressing."})
    job = {"kind": "Job", "metadata": m("backup-1", ownerReferences=[{"kind": "CronJob", "name": "backup"}]),
           "spec": {"backoffLimit": 2},
           "status": {"failed": 3, "conditions": [{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded",
                                                   "message": "Job has reached the specified backoff limit",
                                                   "lastTransitionTime": ts(minutes_ago=30)}]}}
    cron = {"kind": "CronJob", "metadata": m("backup"), "spec": {"schedule": "0 * * * *"},
            "status": {"lastScheduleTime": ts(minutes_ago=31)}}
    hpa = {"kind": "HorizontalPodAutoscaler", "metadata": m("api"),
           "spec": {"maxReplicas": 3, "scaleTargetRef": {"kind": "Deployment", "name": "api"}},
           "status": {"currentReplicas": 3, "desiredReplicas": 3, "conditions": [
               {"type": "ScalingActive", "status": "False", "reason": "FailedGetResourceMetric",
                "message": "failed to get cpu utilization: missing request for cpu"},
               {"type": "ScalingLimited", "status": "True", "reason": "TooManyReplicas", "message": "at max"}]}}
    sts = {"kind": "StatefulSet", "metadata": m("db"), "spec": {"replicas": 3},
           "status": {"readyReplicas": 2, "currentRevision": "db-1", "updateRevision": "db-2"}}
    ds = {"kind": "DaemonSet", "metadata": m("node-agent", "kube-system"),
          "status": {"desiredNumberScheduled": 3, "numberReady": 2, "numberUnavailable": 1}}
    found = checks(make(deployments=[d], jobs=[job], cronjobs=[cron], hpas=[hpa], statefulsets=[sts],
                        daemonsets=[ds]), settings)
    assert "deployment-rollout-stuck" in found and "rollout undo" in found["deployment-rollout-stuck"].commands[0]
    assert "CronJob backup" in found["job-failed"].message
    assert "cronjob-failing" in found
    assert "no CPU/memory request" in found["hpa-cannot-scale"].remediation
    assert "hpa-maxed-out" in found
    assert "during an update" in found["statefulset-unready"].message
    assert found["daemonset-unavailable"].severity.value == "critical"


def test_storage(settings):
    pvc = {"kind": "PersistentVolumeClaim", "metadata": m("data", created=ts(minutes_ago=10)),
           "spec": {"storageClassName": "fast-ssd"}, "status": {"phase": "Pending"}}
    sc = {"kind": "StorageClass", "metadata": m("standard", None,
                                                annotations={"storageclass.kubernetes.io/is-default-class": "true"})}
    p = pod("db-0", phase="Pending", statuses=[], conditions=[])
    p["status"]["containerStatuses"] = []
    ev = event("Pod", "db-0", "FailedAttachVolume",
               'Multi-Attach error for volume "pvc-1" Volume is already exclusively attached to one node')
    found = checks(make(pvcs=[pvc], storageclasses=[sc], pods=[p], events=[ev]), settings)
    assert "StorageClass 'fast-ssd' does not exist" in found["pvc-pending"].message
    assert "Recreate" in found["volume-mount-failed"].remediation


def test_platform_events(settings):
    evs = [event("ReplicaSet", "web-1", "FailedCreate", 'pods "web-1-x" is forbidden: violates PodSecurity "restricted:latest"'),
           event("Pod", "web-2", "FailedCreatePodSandBox", "failed to setup network for sandbox: failed to assign an IP address to container")]
    found = [f for f in run_checks(make(events=evs), settings) if f.check == "platform-warning"]
    titles = {f.title for f in found}
    assert {"Pod Security Admission rejects pods", "Pod IP addresses exhausted"} <= titles


def test_notify_slack(settings):
    from fixtures import broken_cluster

    from kubeoracle import notify
    from kubeoracle.analysis.engine import investigate

    got = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.slack_webhook_url = f"http://127.0.0.1:{srv.server_port}/hook"
    settings.teams_webhook_url = f"http://127.0.0.1:{srv.server_port}/teams"
    report = investigate(settings, broken_cluster(), use_llm=False)
    sent = notify.send(settings, report, [f for f in report.findings if f.kind == "problem"])
    srv.shutdown()
    assert sent == ["slack", "teams"]
    assert got[0]["blocks"][0]["type"] == "header"
    assert got[1]["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"


def test_runbooks(tmp_path, settings):
    from fixtures import broken_cluster

    from kubeoracle.analysis.engine import investigate

    (tmp_path / "rb.yaml").write_text(
        "- title: API OOM playbook\n  match: {check: pod-oomkilled, namespace: shop, name: '^api$'}\n"
        "  url: https://wiki.example.com/oom\n  steps: [Check heap dashboard]\n", encoding="utf-8")
    settings.runbooks_dir = str(tmp_path)
    report = investigate(settings, broken_cluster(), use_llm=False)
    oom = next(f for f in report.findings if f.check == "pod-oomkilled")
    assert any("API OOM playbook" in n and "wiki.example.com" in n for n in oom.notes)
    crash = next(f for f in report.findings if f.check == "pod-crashloop")
    assert not any("playbook" in n for n in crash.notes)


def test_crashloop_between_backoffs(settings):
    """kubectl shows Error or Running between restarts; it is still a crash loop."""
    error_now = pod("a", statuses=[{"name": "app", "ready": False, "restartCount": 2,
                                    "state": {"terminated": {"exitCode": 1, "reason": "Error", "finishedAt": ts(minutes_ago=0.1)}},
                                    "lastState": {"terminated": {"exitCode": 1, "reason": "Error", "finishedAt": ts(minutes_ago=1)}}}])
    running_again = pod("b", statuses=[{"name": "app", "ready": True, "restartCount": 4,
                                        "state": {"running": {"startedAt": ts(minutes_ago=0.05)}},
                                        "lastState": {"terminated": {"exitCode": 1, "reason": "Error", "finishedAt": ts(minutes_ago=0.1)}}}])
    stable = pod("c", statuses=[{"name": "app", "ready": True, "restartCount": 4,
                                 "state": {"running": {"startedAt": ts(days_ago=1)}},
                                 "lastState": {"terminated": {"exitCode": 1, "reason": "Error", "finishedAt": ts(days_ago=1)}}}])
    found = [f for f in run_checks(make(pods=[error_now, running_again, stable]), settings) if f.check == "pod-crashloop"]
    assert {f.resource.name for f in found} == {"a", "b"}
