"""Incidents become tickets: readable Markdown, one GitHub issue per incident, never a duplicate."""

import json

import httpx
import pytest

from kubesift.analysis.engine import investigate
from kubesift.tickets import TicketError, incident_key, incident_markdown, open_github_issue


@pytest.fixture
def report(settings, snap):
    return investigate(settings, snap, use_llm=False)


@pytest.fixture
def gh(settings):
    settings.github_repo, settings.github_token = "acme/platform", "test-token"
    return settings


def fake_github(existing=None, status=200):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"message": "Resource not accessible by integration"})
        if request.method == "GET":
            return httpx.Response(200, json=existing or [])
        if request.url.path.endswith("/comments"):
            return httpx.Response(201, json={"id": 1})
        body = json.loads(request.content)
        return httpx.Response(201, json={"number": 42, "html_url": "https://github.com/acme/platform/issues/42",
                                         "title": body["title"]})
    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_markdown_has_cause_evidence_fix_and_how_to_verify(report):
    inc = report.incidents[0]
    title, body = incident_markdown(report, inc)
    assert title.startswith(f"[{inc.severity.value}] ")
    for section in ("### Likely cause", "### What is wrong", "<summary>Evidence</summary>", "### Fix",
                    "kubesift verify", f"kubesift-incident: {incident_key(report, inc)}"):
        assert section in body, section


def test_the_key_is_stable_across_scans(settings, snap):
    a = investigate(settings, snap, use_llm=False)
    b = investigate(settings, snap, use_llm=False)
    assert incident_key(a, a.incidents[0]) == incident_key(b, b.incidents[0])


def test_opens_an_issue_with_labels(gh, report):
    http, calls = fake_github()
    r = open_github_issue(gh, report, report.incidents[0], client=http)
    assert r == {"url": "https://github.com/acme/platform/issues/42", "number": 42, "created": True}
    post = calls[-1]
    assert post.url.path == "/repos/acme/platform/issues" and post.headers["Authorization"] == "Bearer test-token"
    labels = json.loads(post.content)["labels"]
    assert "kubesift" in labels and any(lbl.startswith("severity: ") for lbl in labels)


def test_comments_instead_of_opening_a_duplicate(gh, report):
    inc = report.incidents[0]
    _, body = incident_markdown(report, inc)
    open_issue = {"number": 7, "html_url": "https://github.com/acme/platform/issues/7",
                  "url": "https://api.github.com/repos/acme/platform/issues/7", "body": body}
    http, calls = fake_github(existing=[open_issue])
    r = open_github_issue(gh, report, inc, client=http)
    assert r["created"] is False and r["number"] == 7
    assert calls[-1].url.path == "/repos/acme/platform/issues/7/comments"
    assert "Still happening" in json.loads(calls[-1].content)["body"]


def test_clear_errors(settings, gh, report):
    inc = report.incidents[0]
    settings.github_repo = None
    with pytest.raises(TicketError, match="github_repo"):
        open_github_issue(settings, report, inc)
    gh.github_repo, gh.github_api_url = "acme/platform", "http://ghe.local/api/v3"
    with pytest.raises(TicketError, match="https"):
        open_github_issue(gh, report, inc)
    gh.github_api_url = None
    http, _ = fake_github(status=403)
    with pytest.raises(TicketError, match="403.*issues:write"):
        open_github_issue(gh, report, inc, client=http)


def test_missing_token_is_explained(settings, report, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    settings.github_repo, settings.github_token = "acme/platform", None
    with pytest.raises(TicketError, match="token"):
        open_github_issue(settings, report, report.incidents[0])


def test_cli_prints_markdown_without_github(snapshot_file):
    from typer.testing import CliRunner

    from kubesift.cli import app

    result = CliRunner().invoke(app, ["ticket", "--from", str(snapshot_file), "--print"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("# [") and "### Fix" in result.output


def test_api_returns_ticket_text(settings, snap, monkeypatch):
    from fastapi.testclient import TestClient

    from kubesift.server import app as server_app

    monkeypatch.setattr(server_app, "snapshot_for", lambda *a, **k: snap)
    client = TestClient(server_app.create_app(settings))
    r = client.post("/api/v1/tickets", json={"incident": 1, "markdown_only": True})
    assert r.status_code == 200 and "### Likely cause" in r.json()["body"]
    assert client.post("/api/v1/tickets", json={"incident": 99}).status_code == 404
    assert client.post("/api/v1/tickets", json={"incident": 1}).status_code == 400  # GitHub not configured


def test_slack_offers_the_button_only_when_github_is_set_up(report):
    from kubesift import slack

    def has_button(blocks):
        return any(e.get("action_id") == "sift_ticket" for b in blocks for e in b.get("elements") or [])
    assert not has_button(slack.report_blocks(report, "t"))
    assert has_button(slack.report_blocks(report, "t", tickets=True))


def test_automatic_tickets_only_for_new_urgent_incidents(gh, report, monkeypatch):
    from kubesift.server import app as server_app

    opened = []
    monkeypatch.setattr("kubesift.tickets.open_github_issue", lambda s, r, inc: opened.append(inc.title))
    server_app._auto_tickets(gh, report)
    assert opened == []                                   # off by default
    gh.ticket_on_alert = True
    server_app._auto_tickets(gh, report, fresh=set())     # scan found nothing new: no comments every interval
    assert opened == []
    server_app._auto_tickets(gh, report, fresh={f.fingerprint for f in report.incidents[0].findings})
    assert opened == [report.incidents[0].title]


def test_mcp_offers_open_ticket_only_when_configured(settings, gh):
    import asyncio

    from kubesift.mcp_server import build_server

    gh.github_repo = None
    assert "open_ticket" not in {t.name for t in asyncio.run(build_server(gh).list_tools())}
    gh.github_repo = "acme/platform"
    tools = {t.name: t for t in asyncio.run(build_server(gh).list_tools())}
    assert tools["open_ticket"].annotations.read_only_hint is False
