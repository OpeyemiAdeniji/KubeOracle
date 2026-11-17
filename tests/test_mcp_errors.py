"""MCP tools must tell the assistant why a call failed, not just "Error executing tool ..."."""

import asyncio
import json
from datetime import timedelta

import pytest
from fixtures import container, rs

from kubesift.analysis import engine
from kubesift.analysis.changes import detect_changes
from kubesift.cluster.client import ClusterAccessError


def _call(server, tool, args):
    from mcp import Client

    async def go():
        async with Client(server) as c:
            r = await c.call_tool(tool, args)
            return r.is_error, "".join(getattr(x, "text", "") for x in r.content)
    return asyncio.run(go())


@pytest.fixture
def server(settings, snap, monkeypatch):
    from kubesift.mcp_server import build_server

    monkeypatch.setattr(engine, "snapshot_for", lambda *a, **k: snap)
    return build_server(settings)


def test_answers_are_not_errors(server):
    err, text = _call(server, "diagnose", {"namespace": "shop"})
    assert not err and json.loads(text)["incidents"]


@pytest.mark.parametrize("tool,args,words", [
    ("explain_resource", {"kind": "deployment", "name": "nope", "namespace": "shop"}, "deployment/nope not found"),
    ("recent_changes", {"namespace": "shop", "since": "yesterday"}, "invalid duration"),
    ("upgrade_check", {"target_version": "latest"}, "invalid target version"),
    ("cluster_diff", {"since": "1h"}, "no stored history"),
    ("lint_manifests", {"manifests_yaml": "a: [unclosed"}, "invalid YAML"),
])
def test_expected_failures_are_errors_with_the_reason(server, tool, args, words):
    err, text = _call(server, tool, args)
    assert err, text
    assert words in text


def test_unreachable_cluster_says_why(settings, monkeypatch):
    from kubesift.mcp_server import build_server

    def down(*a, **k):
        raise ClusterAccessError("cannot reach the cluster at https://10.0.0.1:6443: connection refused")
    monkeypatch.setattr(engine, "snapshot_for", down)
    err, text = _call(build_server(settings), "diagnose", {})
    assert err and "connection refused" in text


def test_crashes_still_hide_their_text(settings, monkeypatch):
    from kubesift.mcp_server import build_server

    def boom(*a, **k):
        raise RuntimeError("internal detail /home/user/secret-path")
    monkeypatch.setattr(engine, "snapshot_for", boom)
    err, text = _call(build_server(settings), "diagnose", {})
    assert err and "secret-path" not in text


def _single_rs_snapshot(revision):
    from test_features import make

    labels = {"app": "web"}
    return make(replicasets=[rs("web-1", "web", revision, [container()], 10, labels)])


def test_new_deployment_reads_as_created():
    c = next(c for c in detect_changes(_single_rs_snapshot(1), timedelta(hours=1)) if c.kind == "rollout")
    assert c.summary == "Deployment web was created" and c.rollback_command is None


def test_cleaned_up_history_is_not_called_new():
    c = next(c for c in detect_changes(_single_rs_snapshot(4), timedelta(hours=1)) if c.kind == "rollout")
    assert c.summary == "Deployment web rolled out revision 4" and "not kept" in c.details[0]
