"""REST API, web dashboard, Alertmanager webhook, Prometheus metrics and background scanning."""

from __future__ import annotations

import hmac
import logging
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from kubeoracle import __version__, notify
from kubeoracle.analysis.changes import detect_changes
from kubeoracle.analysis.engine import ask, explain_resource, investigate, snapshot_for
from kubeoracle.cluster.client import list_contexts
from kubeoracle.config import Settings
from kubeoracle.models import Report, Severity
from kubeoracle.store import Store
from kubeoracle.util import parse_duration

log = logging.getLogger("kubeoracle.server")
STATIC = Path(__file__).parent / "static"


class DiagnoseRequest(BaseModel):
    namespace: str | None = None
    context: str | None = None
    llm: bool = True
    since: str = "6h"


class AskRequest(BaseModel):
    question: str
    namespace: str | None = None
    context: str | None = None
    llm: bool = True


class LintRequest(BaseModel):
    manifests: str
    target_version: str | None = None


class FixRequest(BaseModel):
    action: str
    kind: str
    namespace: str
    name: str
    replicas: int | None = None
    dry_run: bool = True


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="KubeOracle", version=__version__,
                  description="Evidence-based Kubernetes troubleshooting API.")
    store = Store(settings.data_dir)
    state: dict[str, Any] = {"last_scan": None, "last_report": None}
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
                           allow_headers=["Authorization", "Content-Type"])

    def auth(authorization: str | None = Header(default=None)) -> None:
        if not settings.api_token:
            return
        token = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token, settings.api_token):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    def snap(namespace: str | None, context: str | None, with_logs: bool = True):
        try:
            return snapshot_for(settings, namespace, context, with_logs=with_logs)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"cannot reach the cluster: {exc}") from exc

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
                "last_scan": state["last_scan"]}

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
        rid = store.save_report(report, source="api")
        state["last_report"] = report
        return {"id": rid, **report.model_dump(mode="json")}

    @app.post("/api/v1/ask", dependencies=[Depends(auth)])
    def ask_question(req: AskRequest) -> dict[str, Any]:
        report, answer = ask(settings, snap(req.namespace, req.context), req.question, use_llm=req.llm, store=store)
        rid = store.save_report(report, source="api-ask")
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
        from kubeoracle.cost import cost_report
        return cost_report(snap(namespace, context, with_logs=False), settings)

    @app.get("/api/v1/upgrade", dependencies=[Depends(auth)])
    def upgrade(target: str, context: str | None = None) -> dict[str, Any]:
        from kubeoracle.upgrade import upgrade_report
        try:
            return upgrade_report(snap(None, context, with_logs=False), settings, target)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/lint", dependencies=[Depends(auth)])
    def lint(req: LintRequest) -> list[dict[str, Any]]:
        from kubeoracle.lint import LintError, lint_text
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
        from kubeoracle.cluster.client import KubeClient, KubeError
        from kubeoracle.remediate import execute, plan
        if not req.dry_run and not settings.allow_writes:
            raise HTTPException(status_code=403, detail="writes are disabled (set KUBEORACLE_ALLOW_WRITES=true)")
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
        background.add_task(_alert_investigation, settings, store, namespaces or [None], names)
        return {"accepted": len(firing), "namespaces": namespaces}

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> PlainTextResponse:
        report: Report | None = state["last_report"]
        lines = ["# HELP kubeoracle_findings Findings from the last scan by severity and kind",
                 "# TYPE kubeoracle_findings gauge"]
        if report:
            for sev in Severity:
                for kind in ("problem", "risk"):
                    n = sum(1 for f in report.findings if f.severity == sev and f.kind == kind)
                    lines.append(f'kubeoracle_findings{{severity="{sev.value}",kind="{kind}"}} {n}')
        lines += ["# HELP kubeoracle_last_scan_timestamp_seconds Unix time of the last scan",
                  "# TYPE kubeoracle_last_scan_timestamp_seconds gauge",
                  f"kubeoracle_last_scan_timestamp_seconds {state['last_scan'] or 0}"]
        return PlainTextResponse("\n".join(lines) + "\n")

    if settings.scan_interval > 0:
        threading.Thread(target=_scan_loop, args=(settings, store, state), daemon=True, name="scanner").start()
    return app


def _alert_investigation(settings: Settings, store: Store, namespaces: list[str | None], alerts: str) -> None:
    for ns in namespaces:
        try:
            report = investigate(settings, snapshot_for(settings, ns), use_llm=True, store=store, include_risks=False)
            store.save_report(report, source="alertmanager")
            problems = [f for f in report.findings if f.kind == "problem"]
            title = f"Alert {alerts} in {ns or 'cluster'}: " + (report.diagnosis.summary[:120] if report.diagnosis else "")
            notify.send(settings, report, problems or report.findings[:3], title=title)
        except Exception as exc:
            log.warning("alert investigation failed for %s: %s", ns, exc)


def _scan_loop(settings: Settings, store: Store, state: dict[str, Any]) -> None:
    threshold = Severity.parse(settings.notify_min_severity).rank
    while True:
        try:
            snap = snapshot_for(settings, settings.scan_namespace)
            report = investigate(settings, snap, use_llm=False, window=timedelta(hours=1), store=store)
            state["last_report"] = report
            state["last_scan"] = int(time.time())
            urgent = [f for f in report.findings if f.kind == "problem" and f.severity.rank >= threshold]
            fresh = store.new_findings(report.cluster, urgent)
            if fresh:
                from kubeoracle.analysis.engine import explain
                report.diagnosis = explain(settings, report, use_llm=True)
                store.save_report(report, source="scan")
                notify.send(settings, report, fresh)
        except Exception as exc:
            log.warning("background scan failed: %s", exc)
        time.sleep(settings.scan_interval)
