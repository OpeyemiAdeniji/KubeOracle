"""REST API, web dashboard, Alertmanager webhook, Prometheus metrics and background scanning."""

from __future__ import annotations

import copy
import hmac
import logging
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from kubesift import __version__, notify
from kubesift.analysis.changes import detect_changes
from kubesift.analysis.engine import ask, explain_resource, investigate, snapshot_for
from kubesift.cluster.client import KubeClient, list_contexts
from kubesift.config import Settings
from kubesift.leader import AlwaysLeader, LeaderElector, pod_namespace
from kubesift.models import Report, Severity
from kubesift.store import Store, open_store
from kubesift.util import parse_duration

log = logging.getLogger("kubesift.server")
STATIC = Path(__file__).parent / "static"


class DiagnoseRequest(BaseModel):
    namespace: str | None = None
    context: str | None = None
    llm: bool = True
    since: str = "6h"


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    namespace: str | None = None
    context: str | None = None
    llm: bool = True


class LintRequest(BaseModel):
    manifests: str = Field(max_length=2_000_000)
    target_version: str | None = None


class FixRequest(BaseModel):
    action: str
    kind: str
    namespace: str
    name: str
    replicas: int | None = None
    dry_run: bool = True


def create_app(settings: Settings) -> FastAPI:
    # MCP over HTTP is served by this same process at /mcp, behind the same bearer token.
    mcp_server = mcp_asgi = None
    try:
        from kubesift.mcp_server import build_http_app
        mcp_server, mcp_asgi = build_http_app(settings)
    except ImportError:
        log.info("MCP extra not installed; /mcp disabled")

    state: dict[str, Any] = {"last_scan": None, "last_report": None, "elector": AlwaysLeader()}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            if mcp_server is None:
                yield
            else:
                async with mcp_server.session_manager.run():
                    yield
        finally:
            state["elector"].stop()  # on SIGTERM: hand the scanner lease to another replica immediately

    app = FastAPI(title="KubeSift", version=__version__, lifespan=lifespan,
                  description="Evidence-based Kubernetes troubleshooting API.")

    @app.exception_handler(ValueError)
    async def bad_input(_: Request, exc: ValueError) -> Any:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(exc)}, status_code=400)  # e.g. since=abc, invalid names
    store = open_store(settings)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
                           allow_headers=["Authorization", "Content-Type"])

    def auth(authorization: str | None = Header(default=None)) -> None:
        if not settings.api_token:
            return
        token = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token, settings.api_token):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    # Short-lived snapshot cache: many dashboard/API calls in a row read the cluster once,
    # and concurrent requests for the same scope wait for one collection instead of each starting one.
    cache: dict[tuple, tuple[float, Any]] = {}
    locks: dict[tuple, threading.Lock] = defaultdict(threading.Lock)

    def snap(namespace: str | None, context: str | None, with_logs: bool = True):
        from kubesift.util import check_name
        try:
            check_name(namespace, "namespace")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        key = (namespace, context, with_logs)
        now_t = time.monotonic()
        for k in [k for k, v in list(cache.items()) if now_t - v[0] > settings.cache_ttl]:
            cache.pop(k, None)  # drop expired snapshots so memory doesn't grow with every namespace queried
        if len(locks) > 512:
            locks.clear()
        try:
            with locks[key]:
                hit = cache.get(key)
                if hit is None or time.monotonic() - hit[0] > settings.cache_ttl:
                    hit = (time.monotonic(), snapshot_for(settings, namespace, context, with_logs=with_logs))
                    cache[key] = hit
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"cannot reach the cluster: {exc}") from exc
        out = copy.copy(hit[1])
        out.warnings = list(hit[1].warnings)
        return out

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/v1/info", dependencies=[Depends(auth)])
    def info() -> dict[str, Any]:
        return {"version": __version__, "llm": settings.resolved_provider(), "model": settings.resolved_model(),
                "allow_writes": settings.allow_writes, "scan_interval": settings.scan_interval,
                "last_scan": state["last_scan"], "leader": state["elector"].is_leader,
                "storage": "postgresql" if store.postgres else "sqlite", "mcp": mcp_asgi is not None}

    @app.get("/api/v1/contexts", dependencies=[Depends(auth)])
    def contexts() -> dict[str, Any]:
        try:
            items, current = list_contexts(settings.kubeconfig)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"contexts": items, "current": current}

    @app.post("/api/v1/diagnose", dependencies=[Depends(auth)])
    def diagnose(req: DiagnoseRequest) -> dict[str, Any]:
        report = investigate(settings, snap(req.namespace, req.context), use_llm=req.llm,
                             window=parse_duration(req.since), store=store)
        rid = store.save_report(report, source="api", retention_days=settings.history_days)
        state["last_report"] = report
        return {"id": rid, **report.model_dump(mode="json")}

    @app.post("/api/v1/ask", dependencies=[Depends(auth)])
    def ask_question(req: AskRequest) -> dict[str, Any]:
        report, answer = ask(settings, snap(req.namespace, req.context), req.question, use_llm=req.llm, store=store)
        rid = store.save_report(report, source="api-ask", retention_days=settings.history_days)
        return {"id": rid, "answer": answer, "report": report.model_dump(mode="json")}

    @app.get("/api/v1/changes", dependencies=[Depends(auth)])
    def changes(namespace: str | None = None, since: str = "24h", context: str | None = None) -> list[dict[str, Any]]:
        s = snap(namespace, context, with_logs=False)
        return [c.model_dump(mode="json") for c in detect_changes(s, parse_duration(since))]

    @app.get("/api/v1/explain/{kind}/{name}", dependencies=[Depends(auth)])
    def explain(kind: str, name: str, namespace: str | None = None, context: str | None = None) -> dict[str, Any]:
        return explain_resource(settings, snap(namespace, context), kind, namespace, name)

    @app.get("/api/v1/cost", dependencies=[Depends(auth)])
    def cost(namespace: str | None = None, context: str | None = None) -> dict[str, Any]:
        from kubesift.cost import cost_report
        return cost_report(snap(namespace, context, with_logs=False), settings)

    @app.get("/api/v1/upgrade", dependencies=[Depends(auth)])
    def upgrade(target: str, context: str | None = None) -> dict[str, Any]:
        from kubesift.upgrade import upgrade_report
        try:
            return upgrade_report(snap(None, context, with_logs=False), settings, target)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/lint", dependencies=[Depends(auth)])
    def lint(req: LintRequest) -> list[dict[str, Any]]:
        from kubesift.lint import LintError, lint_text
        try:
            return [f.model_dump(mode="json") for f in lint_text(req.manifests, settings, req.target_version)]
        except LintError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/history", dependencies=[Depends(auth)])
    def history(limit: int = 30) -> list[dict[str, Any]]:
        return store.list_reports(min(limit, 200))

    @app.get("/api/v1/history/{rid}", dependencies=[Depends(auth)])
    def history_item(rid: str) -> dict[str, Any]:
        report = store.get_report(rid)
        if report is None:
            raise HTTPException(status_code=404, detail="not found")
        return report.model_dump(mode="json")

    @app.post("/api/v1/fix", dependencies=[Depends(auth)])
    def fix(req: FixRequest) -> dict[str, Any]:
        from kubesift.cluster.client import KubeClient, KubeError
        from kubesift.remediate import execute, plan
        if not req.dry_run and not settings.allow_writes:
            raise HTTPException(status_code=403, detail="writes are disabled (set KUBESIFT_ALLOW_WRITES=true)")
        client = KubeClient(kubeconfig=settings.kubeconfig, context=settings.context, timeout=settings.request_timeout)
        try:
            p = plan(client, req.action, req.kind, req.namespace, req.name, req.replicas)
            result = execute(client, p, req.dry_run, store, actor="api")
        except (ValueError, KubeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"plan": p.description, "kubectl": p.kubectl, "dry_run": req.dry_run, "result": result}

    @app.get("/api/v1/audit-log", dependencies=[Depends(auth)])
    def audit_log() -> list[dict[str, Any]]:
        return store.list_audit()

    @app.post("/api/v1/webhooks/alertmanager", dependencies=[Depends(auth)])
    async def alertmanager(request: Request, background: BackgroundTasks) -> dict[str, Any]:
        """Alertmanager webhook receiver: investigates the namespaces of firing alerts and posts the diagnosis."""
        payload = await request.json()
        firing = [a for a in payload.get("alerts") or [] if a.get("status") == "firing"]
        from kubesift.util import check_name

        def _valid(ns: Any) -> bool:
            try:
                return isinstance(ns, str) and bool(check_name(ns))
            except ValueError:
                return False
        namespaces = sorted({(a.get("labels") or {}).get("namespace") for a in firing if
                             _valid((a.get("labels") or {}).get("namespace"))})
        names = ", ".join(sorted({(a.get("labels") or {}).get("alertname", "?") for a in firing}))
        # Alert storms fire the same alert many times; investigate each namespace at most once per cooldown.
        # The claim lives in the database, so replicas behind one Service don't duplicate the work.
        due = [ns for ns in namespaces or [None] if store.claim(f"alert:{ns or '*'}", settings.alert_cooldown)]
        if due:
            background.add_task(_alert_investigation, settings, store, due, names)
        return {"accepted": len(firing), "namespaces": namespaces, "investigating": due}

    class PreflightRequest(BaseModel):
        manifests: str = Field(max_length=2_000_000)
        namespace: str = "default"
        server_dry_run: bool = False
        check_images: bool = False

    @app.post("/api/v1/preflight", dependencies=[Depends(auth)])
    def preflight_api(req: PreflightRequest) -> dict[str, Any]:
        from kubesift.lint import LintError, _docs_from_text
        from kubesift.preflight import preflight
        try:
            docs = _docs_from_text(req.manifests, "request")
        except LintError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        client = KubeClient(kubeconfig=settings.kubeconfig, context=settings.context,
                            timeout=settings.request_timeout) if req.server_dry_run else None
        res = preflight(docs, snap(None, None, with_logs=False), settings, default_namespace=req.namespace,
                        client=client, server_dry_run=req.server_dry_run, check_images=req.check_images)
        return res.to_dict()

    @app.get("/api/v1/diff", dependencies=[Depends(auth)])
    def diff_api(since: str = "1h", namespace: str | None = None) -> dict[str, Any]:
        from kubesift.timetravel import diff, digest
        from kubesift.util import now
        current = digest(snap(namespace, None, with_logs=False))
        earlier = store.digest_before(current["cluster"], now() - parse_duration(since))
        if earlier is None:
            raise HTTPException(status_code=404, detail=f"no stored history from {since} ago yet")
        items = diff(earlier, current)
        if namespace:
            items = [i for i in items if f"/{namespace}/" in i["resource"] or i["kind"] == "Node"]
        return {"from": earlier.get("time"), "to": current.get("time"), "changes": items}

    @app.get("/api/v1/postmortem", dependencies=[Depends(auth)])
    def postmortem_api(namespace: str | None = None, since: str = "24h", format: str = "json") -> Any:
        from kubesift.postmortem import build, to_markdown
        s = snap(namespace, None)
        report = investigate(settings, s, use_llm=True, window=parse_duration(since), store=store)
        pm = build(report, s, store.list_audit(200), settings)
        return PlainTextResponse(to_markdown(pm)) if format == "markdown" else pm

    # ---------------------------------------------------------------- Slack
    async def _slack_verified(request: Request) -> bytes:
        from kubesift import slack
        if not settings.slack_signing_secret:
            raise HTTPException(status_code=404, detail="Slack integration is not configured")
        body = await request.body()
        if not slack.verify(settings.slack_signing_secret, request.headers.get("X-Slack-Request-Timestamp"), body,
                            request.headers.get("X-Slack-Signature")):
            raise HTTPException(status_code=401, detail="invalid Slack signature")
        return body

    @app.post("/api/v1/slack/commands", include_in_schema=False)
    async def slack_command(request: Request, background: BackgroundTasks) -> dict[str, Any]:
        from urllib.parse import parse_qs

        from kubesift import slack
        form = {k: v[0] for k, v in parse_qs((await _slack_verified(request)).decode()).items()}
        verb, args = slack.parse(form.get("text", ""))
        from kubesift.util import check_name, parse_duration
        try:
            if verb in ("diagnose", "changes", "postmortem") and args:
                check_name(args[0], "namespace")
            if verb == "changes" and len(args) > 1:
                parse_duration(args[1])
            if verb == "why":
                for a in args[:2]:
                    check_name(a)
        except ValueError as exc:
            return {"response_type": "ephemeral", "text": f":x: {exc}"}
        if verb not in ("diagnose", "why", "changes", "postmortem"):
            return {"response_type": "ephemeral", "text": slack.HELP}
        background.add_task(_slack_work, settings, store, verb, args, form.get("response_url", ""))
        return {"response_type": "ephemeral", "text": f":mag: Working on `{verb} {' '.join(args)}`…"}

    @app.post("/api/v1/slack/actions", include_in_schema=False)
    async def slack_action(request: Request, background: BackgroundTasks) -> dict[str, Any]:
        import json as _json
        from urllib.parse import parse_qs
        body = await _slack_verified(request)
        payload = _json.loads(parse_qs(body.decode()).get("payload", ["{}"])[0])
        actions = payload.get("actions") or []
        if not actions or actions[0].get("action_id") != "sift_fix":
            return {}
        value = _json.loads(actions[0].get("value") or "{}")
        user = (payload.get("user") or {}).get("id", "")
        background.add_task(_slack_fix, settings, store, value, user, payload.get("response_url", ""))
        return {}

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> PlainTextResponse:
        report: Report | None = state["last_report"]
        lines = ["# HELP kubesift_findings Findings from the last scan by severity and kind",
                 "# TYPE kubesift_findings gauge"]
        if report:
            for sev in Severity:
                for kind in ("problem", "risk"):
                    n = sum(1 for f in report.findings if f.severity == sev and f.kind == kind)
                    lines.append(f'kubesift_findings{{severity="{sev.value}",kind="{kind}"}} {n}')
        lines += ["# HELP kubesift_last_scan_timestamp_seconds Unix time of the last scan",
                  "# TYPE kubesift_last_scan_timestamp_seconds gauge",
                  f"kubesift_last_scan_timestamp_seconds {state['last_scan'] or 0}",
                  "# HELP kubesift_leader 1 if this replica runs background scans",
                  "# TYPE kubesift_leader gauge",
                  f"kubesift_leader {int(state['elector'].is_leader)}"]
        return PlainTextResponse("\n".join(lines) + "\n")

    if settings.scan_interval > 0:
        if settings.leader_election:
            client = KubeClient(kubeconfig=settings.kubeconfig, context=settings.context, timeout=settings.request_timeout)
            state["elector"] = LeaderElector(client, settings.leader_namespace or pod_namespace()).start()
        threading.Thread(target=_scan_loop, args=(settings, store, state), daemon=True, name="scanner").start()
    if mcp_asgi is not None:
        app.mount("/", mcp_asgi)  # registered last, so the API and dashboard routes above win
    return app


_ALERT_SLOTS = threading.BoundedSemaphore(2)  # at most two alert investigations at a time


def _alert_investigation(settings: Settings, store: Store, namespaces: list[str | None], alerts: str) -> None:
    with _ALERT_SLOTS:
        _alert_investigation_locked(settings, store, namespaces, alerts)


def _alert_investigation_locked(settings: Settings, store: Store, namespaces: list[str | None], alerts: str) -> None:
    for ns in namespaces:
        try:
            report = investigate(settings, snapshot_for(settings, ns), use_llm=True, store=store, include_risks=False)
            store.save_report(report, source="alertmanager", retention_days=settings.history_days)
            problems = [f for f in report.findings if f.kind == "problem"]
            title = f"Alert {alerts} in {ns or 'cluster'}: " + (report.diagnosis.summary[:120] if report.diagnosis else "")
            notify.send(settings, report, problems or report.findings[:3], title=title)
        except Exception as exc:
            log.warning("alert investigation failed for %s: %s", ns, exc)


def _scan_loop(settings: Settings, store: Store, state: dict[str, Any]) -> None:
    threshold = Severity.parse(settings.notify_min_severity).rank
    while True:
        if not state["elector"].is_leader:
            time.sleep(5)  # another replica holds the lease
            continue
        try:
            snap = snapshot_for(settings, settings.scan_namespace)
            report = investigate(settings, snap, use_llm=False, window=timedelta(hours=1), store=store)
            state["last_report"] = report
            state["last_scan"] = int(time.time())
            try:
                from kubesift.timetravel import digest
                store.save_digest(digest(snap))  # history for `kubesift diff --since` / time travel
            except Exception as exc:
                log.warning("could not store cluster digest: %s", exc)
            urgent = [f for f in report.findings if f.kind == "problem" and f.severity.rank >= threshold]
            fresh = store.new_findings(report.cluster, urgent)
            if fresh:
                from kubesift.analysis.engine import explain
                report.diagnosis = explain(settings, report, use_llm=True)
                store.save_report(report, source="scan", retention_days=settings.history_days)
                notify.send(settings, report, fresh)
        except Exception as exc:
            log.warning("background scan failed: %s", exc)
        time.sleep(settings.scan_interval)


def _slack_post(url: str, blocks: list[dict[str, Any]], text: str) -> None:
    import httpx
    if not url.startswith("https://hooks.slack.com/"):
        log.warning("refusing to post to a non-Slack response_url")
        return
    try:
        httpx.post(url, json={"response_type": "in_channel", "replace_original": False, "text": text,
                              "blocks": blocks}, timeout=15)
    except httpx.HTTPError as exc:
        log.warning("Slack response failed: %s", exc)


def _slack_work(settings: Settings, store: Store, verb: str, args: list[str], response_url: str) -> None:
    from kubesift import slack
    from kubesift.analysis.changes import detect_changes
    try:
        if verb == "diagnose":
            ns = args[0] if args else None
            report = investigate(settings, snapshot_for(settings, ns), use_llm=True, store=store, include_risks=False)
            store.save_report(report, source="slack", retention_days=settings.history_days)
            _slack_post(response_url, slack.report_blocks(report, f"Diagnosis: {ns or 'all namespaces'}"), "KubeSift diagnosis")
        elif verb == "why":
            if not args:
                _slack_post(response_url, [{"type": "section", "text": {"type": "mrkdwn", "text": slack.HELP}}], "help")
                return
            ns = args[1] if len(args) > 1 else None
            report, answer = ask(settings, snapshot_for(settings, ns), f"why is {args[0]} failing?", store=store)
            blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{args[0]}*: {answer['answer'][:2800]}"}}]
            blocks += slack.report_blocks(report, f"Evidence for {args[0]}")[1:]
            _slack_post(response_url, blocks, answer["answer"][:200])
        elif verb == "changes":
            ns = args[0] if args else None
            since = args[1] if len(args) > 1 else "6h"
            items = detect_changes(snapshot_for(settings, ns, with_logs=False), parse_duration(since))
            _slack_post(response_url, slack.changes_blocks(items, ns or "all namespaces", since), "KubeSift changes")
        elif verb == "postmortem":
            from kubesift.postmortem import build, to_markdown
            ns = args[0] if args else None
            s = snapshot_for(settings, ns)
            pm = build(investigate(settings, s, use_llm=True, store=store), s, store.list_audit(200), settings)
            md = to_markdown(pm)
            _slack_post(response_url, [{"type": "section", "text": {"type": "mrkdwn", "text": md[:2900]}}], pm["title"])
    except Exception as exc:
        _slack_post(response_url, [{"type": "section", "text": {"type": "mrkdwn", "text": f":x: {exc}"}}], "KubeSift error")


def _slack_fix(settings: Settings, store: Store, value: dict[str, Any], user: str, response_url: str) -> None:
    from kubesift import slack
    from kubesift.cluster.client import KubeError
    from kubesift.remediate import execute, plan
    # Applying from Slack needs both allow_writes and the user on an explicit allowlist (empty list = nobody).
    may_apply = bool(settings.allow_writes and user and user in settings.slack_allowed_users)
    dry_run = bool(value.get("dry_run", True)) or not may_apply
    try:
        client = KubeClient(kubeconfig=settings.kubeconfig, context=settings.context, timeout=settings.request_timeout)
        p = plan(client, value["action"], value["kind"], value["namespace"], value["name"], value.get("replicas"))
        result = execute(client, p, dry_run, store, actor=f"slack:{user}")
        blocks = slack.fix_result_blocks(p.description, p.kubectl, result, dict(value, dry_run=dry_run), may_apply)
    except (KubeError, ValueError, KeyError) as exc:
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f":x: {exc}"}}]
    _slack_post(response_url, blocks, "KubeSift fix")
