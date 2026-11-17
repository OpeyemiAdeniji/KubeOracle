"""MCP server: lets AI assistants (Claude Code, Claude Desktop, Cursor, VS Code, ...) investigate the cluster.

Tools return structured evidence; the assistant's own model does the reasoning, so no LLM key is needed here.
Write tools are only registered when allow_writes is enabled, and default to a server-side dry run.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from kubesift import __version__
from kubesift.cluster.client import ClusterAccessError, KubeError
from kubesift.config import Settings
from kubesift.lint import LintError

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

F = TypeVar("F", bound=Callable[..., Any])

# Failures we expect and can explain: bad input, an unreachable cluster, an API refusal, unreadable YAML.
# The SDK hides the text of any other exception from the model ("Error executing tool diagnose"), so these
# are re-raised as ToolError: the call is marked is_error and the assistant reads why and what to do.
EXPLAINED = (ValueError, ClusterAccessError, KubeError, LintError, FileNotFoundError)


def explained(fn: F) -> F:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except EXPLAINED as exc:
            raise ToolError(str(exc)) from exc
    return wrapper  # type: ignore[return-value]


def _found(result: Any) -> Any:
    """Turn the engine's {"error": ...} answer (not found, no history) into a failed tool call."""
    if isinstance(result, dict) and set(result) == {"error"}:
        raise ToolError(result["error"])
    return result

INSTRUCTIONS = """KubeSift reads a Kubernetes cluster and returns evidence-backed findings.
Start with `diagnose` (optionally scoped to a namespace), then use `explain_resource` on the workloads it names,
and `recent_changes` to see what was deployed or edited before the problem began.
Findings come from deterministic checks; quote their evidence and commands rather than guessing."""


def _compact_report(report: Any, include_risks: bool) -> dict[str, Any]:
    def f(x: Any) -> dict[str, Any]:
        return {"fingerprint": x.fingerprint, "check": x.check, "severity": x.severity.value, "kind": x.kind,
                "resource": str(x.resource), "owner": str(x.owner) if x.owner else None, "node": x.node,
                "message": x.message, "evidence": x.evidence[:8], "fix": x.remediation, "commands": x.commands[:3],
                "started": x.since.isoformat() if x.since else None,
                "after_changes": [c.summary for c in x.related_changes], "team_notes": x.notes[:3]}
    return {
        "cluster": report.cluster, "namespace": report.namespace or "all", "kubernetes": report.server_version,
        "summary": report.diagnosis.summary if report.diagnosis else "",
        "incidents": [{"title": i.title, "layer": i.layer, "severity": i.severity.value,
                       "likely_trigger": i.likely_trigger.summary if i.likely_trigger else None,
                       "rollback": i.likely_trigger.rollback_command if i.likely_trigger else None,
                       "findings": [f(x) for x in i.findings[:8]]} for i in report.incidents[:15]],
        "risks": [f(x) for x in report.findings if x.kind == "risk"][:40] if include_risks else
        f"{sum(1 for x in report.findings if x.kind == 'risk')} risks hidden; call with include_risks=true",
        "data_gaps": report.warnings,
    }


def build_server(settings: Settings) -> MCPServer:
    from kubesift.analysis.changes import detect_changes
    from kubesift.analysis.engine import explain_resource, investigate, snapshot_for
    from kubesift.store import open_store
    from kubesift.util import parse_duration

    server = MCPServer(name="kubesift", version=__version__, instructions=INSTRUCTIONS)
    store = open_store(settings)

    @server.tool(annotations=READ_ONLY)
    @explained
    def diagnose(namespace: str | None = None, include_risks: bool = False, context: str | None = None) -> str:
        """Scan the cluster (or one namespace) and return problems grouped into incidents, with evidence,
        likely triggering changes and suggested fixes. Set include_risks to also list configuration risks."""
        report = investigate(settings, snapshot_for(settings, namespace, context), use_llm=False, store=store)
        store.save_report(report, source="mcp", retention_days=settings.history_days)
        return json.dumps(_compact_report(report, include_risks), default=str)

    @server.tool(name="explain_resource", annotations=READ_ONLY)
    @explained
    def explain_resource_tool(kind: str, name: str, namespace: str | None = None, context: str | None = None) -> str:
        """Status, pods, recent events, redacted error logs, findings and changes for one resource.
        kind: pod, deployment, statefulset, daemonset, service, ingress, job, cronjob, node, pvc..."""
        snap = snapshot_for(settings, namespace if kind.lower() not in ("node", "no") else None, context)
        return json.dumps(_found(explain_resource(settings, snap, kind, namespace, name)), default=str)

    @server.tool(annotations=READ_ONLY)
    @explained
    def recent_changes(namespace: str | None = None, since: str = "24h", context: str | None = None) -> str:
        """Rollouts (with image/env diffs), ConfigMap/Secret edits, Helm upgrades, Argo CD syncs, scaling and node
        events in the window (e.g. 30m, 6h, 2d). Includes rollback commands."""
        snap = snapshot_for(settings, namespace, context, with_logs=False)
        return json.dumps([c.model_dump(mode="json") for c in detect_changes(snap, parse_duration(since))])

    @server.tool(annotations=READ_ONLY)
    @explained
    def cost_report(namespace: str | None = None, context: str | None = None) -> str:
        """Right-sizing suggestions and waste (over-requested CPU/memory, idle GPUs, unused volumes, idle LBs)."""
        from kubesift.cost import cost_report as build
        return json.dumps(build(snapshot_for(settings, namespace, context, with_logs=False), settings))

    @server.tool(annotations=READ_ONLY)
    @explained
    def upgrade_check(target_version: str, context: str | None = None) -> str:
        """What blocks upgrading to target_version (e.g. 1.36): removed APIs, PDBs that block drains,
        kubelet skew, containerd 1.x, retired ingress-nginx, deprecated features."""
        from kubesift.upgrade import upgrade_report
        return json.dumps(upgrade_report(snapshot_for(settings, None, context, with_logs=False), settings,
                                         target_version), default=str)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False, idempotent_hint=True))
    @explained
    def lint_manifests(manifests_yaml: str, target_version: str | None = None) -> str:
        """Check Kubernetes YAML (multi-document) for reliability, security and removed-API problems before it ships."""
        from kubesift.lint import lint_text
        return json.dumps([f.model_dump(mode="json") for f in lint_text(manifests_yaml, settings, target_version)])

    @server.tool(annotations=READ_ONLY)
    @explained
    def preflight_manifests(manifests_yaml: str, namespace: str = "default", context: str | None = None) -> str:
        """Check Kubernetes YAML against the LIVE cluster before deploying: missing Secrets/ConfigMaps/keys,
        namespaces, StorageClasses, capacity and quota, PDBs, removed env vars, and memory limits below current usage."""
        from kubesift.lint import _docs_from_text
        from kubesift.preflight import preflight
        res = preflight(_docs_from_text(manifests_yaml, "mcp"), snapshot_for(settings, None, context, with_logs=False),
                        settings, default_namespace=namespace)
        return json.dumps(res.to_dict(), default=str)

    @server.tool(annotations=READ_ONLY)
    @explained
    def cluster_diff(since: str = "1h", namespace: str | None = None, context: str | None = None) -> str:
        """Time travel: what differs between the cluster now and `since` ago (needs stored history from the
        in-cluster service or `kubesift snapshot record`)."""
        from kubesift.timetravel import diff, digest
        from kubesift.util import now
        current = digest(snapshot_for(settings, namespace, context, with_logs=False))
        earlier = store.digest_before(current["cluster"], now() - parse_duration(since))
        if earlier is None:
            raise ToolError(f"no stored history from {since} ago; history comes from the in-cluster service "
                            "or `kubesift snapshot record`")
        return json.dumps({"from": earlier.get("time"), "to": current.get("time"), "changes": diff(earlier, current)})

    @server.tool(annotations=READ_ONLY)
    @explained
    def draft_postmortem(namespace: str | None = None, since: str = "24h", context: str | None = None) -> str:
        """Draft a blameless postmortem (impact, timeline, root cause, triggering change, action items) as Markdown."""
        from kubesift.postmortem import build, to_markdown
        s = snapshot_for(settings, namespace, context)
        report = investigate(settings, s, use_llm=False, window=parse_duration(since), store=store)
        return to_markdown(build(report, s, store.list_audit(200)))

    @server.tool(annotations=READ_ONLY)
    @explained
    def list_checks() -> str:
        """Every check KubeSift runs, with severity and what it detects."""
        from kubesift.checks import REGISTRY
        return json.dumps([{"id": c.id, "title": c.title, "severity": c.severity.value, "category": c.category.value,
                            "description": c.description} for c in REGISTRY.values()])

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    @explained
    def add_team_note(check_id: str, note: str, target: str | None = None) -> str:
        """Record what fixed a problem (shown next to matching findings in future investigations)."""
        store.add_note(check_id, note, target, author="mcp")
        return "saved"

    if settings.allow_writes:
        @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False))
        @explained
        def apply_fix(action: str, kind: str, namespace: str, name: str, replicas: int | None = None,
                      dry_run: bool = True) -> str:
            """Guarded remediation: restart | rollback | scale | delete-pod. Always run with dry_run=true first and
            ask the user before calling with dry_run=false."""
            from kubesift.cluster.client import KubeClient
            from kubesift.remediate import execute, plan
            client = KubeClient(kubeconfig=settings.kubeconfig, context=settings.context, timeout=settings.request_timeout)
            p = plan(client, action, kind, namespace, name, replicas)
            return json.dumps({"plan": p.description, "kubectl": p.kubectl, "dry_run": dry_run,
                               "result": execute(client, p, dry_run, store, actor="mcp")})

    return server


class TokenGate:
    """ASGI wrapper that requires `Authorization: Bearer <api_token>` on HTTP requests."""

    def __init__(self, app: Any, token: str | None):
        self.app = app
        self.token = token

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and self.token:
            import hmac

            headers = dict(scope.get("headers") or [])
            given = headers.get(b"authorization", b"").decode("latin-1").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(given, self.token):
                from starlette.responses import JSONResponse

                resp = JSONResponse({"detail": "invalid or missing bearer token"}, status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"})
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_http_app(settings: Settings, path: str = "/mcp") -> tuple[MCPServer, TokenGate]:
    """Streamable-HTTP MCP app. Stateless, so any replica behind a load balancer can answer any request."""
    from mcp.server.transport_security import TransportSecuritySettings

    server = build_server(settings)
    # With a token, requests are authenticated, so Host checks only get in the way of ingress/port-forward.
    # Without one (local use), keep DNS-rebinding protection on.
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=not settings.api_token,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    )
    app = server.streamable_http_app(streamable_http_path=path, stateless_http=True, json_response=True,
                                     transport_security=security)
    return server, TokenGate(app, settings.api_token)
