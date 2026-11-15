"""Command line interface. Also installed as `kubectl-oracle`, so `kubectl oracle ...` works."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from kubeoracle import __version__
from kubeoracle.config import Settings, load_settings
from kubeoracle.models import Report, Severity

app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich", add_completion=True,
                  help="KubeOracle: find out what is broken in Kubernetes, why, and what changed.")
note_app = typer.Typer(help="Team notes: record what fixed a problem so the next person sees it.")
snapshot_app = typer.Typer(help="Save a redacted cluster snapshot to analyse offline or attach to a bug report.")
app.add_typer(note_app, name="note")
app.add_typer(snapshot_app, name="snapshot")

console = Console()
err = Console(stderr=True)


class State:
    settings: Settings


state = State()


@app.callback()
def main_options(
    kubeconfig: str | None = typer.Option(None, "--kubeconfig", envvar="KUBECONFIG_PATH", help="Path to kubeconfig."),
    context: str | None = typer.Option(None, "--context", help="Kubeconfig context to use."),
    llm: str | None = typer.Option(None, "--llm-provider", help="anthropic | openai | openrouter | ollama | none"),
    model: str | None = typer.Option(None, "--model", help="LLM model name."),
    prometheus: str | None = typer.Option(None, "--prometheus", help="Prometheus URL for usage history."),
) -> None:
    state.settings = load_settings(kubeconfig=kubeconfig, context=context, llm_provider=llm, llm_model=model,
                                   prometheus_url=prometheus)


def _fail(msg: str, code: int = 1) -> None:
    err.print(Text(f"error: {msg}", style="bold red"))
    raise typer.Exit(code)


def _snapshot(namespace: str | None, from_file: str | None = None, with_logs: bool = True):
    from kubeoracle.cluster.snapshot import Snapshot

    if from_file:
        data = json.loads(Path(from_file).read_text(encoding="utf-8"))
        snap = Snapshot.from_dict(data)
        if namespace:
            snap.namespace = namespace
        return snap
    from kubeoracle.analysis.engine import snapshot_for

    try:
        with err.status("Reading cluster state…"):
            return snapshot_for(state.settings, namespace, with_logs=with_logs)
    except Exception as exc:  # kubeconfig missing, cluster unreachable
        _fail(f"cannot connect to the cluster: {exc}")


def _store():
    from kubeoracle.store import Store

    try:
        return Store(state.settings.data_dir)
    except Exception:
        return None


def _exit_on(report_findings: list, fail_on: str | None) -> None:
    if not fail_on:
        return
    threshold = Severity.parse(fail_on).rank
    if any(f.severity.rank >= threshold for f in report_findings):
        raise typer.Exit(2)


def _emit(report: Report, output: str, risks: bool, verbose: bool) -> None:
    from kubeoracle.report import print_report, to_markdown

    if output == "json":
        print(report.model_dump_json(indent=2))
    elif output in ("md", "markdown"):
        print(to_markdown(report))
    else:
        print_report(console, report, show_risks=risks, verbose=verbose)


NS = typer.Option(None, "--namespace", "-n", help="Only this namespace (default: all namespaces).")
OUT = typer.Option("text", "--output", "-o", help="text | json | markdown")
LLM = typer.Option(True, "--llm/--no-llm", help="Use the configured LLM to explain findings.")
FAIL = typer.Option(None, "--fail-on", help="Exit 2 if a finding at this severity or above exists.")
FROM = typer.Option(None, "--from", help="Analyse a saved snapshot file instead of the live cluster.")


@app.command()
def diagnose(
    namespace: str | None = NS,
    output: str = OUT,
    llm: bool = LLM,
    risks: bool = typer.Option(False, "--risks", help="List configuration risks, not just a count."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show evidence, fixes and recent changes."),
    since: str = typer.Option("6h", "--since", help="How far back to look for changes."),
    fail_on: str | None = FAIL,
    from_file: str | None = FROM,
) -> None:
    """Scan the cluster, group problems into incidents and explain the most likely causes."""
    from kubeoracle.analysis.engine import investigate
    from kubeoracle.util import parse_duration

    snap = _snapshot(namespace, from_file)
    store = _store()
    with err.status("Analysing…"):
        report = investigate(state.settings, snap, use_llm=llm, window=parse_duration(since), store=store)
    if store:
        store.save_report(report, retention_days=state.settings.history_days)
    _emit(report, output, risks, verbose)
    _exit_on(report.findings, fail_on)


@app.command()
def audit(
    namespace: str | None = NS,
    output: str = OUT,
    fail_on: str | None = FAIL,
    from_file: str | None = FROM,
) -> None:
    """Production-readiness review: reliability, security, resources and upgrade risks."""
    from kubeoracle.analysis.engine import investigate

    snap = _snapshot(namespace, from_file, with_logs=False)
    report = investigate(state.settings, snap, use_llm=False)
    report.findings = [f for f in report.findings if f.kind == "risk"]
    report.incidents = []
    report.diagnosis = None
    if output == "text":
        from kubeoracle.report import print_findings
        print_findings(console, report.findings, title=f"Audit: {report.cluster}")
    else:
        _emit(report, output, True, False)
    _exit_on(report.findings, fail_on)


@app.command()
def ask(
    question: list[str] = typer.Argument(..., help="Your question in plain English."),
    namespace: str | None = NS,
    output: str = OUT,
    llm: bool = LLM,
    from_file: str | None = FROM,
) -> None:
    """Ask a question: "why is checkout failing?", "is payments ready for production?"."""
    from kubeoracle.analysis.engine import ask as do_ask
    from kubeoracle.analysis.engine import mentioned_namespace

    q = " ".join(question)
    snap = _snapshot(namespace, from_file)
    if not namespace:
        ns = mentioned_namespace(q, [n.get("metadata", {}).get("name", "") for n in snap.all("namespaces")])
        if ns:
            snap.namespace = ns
    with err.status("Thinking…"):
        report, answer = do_ask(state.settings, snap, q, use_llm=llm, store=_store())
    if output == "json":
        print(json.dumps({"answer": answer, "report": report.model_dump(mode="json")}, indent=2, default=str))
        return
    console.print(Text(f"Q: {q}", style="bold"))
    console.print(Text(answer["answer"]))
    console.print(Text(f"confidence {answer['confidence']}% · {answer.get('generated_by')}", style="dim"))
    for c in answer.get("commands") or []:
        console.print(Text(f"$ {c}", style="cyan"))
    for fu in answer.get("follow_up") or []:
        console.print(Text(f"→ {fu}", style="green"))
    if report.findings:
        from kubeoracle.report import print_findings
        print_findings(console, report.findings, title="Supporting findings")


@app.command()
def changes(
    namespace: str | None = NS,
    since: str = typer.Option("24h", "--since", help="Window, e.g. 30m, 6h, 2d."),
    output: str = OUT,
    from_file: str | None = FROM,
) -> None:
    """What changed recently: rollouts, config edits, Helm upgrades, Argo CD syncs, scaling, nodes."""
    from kubeoracle.analysis.changes import detect_changes
    from kubeoracle.report import print_changes
    from kubeoracle.util import parse_duration

    snap = _snapshot(namespace, from_file, with_logs=False)
    items = detect_changes(snap, parse_duration(since))
    if output == "json":
        print(json.dumps([c.model_dump(mode="json") for c in items], indent=2))
    else:
        print_changes(console, items, title=f"Changes in the last {since}")


@app.command()
def explain(
    resource: str = typer.Argument(..., help="kind/name, e.g. deploy/checkout or pod/web-abc"),
    namespace: str = typer.Option("default", "--namespace", "-n"),
    output: str = OUT,
    from_file: str | None = FROM,
) -> None:
    """Everything about one resource: status, pods, events, logs, findings and changes."""
    from kubeoracle.analysis.engine import explain_resource

    if "/" not in resource:
        _fail("use kind/name, e.g. deploy/checkout")
    kind, name = resource.split("/", 1)
    snap = _snapshot(namespace, from_file)
    data = explain_resource(state.settings, snap, kind, None if kind.lower() in ("node", "no") else namespace, name)
    if output == "json" or "error" in data:
        print(json.dumps(data, indent=2, default=str))
        if "error" in data:
            raise typer.Exit(1)
        return
    import yaml
    r = data["resource"]
    console.rule(f"{r['kind']}/{r['name']}" + (f" ({r['namespace']})" if r.get("namespace") else ""))
    console.print(yaml.safe_dump({k: v for k, v in r.items() if k not in ("events", "logs")}, sort_keys=False))
    for e in r.get("events") or []:
        console.print(Text(e, style="yellow" if "Warning" in e else "dim"))
    for p in data["pods"]:
        console.print(Text(f"pod {p['name']}: {p.get('phase')} " + ", ".join(
            f"{c['name']}={c['state']}{'/' + c['reason'] if c.get('reason') else ''} restarts={c['restarts']}"
            for c in p.get("containers") or []), style="bold"))
    from kubeoracle.models import Finding
    from kubeoracle.report import print_findings
    print_findings(console, [Finding.model_validate(f) for f in data["findings"]], title="Findings")


@app.command()
def lint(
    paths: list[str] = typer.Argument(..., help="Files, directories, Helm charts, Kustomize dirs, or - for stdin."),
    target_version: str | None = typer.Option(None, "--target-version", "-t", help="Kubernetes version to check against, e.g. 1.36"),
    values: list[str] = typer.Option([], "--values", "-f", help="Helm values files."),
    output: str = typer.Option("text", "--output", "-o", help="text | json | sarif | markdown"),
    fail_on: str | None = typer.Option("high", "--fail-on", help="Exit 2 at or above this severity (none to disable)."),
) -> None:
    """Check manifests before they ship (CI / pre-commit). No cluster needed."""
    from kubeoracle.lint import LintError
    from kubeoracle.lint import lint as do_lint
    from kubeoracle.report import print_findings, to_sarif

    try:
        findings = do_lint(paths, state.settings, target_version=target_version, values=values)
    except LintError as exc:
        _fail(str(exc))
    if output == "sarif":
        print(to_sarif(findings))
    elif output == "json":
        print(json.dumps([f.model_dump(mode="json") for f in findings], indent=2))
    elif output in ("md", "markdown"):
        print("| Severity | Check | Resource | Source | Detail |\n|---|---|---|---|---|")
        for f in findings:
            print(f"| {f.severity.value} | {f.check} | `{f.resource}` | {f.source or ''} | {f.message} |")
    else:
        print_findings(console, findings, title="Lint results")
    _exit_on(findings, None if (fail_on or "").lower() == "none" else fail_on)


@app.command()
def cost(
    namespace: str | None = NS,
    output: str = OUT,
    from_file: str | None = FROM,
) -> None:
    """Right-sizing and waste: over-requested CPU/memory, idle GPUs, unused volumes and load balancers."""
    from kubeoracle.cost import cost_report

    snap = _snapshot(namespace, from_file, with_logs=False)
    data = cost_report(snap, state.settings)
    if output == "json":
        print(json.dumps(data, indent=2))
        return
    c = data["cluster"]
    console.print(Text(f"{c['nodes']} nodes · {c['cpu']} CPU · {c['memory']} memory · "
                       f"~${c['monthly_compute_estimate']:,.0f}/month compute (list prices)", style="bold"))
    console.print(Text(f"usage source: {data['source']}", style="dim"))
    t = Table(title="Right-sizing", expand=True)
    for col in ("Workload", "Container", "CPU req → used → suggest", "Memory req → used → suggest", "$/month saved"):
        t.add_column(col)
    for i in data["rightsizing"][:25]:
        t.add_row(i["workload"], i["container"], f"{i['cpu_request_h']} → {i['cpu_used_h']} → {i['recommend_cpu']}",
                  f"{i['mem_request_h']} → {i['mem_used_h']} → {i['recommend_memory']}" + (f"\n{i['note']}" if i["note"] else ""),
                  f"{i['monthly_savings']:,.2f}")
    console.print(t)
    if data["waste"]:
        w = Table(title="Waste", expand=True)
        for col in ("Type", "Resource", "Detail", "$/month"):
            w.add_column(col)
        for x in data["waste"]:
            w.add_row(x["type"], x["resource"], x["detail"], f"{x['monthly_cost']:,.2f}")
        console.print(w)
    console.print(Text(f"Potential savings: ~${data['totals']['monthly_savings']:,.0f}/month", style="bold green"))
    for warning in data["warnings"]:
        console.print(Text(f"! {warning}", style="yellow"))


@app.command("upgrade-check")
def upgrade_check(
    target: str = typer.Option(..., "--target", "-t", help="Target Kubernetes version, e.g. 1.36"),
    output: str = OUT,
    from_file: str | None = FROM,
) -> None:
    """What will break when you upgrade: removed APIs, blocking PDBs, node skew, retired components."""
    from kubeoracle.upgrade import upgrade_report

    snap = _snapshot(None, from_file, with_logs=False)
    try:
        data = upgrade_report(snap, state.settings, target)
    except ValueError as exc:
        _fail(str(exc))
    if output == "json":
        print(json.dumps(data, indent=2, default=str))
        return
    ok = data["ready"]
    console.print(Text(f"{data['current']} → {data['target']}: " + ("no blockers found" if ok else
                                                                    f"{len(data['blockers'])} blocker(s)"),
                       style="bold green" if ok else "bold red"))
    from kubeoracle.models import Finding
    from kubeoracle.report import print_findings
    if data["blockers"]:
        print_findings(console, [Finding.model_validate(f) for f in data["blockers"]], title="Blockers")
    if data["warnings"]:
        print_findings(console, [Finding.model_validate(f) for f in data["warnings"]], title="Warnings")
    for n in data["notes"]:
        console.print(Text(f"• {n}", style="yellow"))
    for s in data["steps"]:
        console.print(Text(f"→ {s}", style="dim"))
    if not ok:
        raise typer.Exit(2)


@app.command()
def fix(
    action: str = typer.Argument(..., help="restart | rollback | scale | delete-pod"),
    resource: str = typer.Argument(..., help="kind/name, e.g. deployment/checkout or pod/web-abc"),
    namespace: str = typer.Option("default", "--namespace", "-n"),
    replicas: int | None = typer.Option(None, "--replicas"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply after a successful dry run without asking."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only run the server-side dry run."),
) -> None:
    """Apply a guarded fix: shows the plan, runs a server-side dry run, then asks before changing anything."""
    from kubeoracle.cluster.client import KubeClient, KubeError
    from kubeoracle.remediate import execute, plan

    if "/" not in resource:
        _fail("use kind/name")
    kind, name = resource.split("/", 1)
    if action == "delete-pod":
        kind = "pod"
    s = state.settings
    try:
        client = KubeClient(kubeconfig=s.kubeconfig, context=s.context, timeout=s.request_timeout)
        p = plan(client, action, kind, namespace, name, replicas)
    except (ValueError, KubeError) as exc:
        _fail(str(exc))
    except Exception as exc:
        _fail(f"cannot connect to the cluster: {exc}")
    store = _store()
    console.print(Text(f"Plan: {p.description}", style="bold"))
    console.print(Text(f"Equivalent: {p.kubectl}", style="cyan"))
    console.print(Text(f"Cluster context: {client.context_name}", style="dim"))
    try:
        console.print(Text(f"Dry run: {execute(client, p, True, store)}", style="green"))
    except KubeError as exc:
        _fail(f"dry run failed: {exc.message}")
    if dry_run:
        return
    if not yes and not typer.confirm("Apply this change to the cluster?", default=False):
        console.print("Cancelled.")
        raise typer.Exit(1)
    try:
        console.print(Text(f"Result: {execute(client, p, False, store)}", style="bold green"))
    except KubeError as exc:
        _fail(exc.message)


@snapshot_app.command("save")
def snapshot_save(
    output_file: str = typer.Option("kubeoracle-snapshot.json", "--output", "-o"),
    namespace: str | None = NS,
    logs: bool = typer.Option(True, "--logs/--no-logs", help="Include redacted error lines from failing pods."),
) -> None:
    """Write a redacted snapshot (no Secret values, ConfigMap values or credentials)."""
    snap = _snapshot(namespace, with_logs=logs)
    Path(output_file).write_text(json.dumps(snap.to_dict(), default=str), encoding="utf-8")
    console.print(f"Saved {sum(len(v) for v in snap.objects.values())} objects to {output_file}")


@app.command()
def history(report_id: str | None = typer.Argument(None), limit: int = 20, output: str = OUT) -> None:
    """Past investigations, newest first. Pass an id to show one."""
    store = _store()
    if store is None:
        _fail("history store unavailable")
    if report_id:
        report = store.get_report(report_id)
        if report is None:
            _fail(f"no report {report_id}")
        _emit(report, output, True, True)
        return
    rows = store.list_reports(limit)
    if output == "json":
        print(json.dumps(rows, indent=2))
        return
    t = Table(title="History")
    for col in ("ID", "When (UTC)", "Cluster", "Namespace", "Crit/High", "Summary"):
        t.add_column(col)
    for r in rows:
        c = r["counts"]
        t.add_row(r["id"], r["created"][:16].replace("T", " "), r["cluster"] or "", r["namespace"] or "all",
                  f"{c.get('critical', 0)}/{c.get('high', 0)}", (r["question"] or r["summary"] or "")[:80])
    console.print(t)


@note_app.command("add")
def note_add(
    check_id: str = typer.Argument(..., help="Check id, e.g. pod-oomkilled"),
    note: str = typer.Argument(..., help="What fixed it / what to know"),
    target: str | None = typer.Option(None, "--target", help="Limit to a resource, e.g. Deployment/shop/checkout"),
    author: str | None = typer.Option(None, "--author"),
) -> None:
    """Record a fix. It is shown next to matching findings from now on."""
    from kubeoracle.checks import REGISTRY

    if check_id not in REGISTRY:
        _fail(f"unknown check {check_id}; see `kubeoracle checks`")
    store = _store()
    if store is None:
        _fail("store unavailable")
    store.add_note(check_id, note, target, author)
    console.print("Saved.")


@note_app.command("list")
def note_list(check_id: str | None = typer.Argument(None)) -> None:
    store = _store()
    for n in (store.list_notes(check_id) if store else []):
        console.print(f"[{n['id']}] {n['created'][:10]} {n['check_id']} {n['target'] or ''}: {n['note']}")


@app.command("checks")
def list_checks(output: str = OUT) -> None:
    """List every check with its severity and what it detects."""
    from kubeoracle.checks import REGISTRY

    if output == "json":
        print(json.dumps([{"id": c.id, "title": c.title, "category": c.category.value, "severity": c.severity.value,
                           "type": "risk" if c.risk else "problem", "works_offline": not c.live_only,
                           "description": c.description} for c in REGISTRY.values()], indent=2))
        return
    t = Table(title=f"{len(REGISTRY)} checks", expand=True)
    for col in ("ID", "Category", "Severity", "Type", "Lint", "Detects"):
        t.add_column(col)
    for c in sorted(REGISTRY.values(), key=lambda c: (c.category.value, c.id)):
        t.add_row(c.id, c.category.value, c.severity.value, "risk" if c.risk else "problem",
                  "" if c.live_only else "✓", c.description)
    console.print(t)


@app.command()
def contexts() -> None:
    """List kubeconfig contexts."""
    from kubeoracle.cluster.client import list_contexts

    try:
        items, current = list_contexts(state.settings.kubeconfig)
    except Exception as exc:
        _fail(f"cannot read kubeconfig: {exc}")
    for c in items:
        mark = "*" if c["name"] == current else " "
        console.print(f"{mark} {c['name']}  (cluster {c['cluster']})")


@app.command()
def doctor() -> None:
    """Check connectivity, permissions, metrics and LLM configuration."""
    from kubeoracle.cluster.client import KubeClient
    from kubeoracle.cluster.snapshot import KINDS

    s = state.settings
    ok = True

    def line(good: bool | None, text: str) -> None:
        mark = {True: "[green]✓[/]", False: "[red]✗[/]", None: "[yellow]![/]"}[good]
        console.print(f"{mark} {text}")

    try:
        client = KubeClient(kubeconfig=s.kubeconfig, context=s.context, timeout=s.request_timeout)
        version = client.server_version()
        line(bool(version), f"cluster {client.context_name}: Kubernetes {version or 'unreachable'}")
        ok &= bool(version)
    except Exception as exc:
        line(False, f"kubeconfig/cluster: {exc}")
        raise typer.Exit(1) from None
    if version:
        missing = []
        for key, _, prefix, plural, _, optional in KINDS:
            if optional and key != "secrets":
                continue
            try:
                review = client.request("POST", "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews", body={
                    "apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectAccessReview",
                    "spec": {"resourceAttributes": {"verb": "list", "resource": plural,
                                                    "group": "" if prefix == "/api/v1" else prefix.split("/")[2]}}})
                if not (review.get("status") or {}).get("allowed"):
                    missing.append(plural)
            except Exception:
                missing.append(plural)
        line(not missing or None, "read permissions" + (f": cannot list {', '.join(missing)}" if missing else ": complete"))
        try:
            client.get("/apis/metrics.k8s.io/v1beta1/nodes")
            line(True, "metrics-server available (cost and HPA checks)")
        except Exception:
            line(None, "metrics-server not found: cost report and right-sizing will be limited")
    provider = s.resolved_provider()
    if provider == "none":
        line(None, "no LLM configured: rule-based diagnosis only (set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
                   "OPENROUTER_API_KEY or KUBEORACLE_LLM_PROVIDER=ollama)")
    else:
        line(True, f"LLM: {provider} / {s.resolved_model()}")
    line(True if s.prometheus_url else None, f"Prometheus: {s.prometheus_url or 'not configured (optional)'}")
    if not ok:
        raise typer.Exit(1)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8080, "--port"),
) -> None:
    """Run the web dashboard and REST API (set KUBEORACLE_API_TOKEN when exposing it)."""
    try:
        import uvicorn

        from kubeoracle.server.app import create_app
    except ImportError:
        _fail("install the server extra: pip install 'kubeoracle[server]'")
    s = state.settings
    if host not in ("127.0.0.1", "localhost") and not s.api_token:
        _fail("refusing to listen on a non-local address without KUBEORACLE_API_TOKEN")
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(s), host=host, port=port, log_level="info")


@app.command()
def mcp(
    transport: str = typer.Option("stdio", "--transport", help="stdio | streamable-http"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
) -> None:
    """Run as an MCP server so AI assistants (Claude, Cursor, VS Code…) can investigate the cluster."""
    try:
        from kubeoracle.mcp_server import build_server
    except ImportError:
        _fail("install the MCP extra: pip install 'kubeoracle[mcp]'")
    server = build_server(state.settings)
    if transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=host, port=port)


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"kubeoracle {__version__}")


def main() -> Any:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
            with contextlib.suppress(AttributeError, ValueError):
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    try:
        return app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
