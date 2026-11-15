"""CLI, REST API, MCP server, cost and upgrade reports."""

import json

import pytest
from typer.testing import CliRunner

from kubesift.cli import app
from kubesift.cost import cost_report
from kubesift.upgrade import upgrade_report

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("KUBESIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KUBESIFT_LLM_PROVIDER", "none")
    monkeypatch.setenv("KUBESIFT_CONFIG", str(tmp_path / "none.yaml"))


def test_cli_diagnose_json(snapshot_file):
    result = runner.invoke(app, ["diagnose", "--from", str(snapshot_file), "-o", "json", "--no-llm"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["cluster"] == "test-cluster"
    assert any(f["check"] == "pod-crashloop" for f in data["findings"])


def test_cli_diagnose_text_and_fail_on(snapshot_file):
    result = runner.invoke(app, ["diagnose", "--from", str(snapshot_file), "--no-llm", "-v", "--fail-on", "critical"])
    assert result.exit_code == 2
    assert "Diagnosis" in result.output


def test_cli_markdown(snapshot_file):
    result = runner.invoke(app, ["diagnose", "--from", str(snapshot_file), "--no-llm", "-o", "markdown"])
    assert result.exit_code == 0
    assert result.output.startswith("# KubeSift report")


def test_cli_ask(snapshot_file):
    result = runner.invoke(app, ["ask", "why", "is", "checkout", "failing", "--from", str(snapshot_file), "--no-llm"])
    assert result.exit_code == 0, result.output
    assert "checkout" in result.output


def test_cli_changes_and_explain(snapshot_file):
    result = runner.invoke(app, ["changes", "--from", str(snapshot_file), "--since", "6h", "-o", "json"])
    assert result.exit_code == 0
    assert any(c["kind"] == "rollout" for c in json.loads(result.output))
    result = runner.invoke(app, ["explain", "deploy/checkout", "-n", "shop", "--from", str(snapshot_file), "-o", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["resource"]["kind"] == "Deployment" and data["findings"]


def test_cli_lint_exit_code(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text("apiVersion: v1\nkind: Pod\nmetadata: {name: p}\nspec:\n  containers:\n  - name: c\n    image: nginx\n"
                 "    securityContext: {privileged: true}\n", encoding="utf-8")
    assert runner.invoke(app, ["lint", str(f)]).exit_code == 2
    assert runner.invoke(app, ["lint", str(f), "--fail-on", "none"]).exit_code == 0
    sarif = runner.invoke(app, ["lint", str(f), "-o", "sarif", "--fail-on", "none"])
    assert json.loads(sarif.output)["version"] == "2.1.0"


def test_cli_checks_and_history(snapshot_file):
    assert runner.invoke(app, ["checks", "-o", "json"]).exit_code == 0
    runner.invoke(app, ["diagnose", "--from", str(snapshot_file), "--no-llm", "-o", "json"])
    result = runner.invoke(app, ["history", "-o", "json"])
    assert result.exit_code == 0 and json.loads(result.output)


def test_cli_upgrade_and_cost(snapshot_file):
    result = runner.invoke(app, ["upgrade-check", "--target", "1.36", "--from", str(snapshot_file), "-o", "json"])
    assert result.exit_code == 0
    assert not json.loads(result.output)["ready"]
    assert runner.invoke(app, ["cost", "--from", str(snapshot_file)]).exit_code == 0


def test_upgrade_report(snap, settings):
    data = upgrade_report(snap, settings, "1.36")
    checks = {f["check"] for f in data["blockers"] + data["warnings"]}
    assert {"api-removed", "pdb-blocks-drain", "node-containerd-1x", "ingress-nginx-retired"} <= checks
    assert any("cgroup" in n for n in data["notes"])
    assert any("1.34 -> 1.35 -> 1.36" in s for s in data["steps"])


def test_cost_report(snap, settings):
    data = cost_report(snap, settings)
    row = next(r for r in data["rightsizing"] if "web-1" in r["workload"])
    assert row["recommend_cpu"] == "10m"
    assert data["totals"]["monthly_savings"] >= 0


def test_server(snap, settings, monkeypatch):
    from fastapi.testclient import TestClient

    from kubesift.server import app as server_app

    settings.api_token = "t0ken"
    monkeypatch.setattr(server_app, "snapshot_for", lambda *a, **k: snap)
    client = TestClient(server_app.create_app(settings))
    assert client.get("/healthz").status_code == 200
    assert client.post("/api/v1/diagnose", json={}).status_code == 401
    h = {"Authorization": "Bearer t0ken"}
    r = client.post("/api/v1/diagnose", json={"llm": False}, headers=h)
    assert r.status_code == 200 and r.json()["findings"]
    assert client.get("/api/v1/history", headers=h).json()
    r = client.post("/api/v1/lint", json={"manifests": "apiVersion: v1\nkind: Pod\nmetadata: {name: p}\nspec:\n"
                                                       "  containers: [{name: c, image: nginx}]\n"}, headers=h)
    assert any(f["check"] == "image-mutable-tag" for f in r.json())
    r = client.post("/api/v1/fix", json={"action": "restart", "kind": "deployment", "namespace": "shop",
                                         "name": "checkout", "dry_run": False}, headers=h)
    assert r.status_code == 403  # writes disabled by default
    r = client.post("/api/v1/webhooks/alertmanager", headers=h, json={"alerts": [
        {"status": "firing", "labels": {"alertname": "KubePodCrashLooping", "namespace": "shop"}}]})
    assert r.json()["namespaces"] == ["shop"]
    assert "kubesift_findings" in client.get("/metrics").text
    assert client.get("/").status_code == 200


def test_mcp_tools(settings):
    import asyncio

    from kubesift.mcp_server import build_server

    server = build_server(settings)
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert {"diagnose", "explain_resource", "recent_changes", "cost_report", "upgrade_check", "lint_manifests"} <= names
    assert "apply_fix" not in names
    settings.allow_writes = True
    assert "apply_fix" in {t.name for t in asyncio.run(build_server(settings).list_tools())}
