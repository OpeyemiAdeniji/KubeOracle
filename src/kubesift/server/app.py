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
        key = (namespace, context, with_logs)
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
        namespaces = sorted({(a.get("labels") or {}).get("namespace") for a in firing} - {None})
        names = ", ".join(sorted({(a.get("labels") or {}).get("alertname", "?") for a in firing}))
        # Alert storms fire the same alert many times; investigate each namespace at most once per cooldown.
        # The claim lives in the database, so replicas behind one Service don't duplicate the work.
        due = [ns for ns in namespaces or [None] if store.claim(f"alert:{ns or '*'}", settings.alert_cooldown)]
        if due:
            background.add_task(_alert_investigation, settings, store, due, names)
        return {"accepted": len(firing), "namespaces": namespaces, "investigating": due}

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
