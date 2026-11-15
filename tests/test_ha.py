"""High availability: shared storage, leader election, MCP over HTTP with authentication."""

import asyncio
import json
import os
import socket
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from kubesift.analysis.engine import investigate
from kubesift.store import Store

PG_URL = os.environ.get("KUBESIFT_TEST_DATABASE_URL")
BACKENDS = ["sqlite"] + (["postgres"] if PG_URL else [])


@pytest.fixture(params=BACKENDS)
def store(request, tmp_path):
    if request.param == "sqlite":
        return Store(str(tmp_path))
    s = Store(str(tmp_path), PG_URL)
    for table in ("reports", "notes", "audit", "seen", "claims"):
        s._exec(f"DELETE FROM {table}")
    return s


def test_store_roundtrip(store, snap, settings):
    report = investigate(settings, snap, use_llm=False)
    rid = store.save_report(report, source="test")
    assert store.get_report(rid).cluster == "test-cluster"
    assert store.list_reports()[0]["id"] == rid
    assert store.add_note("pod-oomkilled", "raised limit", "Deployment/shop/api") > 0
    assert store.list_notes("pod-oomkilled")[0]["note"] == "raised limit"
    store.audit("restart", "deployment/shop/x", True, "ok")
    assert store.list_audit()[0]["action"] == "restart"
    store._exec("UPDATE reports SET created = ? WHERE id = ?",
                ((report.collected_at - timedelta(days=60)).isoformat(), rid))
    assert store.prune(30) == 1


def test_claims_are_exclusive(store):
    assert store.claim("alert:shop", 60)
    assert not store.claim("alert:shop", 60)  # second replica / second alert is refused
    assert store.claim("alert:other", 60)
    store._exec("UPDATE claims SET until = ? WHERE key = ?", (time.time() - 1, "alert:shop"))
    assert store.claim("alert:shop", 60)  # expired claims can be taken again


def test_new_findings_shared(store, snap, settings):
    findings = investigate(settings, snap, use_llm=False).findings
    assert store.new_findings("c1", findings)
    assert not store.new_findings("c1", findings)
    assert store.new_findings("c2", findings)  # dedupe is per cluster


# ---------------------------------------------------------------- leader election
@pytest.fixture
def lease_api(tmp_path):
    state = {"lease": None, "rv": 0}
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            return json.loads(self.rfile.read(int(self.headers["Content-Length"])))

        def do_GET(self):
            with lock:
                if state["lease"] is None:
                    return self._send(404, {"message": "not found"})
                return self._send(200, state["lease"])

        def do_POST(self):
            with lock:
                if state["lease"] is not None:
                    return self._send(409, {"message": "exists"})
                body = self._body()
                state["rv"] += 1
                body["metadata"]["resourceVersion"] = str(state["rv"])
                state["lease"] = body
                return self._send(201, body)

        def do_PUT(self):
            with lock:
                body = self._body()
                if body["metadata"].get("resourceVersion") != state["lease"]["metadata"]["resourceVersion"]:
                    return self._send(409, {"message": "conflict"})
                state["rv"] += 1
                body["metadata"]["resourceVersion"] = str(state["rv"])
                state["lease"] = body
                return self._send(200, body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    kc = tmp_path / "kc"
    kc.write_text(yaml.safe_dump({
        "apiVersion": "v1", "kind": "Config", "current-context": "x",
        "clusters": [{"name": "x", "cluster": {"server": f"http://127.0.0.1:{srv.server_port}"}}],
        "users": [{"name": "u", "user": {"token": "t"}}],
        "contexts": [{"name": "x", "context": {"cluster": "x", "user": "u"}}]}))
    yield str(kc), state
    srv.shutdown()


def test_leader_election_and_failover(lease_api):
    from kubesift.cluster.client import KubeClient
    from kubesift.leader import LeaderElector

    kc, state = lease_api
    a = LeaderElector(KubeClient(kubeconfig=kc), "kubesift", identity="pod-a", lease_seconds=2)
    b = LeaderElector(KubeClient(kubeconfig=kc), "kubesift", identity="pod-b", lease_seconds=2)
    assert a.try_acquire() is True          # creates the lease
    assert b.try_acquire() is False         # a holds it
    assert a.try_acquire() is True          # renew
    assert state["lease"]["spec"]["holderIdentity"] == "pod-a"
    time.sleep(2.5)                         # a stops renewing (crashed)
    assert b.try_acquire() is True          # b takes over after expiry
    assert state["lease"]["spec"]["holderIdentity"] == "pod-b"
    assert state["lease"]["spec"]["leaseTransitions"] == 1
    assert a.try_acquire() is False
    b.is_leader = True
    b.stop()                                # graceful shutdown hands the lease back
    assert state["lease"]["spec"]["holderIdentity"] is None
    assert a.try_acquire() is True          # no waiting for expiry


# ---------------------------------------------------------------- MCP over HTTP
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_mcp_http_requires_token(settings):
    import httpx2
    import uvicorn
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from kubesift.server.app import create_app

    settings.api_token = "s3cret-token"
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        r = httpx2.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                        headers={"Accept": "application/json, text/event-stream"})
        assert r.status_code == 401
        assert httpx2.get(f"http://127.0.0.1:{port}/healthz").status_code == 200  # API routes still served

        async def call() -> list[str]:
            pod = "apiVersion: v1\nkind: Pod\nmetadata: {name: p}\nspec:\n  containers: [{name: c, image: nginx}]\n"
            async with (
                httpx2.AsyncClient(headers={"Authorization": "Bearer s3cret-token"}, timeout=30) as http,
                streamable_http_client(url, http_client=http) as (read, write, *_),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                tools = await session.list_tools()
                res = await session.call_tool("lint_manifests", {"manifests_yaml": pod})
                assert "image-mutable-tag" in res.content[0].text
                return [t.name for t in tools.tools]

        names = asyncio.run(call())
        assert "diagnose" in names
    finally:
        server.should_exit = True
