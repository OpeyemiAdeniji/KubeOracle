"""New checks from real-world incidents, preflight, time travel, postmortems, Slack, drills, knowledge packs."""

import copy
import hashlib
import hmac
import json
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml
from fixtures import broken_cluster, container, deployment, event, m, pod, ts
from typer.testing import CliRunner

from kubesift.checks import run_checks
from kubesift.cluster.snapshot import Snapshot


def make(**objects) -> Snapshot:
    snap = Snapshot(context="t", live=True, server_version="v1.35.0")
    snap.objects = defaultdict(list, {k: list(v) for k, v in objects.items()})
    snap.readable = set(objects) | {"storageclasses", "secrets", "configmaps", "endpointslices", "namespaces",
                                    "networkpolicies"}
    return snap


def checks(snap, settings):
    return {f.check: f for f in run_checks(snap, settings)}


# ------------------------------------------------------------------ new checks
def test_rollout_drops_traffic(settings):
    from kubesift.lint import lint_text
    manifests = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: web}
spec:
  replicas: 2
  selector: {matchLabels: {app: web}}
  template:
    metadata: {labels: {app: web}}
    spec:
      containers: [{name: web, image: nginx:1.27, ports: [{containerPort: 80}]}]
---
apiVersion: v1
kind: Service
metadata: {name: web}
spec: {selector: {app: web}, ports: [{port: 80}]}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: api}
spec:
  replicas: 2
  selector: {matchLabels: {app: api}}
  template:
    metadata: {labels: {app: api}}
    spec:
      terminationGracePeriodSeconds: 5
      containers:
      - name: api
        image: nginx:1.27
        ports: [{containerPort: 80}]
        lifecycle: {preStop: {exec: {command: [sh, -c, sleep 10]}}}
---
apiVersion: v1
kind: Service
metadata: {name: api}
spec: {selector: {app: api}, ports: [{port: 80}]}
"""
    found = [f for f in lint_text(manifests, settings) if f.check == "rollout-drops-traffic"]
    assert {f.resource.name for f in found} == {"web", "api"}
    assert any(f.severity.value == "high" and "terminationGracePeriodSeconds" in f.message for f in found)


def test_operational_checks(settings):
    cm = {"kind": "ConfigMap", "metadata": m("flags", managedFields=[
        {"manager": "kubectl-edit", "operation": "Update", "time": ts(minutes_ago=5)}]), "keys": ["X"]}
    p = pod("app-1", containers=[container(envFrom=[{"configMapRef": {"name": "flags"}}])])
    p["status"]["startTime"] = ts(minutes_ago=60)
    ns_term = {"kind": "Namespace", "metadata": m("old", None, deletionTimestamp=ts(minutes_ago=30)),
               "status": {"phase": "Terminating", "conditions": [
                   {"type": "NamespaceDeletionDiscoveryFailure", "status": "True",
                    "message": "Discovery failed for some groups: metrics.k8s.io/v1beta1: stale GroupVersion"}]}}
    apisvc = {"kind": "APIService", "metadata": m("v1beta1.metrics.k8s.io", None),
              "spec": {"service": {"namespace": "kube-system", "name": "metrics-server"}},
              "status": {"conditions": [{"type": "Available", "status": "False", "reason": "MissingEndpoints",
                                         "message": "endpoints for service/metrics-server have no addresses"}]}}
    coredns = deployment("coredns", 1, 1, {"k8s-app": "kube-dns"},
                         [container(resources={"limits": {"cpu": "100m", "memory": "170Mi"}})], ns="kube-system")
    nodes = [{"kind": "Node", "metadata": m(f"n{i}", None, {"topology.kubernetes.io/zone": f"z{i}"}),
              "status": {"conditions": [{"type": "Ready", "status": "True"}]}} for i in (1, 2)]
    web = [pod(f"web-{i}", "web-rs", {"app": "web"}, node="n1") for i in (1, 2)]
    rs = {"kind": "ReplicaSet", "metadata": m("web-rs", ownerReferences=[{"kind": "Deployment", "name": "web", "controller": True}])}
    dep = deployment("web", 2, 2, {"app": "web"}, [container()])
    hpa_events = [event("HorizontalPodAutoscaler", "web", "SuccessfulRescale",
                        "New size: 4; reason: cpu resource utilization (percentage of request) above target", minutes_ago=10),
                  event("HorizontalPodAutoscaler", "web", "SuccessfulRescale", "New size: 2; reason: All metrics below target",
                        minutes_ago=5)]
    for e in hpa_events:
        e["count"] = 4
    nc = {"kind": "NodeClaim", "metadata": m("default-abc", None, created=ts(minutes_ago=20)),
          "status": {"conditions": [{"type": "Launched", "status": "False", "reason": "LaunchFailed",
                                     "message": "InsufficientInstanceCapacity: no capacity in us-east-1a"}]}}
    pvc = {"kind": "PersistentVolumeClaim", "metadata": m("data", deletionTimestamp=ts(minutes_ago=30),
                                                         finalizers=["kubernetes.io/pvc-protection"]),
           "status": {"phase": "Bound"}}
    user = pod("db-0", containers=[container()], volumes=[{"name": "d", "persistentVolumeClaim": {"claimName": "data"}}])
    cron_ev = event("CronJob", "report", "FailedNeedsStart", "Cannot determine if job needs to be started: too many "
                    "missed start times (> 100). Set or decrease .spec.startingDeadlineSeconds")
    snap = make(configmaps=[cm], pods=[p, *web, user], namespaces=[ns_term, {"kind": "Namespace", "metadata": m("shop", None)}],
                apiservices=[apisvc], deployments=[coredns, dep], nodes=nodes, replicasets=[rs],
                events=[*hpa_events, cron_ev], nodeclaims=[nc], pvcs=[pvc], hpas=[], networkpolicies=[])
    found = checks(snap, settings)
    assert "after this pod started" in found["config-stale-in-pod"].message
    assert "APIService" in found["namespace-stuck-terminating"].remediation
    assert "HPAs" in found["apiservice-unavailable"].message
    assert "dns-fragile" in found
    assert "All 2 replicas run on node n1" in found["replicas-single-node"].message
    assert "hpa-flapping" in found
    assert "no capacity" in found["karpenter-nodeclaim-failed"].remediation.lower() or \
        "capacity" in found["karpenter-nodeclaim-failed"].remediation
    assert "db-0" in found["pvc-stuck-terminating"].message
    assert "security-pss-not-enforced" in found and "security-no-network-policy" in found
    assert any(f.title == "CronJob stopped scheduling" for f in run_checks(snap, settings) if f.check == "platform-warning")


def test_rbac_and_mesh_hints(settings):
    p = pod("worker", statuses=[{"name": "app", "ready": False, "restartCount": 3,
                                 "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                                 "lastState": {"terminated": {"exitCode": 1, "finishedAt": ts(minutes_ago=1)}}}])
    snap = make(pods=[p])
    snap.logs["shop/worker/app"] = {"previous": True, "errors": [
        'pods is forbidden: User "system:serviceaccount:shop:default" cannot list resource "pods"'], "tail": []}
    assert "ServiceAccount is not allowed" in checks(snap, settings)["pod-crashloop"].message
    mesh = pod("api", statuses=[{"name": "app", "ready": True, "state": {"running": {}}},
                                {"name": "istio-proxy", "ready": False, "state": {"running": {"startedAt": ts(minutes_ago=40)}}}],
               conditions=[{"type": "Ready", "status": "False", "lastTransitionTime": ts(minutes_ago=30)}])
    f = checks(make(pods=[mesh]), settings)["pod-not-ready"]
    assert "sidecar" in f.evidence[0]


# ------------------------------------------------------------------ preflight
DEPLOY = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: checkout, namespace: shop}
spec:
  replicas: 2
  selector: {matchLabels: {app: checkout}}
  template:
    metadata: {labels: {app: checkout}}
    spec:
      serviceAccountName: checkout-sa
      containers:
      - name: app
        image: ghcr.io/acme/checkout:1.6
        resources: {requests: {cpu: 100m, memory: 64Mi}, limits: {memory: 128Mi}}
        env:
        - name: STRIPE_KEY
          valueFrom: {secretKeyRef: {name: stripe, key: key}}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: batch, namespace: jobs}
spec:
  replicas: 1
  selector: {matchLabels: {app: batch}}
  template:
    metadata: {labels: {app: batch}}
    spec:
      containers: [{name: b, image: ghcr.io/acme/batch:1, resources: {requests: {cpu: "64", memory: 1Ti}}}]
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: data, namespace: shop}
spec: {storageClassName: ultra-ssd, accessModes: [ReadWriteOnce], resources: {requests: {storage: 1Gi}}}
"""


def test_preflight_catches_live_problems(settings, monkeypatch):
    from kubesift import preflight as pf
    from kubesift.lint import _docs_from_text

    live = broken_cluster()
    live.objects["storageclasses"] = [{"kind": "StorageClass", "metadata": m("standard", None)}]
    live.objects["serviceaccounts"] = []
    live.readable |= {"serviceaccounts"}
    # the running checkout uses DATABASE_URL and ~300Mi of memory
    live.get("deployments", "shop", "checkout")["spec"]["template"]["spec"]["containers"][0]["env"] = [
        {"name": "DATABASE_URL", "value": "x"}]
    live.metrics["pods"]["shop/checkout-7c9a-x1/app"] = {"cpu": 0.1, "memory": 300 * 2**20}
    monkeypatch.setattr(pf, "image_exists", lambda img: "batch" not in img)

    res = pf.preflight(_docs_from_text(DEPLOY, "change.yaml"), live, settings, check_images=True)
    found = {f.check: f for f in res.findings}
    assert "Secret 'stripe'" in found["preflight-dependency-missing"].message
    assert any("checkout-sa" in e for e in found["preflight-dependency-missing"].evidence)
    assert "Namespace 'jobs' does not exist" in found["preflight-namespace-missing"].message
    assert "preflight-will-not-fit" in found
    assert "ultra-ssd" in found["preflight-storageclass-missing"].message
    assert "OOMKilled" in found["preflight-will-oom"].message
    assert "DATABASE_URL" in found["preflight-risky-change"].message
    assert "preflight-image-missing" in found
    assert any(c["resource"] == "Deployment/shop/checkout" and c["action"] == "update" for c in res.changes)
    assert found["preflight-dependency-missing"].source == "change.yaml#0"


def test_preflight_server_dry_run(settings):
    from kubesift.cluster.client import KubeClient
    from kubesift.lint import _docs_from_text
    from kubesift.preflight import preflight

    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_PATCH(self):
            seen.append((self.path, self.headers.get("Content-Type")))
            body = json.dumps({"message": 'admission webhook "policy.example.com" denied the request: '
                                          "images must come from ghcr.io/acme"}).encode()
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    kc = {"apiVersion": "v1", "kind": "Config", "current-context": "x",
          "clusters": [{"name": "x", "cluster": {"server": f"http://127.0.0.1:{srv.server_port}"}}],
          "users": [{"name": "u", "user": {"token": "t"}}], "contexts": [{"name": "x", "context": {"cluster": "x", "user": "u"}}]}
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(kc, f)
    client = KubeClient(kubeconfig=f.name)
    res = preflight(_docs_from_text(DEPLOY.split("---")[1], "x"), broken_cluster(), settings,
                    client=client, server_dry_run=True)
    srv.shutdown()
    assert seen and "dryRun=All" in seen[0][0] and seen[0][1] == "application/apply-patch+yaml"
    assert "denied the request" in next(f for f in res.findings if f.check == "preflight-rejected").message


# ------------------------------------------------------------------ time travel
def test_time_travel_diff(settings):
    from kubesift.store import Store
    from kubesift.timetravel import diff, digest

    before = broken_cluster()
    after = copy.deepcopy(before)
    after.get("deployments", "shop", "api")["spec"]["template"]["spec"]["containers"][0]["image"] = "ghcr.io/acme/api:3.3"
    after.get("nodes", None, "node-2")["status"]["conditions"][0]["status"] = "True"
    after.objects["configmaps"][0]["keys"] = ["FEATURE_X", "FEATURE_Y"]
    del after._idx
    changes = {c["resource"]: c for c in diff(digest(before), digest(after))}
    assert any("image ghcr.io/acme/api:3.2 -> ghcr.io/acme/api:3.3" in d for d in changes["Deployment/shop/api"]["details"])
    assert any("ready" in d for d in changes["Node/node-2"]["details"])
    assert any("+FEATURE_Y" in d for d in changes["ConfigMap/shop/checkout-flags"]["details"])

    store = Store(settings.data_dir)
    d1 = digest(before)
    assert store.save_digest(d1) and not store.save_digest(d1)  # unchanged digests are not stored twice
    from datetime import timedelta

    from kubesift.util import now
    assert store.digest_before("test-cluster", now() + timedelta(minutes=1))["cluster"] == "test-cluster"
    assert store.digest_before("test-cluster", now() - timedelta(days=1)) is None


# ------------------------------------------------------------------ postmortem
def test_postmortem(settings):
    from kubesift.analysis.engine import investigate
    from kubesift.postmortem import build, to_markdown

    snap = broken_cluster()
    report = investigate(settings, snap, use_llm=False)
    pm = build(report, snap, [{"created": ts(minutes_ago=1), "action": "rollback", "target": "deployment/shop/checkout",
                               "dry_run": 0, "result": "applied", "actor": "slack:U1"}])
    assert pm["trigger"] and "checkout" in pm["trigger"]["summary"]
    assert pm["status"] == "resolved"
    assert any(e["type"] == "change" for e in pm["timeline"]) and any(e["type"] == "action" for e in pm["timeline"])
    md = to_markdown(pm)
    for section in ("## Summary", "## Impact", "## Root cause", "## Timeline", "## Action items"):
        assert section in md
    assert "kubesift preflight" in md


# ------------------------------------------------------------------ Slack
def _signed(secret: str, body: bytes, ts_: int | None = None) -> dict[str, str]:
    ts_ = ts_ or int(time.time())
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts_}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": str(ts_), "X-Slack-Signature": sig,
            "Content-Type": "application/x-www-form-urlencoded"}


def test_slack_verify():
    from kubesift.slack import verify
    body = b"text=diagnose"
    h = _signed("s3", body)
    assert verify("s3", h["X-Slack-Request-Timestamp"], body, h["X-Slack-Signature"])
    assert not verify("other", h["X-Slack-Request-Timestamp"], body, h["X-Slack-Signature"])
    old = _signed("s3", body, int(time.time()) - 3600)
    assert not verify("s3", old["X-Slack-Request-Timestamp"], body, old["X-Slack-Signature"])  # replay


def test_slack_endpoints(settings, snap, monkeypatch):
    from urllib.parse import urlencode

    from fastapi.testclient import TestClient

    from kubesift.server import app as server_app

    posted = []
    settings.slack_signing_secret = "sec"
    monkeypatch.setattr(server_app, "snapshot_for", lambda *a, **k: snap)
    monkeypatch.setattr(server_app, "_slack_post", lambda url, blocks, text: posted.append((url, blocks, text)))
    client = TestClient(server_app.create_app(settings))

    body = urlencode({"text": "diagnose shop", "response_url": "https://hooks.slack.com/x", "user_id": "U1"}).encode()
    assert client.post("/api/v1/slack/commands", content=body, headers={"Content-Type": "application/x-www-form-urlencoded",
                                                                          "X-Slack-Signature": "v0=bad",
                                                                          "X-Slack-Request-Timestamp": str(int(time.time()))}).status_code == 401
    r = client.post("/api/v1/slack/commands", content=body, headers=_signed("sec", body))
    assert r.status_code == 200 and "Working on" in r.json()["text"]
    assert posted and posted[0][1][0]["type"] == "header"
    buttons = [b["accessory"] for b in posted[0][1] if b.get("accessory")]
    assert buttons and json.loads(buttons[0]["value"])["dry_run"] is True

    help_body = urlencode({"text": "", "response_url": "https://hooks.slack.com/x"}).encode()
    assert "/sift diagnose" in client.post("/api/v1/slack/commands", content=help_body,
                                           headers=_signed("sec", help_body)).json()["text"]


# ------------------------------------------------------------------ drills & knowledge
def test_drills_catalog_loads():
    from kubesift.drills import DRILLS, _docs
    for d in DRILLS.values():
        docs = [doc for f in d.files for doc in _docs(f)]
        assert docs and all("namespace" not in (doc.get("metadata") or {}) for doc in docs)


def test_drill_refuses_foreign_namespace():
    from kubesift.cluster.client import KubeError
    from kubesift.drills import ensure_namespace

    class FakeClient:
        def get(self, path):
            return {"metadata": {"name": "prod", "labels": {}}}

        def request(self, *a, **k):
            raise AssertionError("must not write")

    with pytest.raises(ValueError):
        ensure_namespace(FakeClient(), "prod")

    class Missing(FakeClient):
        created = []

        def get(self, path):
            raise KubeError(404, "not found")

        def request(self, method, path, body=None, **k):
            self.created.append(body)

    c = Missing()
    ensure_namespace(c, "kubesift-drill")
    assert c.created[0]["metadata"]["labels"]["kubesift.dev/drill"] == "true"


def test_knowledge_roundtrip(settings, tmp_path):
    from kubesift.knowledge import export_pack, import_pack
    from kubesift.store import Store

    a = Store(str(tmp_path / "a"))
    a.add_note("pod-oomkilled", "JVM: set -XX:MaxRAMPercentage=75", "Deployment/shop/api", "ops")
    pack = export_pack(a)
    b = Store(str(tmp_path / "b"))
    assert import_pack(b, pack)["notes_added"] == 1
    assert import_pack(b, pack)["notes_added"] == 0  # duplicates skipped
    with pytest.raises(ValueError):
        import_pack(b, {"notes": []})


# ------------------------------------------------------------------ CLI
runner = CliRunner()


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KUBESIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KUBESIFT_LLM_PROVIDER", "none")
    monkeypatch.setenv("KUBESIFT_CONFIG", str(tmp_path / "none.yaml"))


def test_cli_new_commands(cli_env, snapshot_file, tmp_path):
    from kubesift.cli import app

    change = tmp_path / "change.yaml"
    change.write_text(DEPLOY, encoding="utf-8")
    r = runner.invoke(app, ["preflight", str(change), "--from", str(snapshot_file), "-o", "markdown"])
    assert r.exit_code == 2, r.output
    assert "KubeSift preflight" in r.output and "blocker" in r.output

    later = json.loads(snapshot_file.read_text(encoding="utf-8"))
    later["objects"]["deployments"][1]["spec"]["template"]["spec"]["containers"][0]["image"] = "ghcr.io/acme/api:9"
    later_file = tmp_path / "later.json"
    later_file.write_text(json.dumps(later), encoding="utf-8")
    r = runner.invoke(app, ["diff", "--from", str(snapshot_file), "--to", str(later_file), "-o", "json"])
    assert r.exit_code == 0, r.output
    assert any("api:9" in d for c in json.loads(r.output)["changes"] for d in c["details"])

    r = runner.invoke(app, ["postmortem", "--from", str(snapshot_file), "--no-llm"])
    assert r.exit_code == 0 and "## Timeline" in r.output
    assert runner.invoke(app, ["drill", "list"]).exit_code == 0

    runner.invoke(app, ["note", "add", "pod-oomkilled", "raise heap"])
    pack = tmp_path / "pack.yaml"
    assert runner.invoke(app, ["knowledge", "export", "-o", str(pack)]).exit_code == 0
    assert "raise heap" in pack.read_text(encoding="utf-8")
