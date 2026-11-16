"""Run the real collection path against a small fake Kubernetes API server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from fixtures import broken_cluster

TOKEN = "test-token"


@pytest.fixture
def fake_api(tmp_path):
    snap = broken_cluster()
    seen: list[tuple[str, str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            seen.append(("POST", self.path, self.headers.get("Authorization", "")))
            self._send(201, {"status": {"allowed": True}})

        def do_PATCH(self):
            seen.append(("PATCH", self.path, self.headers.get("Content-Type", "")))
            self._send(200, {})

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            seen.append(("GET", self.path, self.headers.get("Authorization", "")))
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self._send(401, {"message": "unauthorized"})
            if url.path == "/version":
                return self._send(200, {"gitVersion": "v1.34.2"})
            if url.path.endswith("/log"):
                return self._send(200, b"starting\nKeyError: 'DATABASE_URL'\n", "text/plain")
            if url.path == "/api/v1/secrets":
                return self._send(403, {"message": "forbidden"})
            plural = url.path.rstrip("/").rsplit("/", 1)[-1]
            from kubesift.cluster.snapshot import KINDS
            key = next((k for k, _, _, p, _, _ in KINDS if p == plural), None)
            if key is None or key not in snap.objects:
                return self._send(404, {"message": "not found"})
            items = [dict(o, kind=None) for o in snap.objects[key]]
            # paginate pods two at a time to exercise `continue`
            if key == "pods":
                start = int(q.get("continue", ["0"])[0])
                page = items[start:start + 2]
                cont = str(start + 2) if start + 2 < len(items) else None
                return self._send(200, {"items": page, "metadata": {"continue": cont} if cont else {}})
            return self._send(200, {"items": items, "metadata": {}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host = f"http://127.0.0.1:{server.server_port}"
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text(yaml.safe_dump({
        "apiVersion": "v1", "kind": "Config", "current-context": "fake",
        "clusters": [{"name": "fake", "cluster": {"server": host}}],
        "users": [{"name": "u", "user": {"token": TOKEN}}],
        "contexts": [{"name": "fake", "context": {"cluster": "fake", "user": "u"}}],
    }))
    yield str(kubeconfig), seen
    server.shutdown()


def test_collect_against_api(fake_api, settings):
    from kubesift.analysis.engine import investigate, snapshot_for
    from kubesift.cluster.client import list_contexts

    kubeconfig, seen = fake_api
    settings.kubeconfig = kubeconfig
    snap = snapshot_for(settings, None)
    assert snap.context == "fake"
    assert snap.server_version == "v1.34.2"
    assert len(snap.all("pods")) == len(broken_cluster().all("pods"))  # all pages read
    assert all(p["kind"] == "Pod" for p in snap.all("pods"))  # kind restored on list items
    assert any("No permission to list secrets" in w for w in snap.warnings)
    assert "secrets" not in snap.readable
    assert any("continue=" in path for _, path, _ in seen)
    assert all(auth == f"Bearer {TOKEN}" for m, _, auth in seen if m == "GET")
    log = snap.logs.get("shop/checkout-7c9a-x1/app")
    assert log and any("DATABASE_URL" in line for line in log["errors"])

    report = investigate(settings, snap, use_llm=False)
    checks = {f.check for f in report.findings}
    assert "pod-crashloop" in checks
    assert "config-ref-missing" in checks or "pod-config-error" in checks
    assert "certificate-expiring" not in checks  # secrets unreadable -> skipped, not guessed
    assert list_contexts(kubeconfig)[1] == "fake"


def test_fix_dry_run_and_patch(fake_api, settings):
    from kubesift.cluster.client import KubeClient
    from kubesift.remediate import execute, plan
    from kubesift.store import Store

    kubeconfig, seen = fake_api
    client = KubeClient(kubeconfig=kubeconfig)
    store = Store(settings.data_dir)
    p = plan(client, "restart", "deployment", "shop", "checkout")
    assert execute(client, p, dry_run=True, store=store) == "dry run succeeded"
    patch = [s for s in seen if s[0] == "PATCH"]
    assert patch and "dryRun=All" in patch[0][1] and patch[0][2] == "application/strategic-merge-patch+json"
    assert store.list_audit()[0]["action"] == "restart"


def _kubeconfig(path, server, user):
    path.write_text(yaml.safe_dump({
        "apiVersion": "v1", "kind": "Config", "current-context": "c",
        "clusters": [{"name": "c", "cluster": {"server": server}}],
        "users": [{"name": "u", "user": user}],
        "contexts": [{"name": "c", "context": {"cluster": "c", "user": "u"}}],
    }))
    return str(path)


def test_rejected_login_fails_fast_instead_of_an_empty_report(fake_api, tmp_path):
    from kubesift.cluster.client import ClusterAccessError, KubeClient
    from kubesift.cluster.snapshot import collect

    kubeconfig, _ = fake_api
    server = yaml.safe_load(Path(kubeconfig).read_text())["clusters"][0]["cluster"]["server"]
    bad = _kubeconfig(tmp_path / "bad", server, {"token": "expired"})
    with pytest.raises(ClusterAccessError, match="rejected the login"):
        collect(KubeClient(kubeconfig=bad))


def test_unreachable_cluster_fails_fast(tmp_path):
    from kubesift.cluster.client import ClusterAccessError, KubeClient
    from kubesift.cluster.snapshot import collect

    down = _kubeconfig(tmp_path / "down", "http://127.0.0.1:9", {"token": "x"})
    with pytest.raises(ClusterAccessError, match="cannot reach the API server for context 'c'"):
        collect(KubeClient(kubeconfig=down, timeout=5))


def test_failing_login_helper_is_reported(tmp_path):
    import sys

    from kubesift.cluster.client import ClusterAccessError, KubeClient

    helper = {"exec": {"apiVersion": "client.authentication.k8s.io/v1beta1", "command": sys.executable,
                       "args": ["-c", "import sys; sys.stderr.write('invalid_grant: Account has been deleted'); "
                                      "sys.exit(1)"],
                       "interactiveMode": "Never"}}
    cfg = _kubeconfig(tmp_path / "exec", "https://127.0.0.1:9", helper)
    with pytest.raises(ClusterAccessError, match="login command for context 'c' failed.*Account has been deleted"):
        KubeClient(kubeconfig=cfg)


def test_kubeconfig_problems_are_explained(tmp_path, monkeypatch):
    from kubesift.cluster.client import ClusterAccessError, KubeClient

    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    with pytest.raises(ClusterAccessError, match="no kubeconfig found at .*Fix: connect a cluster first"):
        KubeClient(kubeconfig=str(tmp_path / "missing"))

    empty = tmp_path / "empty"  # what is left after deleting every context, e.g. with kubectl config delete-context
    empty.write_text(yaml.safe_dump({"apiVersion": "v1", "kind": "Config", "current-context": "",
                                     "clusters": [], "contexts": [], "users": []}))
    with pytest.raises(ClusterAccessError, match="has no clusters set up. Fix: connect a cluster first"):
        KubeClient(kubeconfig=str(empty))

    cfg = _kubeconfig(tmp_path / "one", "https://127.0.0.1:9", {"token": "x"})
    with pytest.raises(ClusterAccessError, match="context 'prod' is not in the kubeconfig.*Available: c"):
        KubeClient(kubeconfig=cfg, context="prod")

    unset = yaml.safe_load(Path(cfg).read_text())
    unset["current-context"] = ""
    Path(cfg).write_text(yaml.safe_dump(unset))
    with pytest.raises(ClusterAccessError, match="no current context is set.*Available: c"):
        KubeClient(kubeconfig=cfg)

    from kubesift.cluster.client import list_contexts

    assert list_contexts(str(empty)) == ([], None)
    items, current = list_contexts(cfg)  # current-context unset: still listed, nothing marked current
    assert [c["name"] for c in items] == ["c"] and current is None
    with pytest.raises(ClusterAccessError, match="no kubeconfig found"):
        list_contexts(str(tmp_path / "missing"))


def test_namespace_scan_reads_argo_apps_from_every_namespace(fake_api):
    """Argo CD Applications live in the argocd namespace but deploy into others."""
    from kubesift.cluster.client import KubeClient
    from kubesift.cluster.snapshot import collect

    kubeconfig, seen = fake_api
    collect(KubeClient(kubeconfig=kubeconfig), namespace="shop", with_logs=False)
    paths = [p for method, p, _ in seen if method == "GET" and "argoproj.io" in p]
    assert paths and all("/namespaces/" not in p for p in paths)
    assert any("/namespaces/shop/pods" in p for method, p, _ in seen)  # everything else stays namespace-scoped
