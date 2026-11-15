"""Run the real collection path against a small fake Kubernetes API server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
            from kubeoracle.cluster.snapshot import KINDS
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
    from kubeoracle.analysis.engine import investigate, snapshot_for
    from kubeoracle.cluster.client import list_contexts

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
    from kubeoracle.cluster.client import KubeClient
    from kubeoracle.remediate import execute, plan
    from kubeoracle.store import Store

    kubeconfig, seen = fake_api
    client = KubeClient(kubeconfig=kubeconfig)
    store = Store(settings.data_dir)
    p = plan(client, "restart", "deployment", "shop", "checkout")
    assert execute(client, p, dry_run=True, store=store) == "dry run succeeded"
    patch = [s for s in seen if s[0] == "PATCH"]
    assert patch and "dryRun=All" in patch[0][1] and patch[0][2] == "application/strategic-merge-patch+json"
    assert store.list_audit()[0]["action"] == "restart"
