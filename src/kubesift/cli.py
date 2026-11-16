"""Command line interface. Also installed as `kubectl-sift`, so `kubectl sift ...` works."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from kubesift import __version__
from kubesift.config import Settings, load_settings
from kubesift.models import Report, Severity

app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich", add_completion=True,
                  help="KubeSift: find out what is broken in Kubernetes, why, and what changed.")
note_app = typer.Typer(help="Team notes: record what fixed a problem so the next person sees it.")
snapshot_app = typer.Typer(help="Save a redacted cluster snapshot to analyse offline or attach to a bug report.")
drill_app = typer.Typer(help="Fire drills: break a dedicated namespace on purpose to practise on-call.")
knowledge_app = typer.Typer(help="Knowledge packs: share what fixed past incidents between teams and clusters.")
app.add_typer(note_app, name="note")
app.add_typer(snapshot_app, name="snapshot")
app.add_typer(drill_app, name="drill")
app.add_typer(knowledge_app, name="knowledge")

console = Console()
err = Console(stderr=True)


class State:
    settings: Settings


state = State()


def _print_version(value: bool) -> None:
    if value:
        from kubesift import __version__

        print(f"kubesift {__version__}")
        raise typer.Exit()


@app.callback()
def main_options(
    kubeconfig: str | None = typer.Option(None, "--kubeconfig", envvar="KUBECONFIG_PATH", help="Path to kubeconfig."),
    context: str | None = typer.Option(None, "--context", help="Kubeconfig context to use."),
    llm: str | None = typer.Option(None, "--llm-provider", help="anthropic | openai | openrouter | ollama | none"),
    model: str | None = typer.Option(None, "--model", help="LLM model name."),
    prometheus: str | None = typer.Option(None, "--prometheus", help="Prometheus URL for usage history."),
    version: bool = typer.Option(False, "--version", "-V", is_eager=True, callback=_print_version,
                                 help="Show the version and exit."),
) -> None:
    state.settings = load_settings(kubeconfig=kubeconfig, context=context, llm_provider=llm, llm_model=model,
                                   prometheus_url=prometheus)


def _fail(msg: str, code: int = 1) -> NoReturn:
    err.print(Text(f"error: {msg}", style="bold red"))
    raise typer.Exit(code)


def _load_snapshot_file(path: str):
    from kubesift.cluster.snapshot import Snapshot

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail(f"snapshot file not found: {path}")
    except (OSError, UnicodeDecodeError) as exc:
        _fail(f"cannot read {path}: {exc}")
    except json.JSONDecodeError as exc:
        _fail(f"{path} is not a KubeSift snapshot (invalid JSON: {exc})")
    if not isinstance(data, dict) or "objects" not in data:
        _fail(f"{path} is not a KubeSift snapshot (create one with: kubesift snapshot save)")
    return Snapshot.from_dict(data)


def _llm_status(text: str, use_llm: bool) -> str:
    if use_llm and state.settings.resolved_provider() == "ollama":
        return f"{text} (local model: this can take several minutes without a GPU)"
    return text


def _snapshot(namespace: str | None, from_file: str | None = None, with_logs: bool = True):

    if from_file:
        from kubesift.util import analyse_as_of

        snap = _load_snapshot_file(from_file)
        if namespace:
            snap.namespace = namespace
        # "Pending for 3m" must stay 3m when the file is read days later, and a certificate with 3 days left must
        # not show as expired: judge the snapshot as of the moment it was taken.
        analyse_as_of(snap.collected_at)
        return snap
    from kubesift.analysis.engine import snapshot_for

    try:
        with err.status("Reading cluster state…"):
            return snapshot_for(state.settings, namespace, with_logs=with_logs)
    except Exception as exc:  # kubeconfig missing, cluster unreachable, login rejected
        from kubesift.cluster.client import ClusterAccessError

        _fail(str(exc) if isinstance(exc, ClusterAccessError) else f"cannot connect to the cluster: {exc}")


def _store():
    from kubesift.store import open_store

    try:
        return open_store(state.settings)
    except Exception:
        return None


def _exit_on(report_findings: list, fail_on: str | None) -> None:
    if not fail_on:
        return
    threshold = Severity.parse(fail_on).rank
    if any(f.severity.rank >= threshold for f in report_findings):
        raise typer.Exit(2)


def _emit(report: Report, output: str, risks: bool, verbose: bool) -> None:
    from kubesift.report import print_report, to_markdown

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
    from kubesift.analysis.engine import investigate
    from kubesift.util import parse_duration

    snap = _snapshot(namespace, from_file)
    store = _store()
    with err.status(_llm_status("Analysing…", llm)):
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
    from kubesift.analysis.engine import investigate

    snap = _snapshot(namespace, from_file, with_logs=False)
    report = investigate(state.settings, snap, use_llm=False)
    report.findings = [f for f in report.findings if f.kind == "risk"]
    report.incidents = []
    report.diagnosis = None
    if output == "text":
        from kubesift.report import print_findings
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
    from kubesift.analysis.engine import ask as do_ask
    from kubesift.analysis.engine import mentioned_namespace

    q = " ".join(question)
    snap = _snapshot(namespace, from_file)
    if not namespace:
        ns = mentioned_namespace(q, [n.get("metadata", {}).get("name", "") for n in snap.all("namespaces")])
        if ns:
            snap.namespace = ns
    with err.status(_llm_status("Thinking…", llm)):
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
        from kubesift.report import print_findings
        print_findings(console, report.findings, title="Supporting findings")


@app.command()
def changes(
    namespace: str | None = NS,
    since: str = typer.Option("24h", "--since", help="Window, e.g. 30m, 6h, 2d."),
    output: str = OUT,
    from_file: str | None = FROM,
) -> None:
    """What changed recently: rollouts, config edits, Helm upgrades, Argo CD syncs, scaling, nodes."""
    from kubesift.analysis.changes import detect_changes
    from kubesift.report import print_changes
    from kubesift.util import parse_duration

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
    from kubesift.analysis.engine import explain_resource

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
    from kubesift.models import Finding
    from kubesift.report import print_findings
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
    from kubesift.lint import LintError
    from kubesift.lint import lint as do_lint
    from kubesift.report import print_findings, to_sarif

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
    from kubesift.cost import cost_report

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
    from kubesift.upgrade import upgrade_report

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
    from kubesift.models import Finding
    from kubesift.report import print_findings
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
    from kubesift.cluster.client import KubeClient, KubeError
    from kubesift.remediate import execute, plan

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
    output_file: str = typer.Option("kubesift-snapshot.json", "--output", "-o"),
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
    from kubesift.checks import REGISTRY

    if check_id not in REGISTRY:
        _fail(f"unknown check {check_id}; see `kubesift checks`")
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
    from kubesift.checks import REGISTRY

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
    from kubesift.cluster.client import CONNECT_HINT, ClusterAccessError, list_contexts

    try:
        items, current = list_contexts(state.settings.kubeconfig)
    except ClusterAccessError as exc:
        _fail(str(exc))
    except Exception as exc:
        _fail(f"cannot read kubeconfig: {exc}")
    if not items:
        console.print(f"No contexts in the kubeconfig. To add one, {CONNECT_HINT}.")
    elif not current:
        console.print("No current context: pick one with --context NAME or kubectl config use-context NAME.")
    for c in items:
        mark = "*" if c["name"] == current else " "
        console.print(f"{mark} {c['name']}  (cluster {c['cluster']})")


@app.command()
def doctor() -> None:
    """Check connectivity, permissions, metrics and LLM configuration."""
    from kubesift.cluster.client import KubeClient
    from kubesift.cluster.snapshot import KINDS

    s = state.settings
    ok = True

    def line(good: bool | None, text: str) -> None:
        mark = {True: "[green]✓[/]", False: "[red]✗[/]", None: "[yellow]![/]"}[good]
        console.print(f"{mark} {text}")

    connected = False
    try:
        client = KubeClient(kubeconfig=s.kubeconfig, context=s.context, timeout=s.request_timeout)
        version = client.check_connection()
        connected = True
        line(True, f"cluster {client.context_name}: Kubernetes {version or '(version hidden)'}")
    except Exception as exc:  # unreachable, login rejected, bad kubeconfig: still check the rest
        ok = False
        line(False, str(exc))
    if connected:
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
                   "OPENROUTER_API_KEY or KUBESIFT_LLM_PROVIDER=ollama)")
    elif provider == "ollama":
        from kubesift.analysis.llm import OllamaProvider

        ready, detail = OllamaProvider(s).status()
        line(True if ready else None, f"LLM: {detail}")
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
    """Run the web dashboard and REST API (set KUBESIFT_API_TOKEN when exposing it)."""
    try:
        import uvicorn

        from kubesift.server.app import create_app
    except ImportError:
        _fail("install the server extra: pip install 'kubesift[server]'")
    s = state.settings
    if host not in ("127.0.0.1", "localhost") and not s.api_token:
        _fail("refusing to listen on a non-local address without KUBESIFT_API_TOKEN")
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
        from kubesift.mcp_server import build_server
    except ImportError:
        _fail("install the MCP extra: pip install 'kubesift[mcp]'")
    s = state.settings
    if transport == "stdio":
        build_server(s).run("stdio")
        return
    if host not in ("127.0.0.1", "localhost") and not s.api_token:
        _fail("refusing to serve MCP on a non-local address without KUBESIFT_API_TOKEN")
    import uvicorn

    from kubesift.mcp_server import build_http_app
    _, http_app = build_http_app(s)
    err.print(f"MCP endpoint: http://{host}:{port}/mcp" + (" (bearer token required)" if s.api_token else ""))
    uvicorn.run(http_app, host=host, port=port, log_level="warning")


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"kubesift {__version__}")


@app.command()
def preflight(
    paths: list[str] = typer.Argument(..., help="Manifests, Helm chart or Kustomize dir, or - for stdin."),
    namespace: str = typer.Option("default", "--namespace", "-n", help="Namespace for objects without one."),
    values: list[str] = typer.Option([], "--values", "-f", help="Helm values files."),
    server_dry_run: bool = typer.Option(False, "--server-dry-run", help="Also ask the API server (webhooks, Pod Security, quota)."),
    check_images: bool = typer.Option(False, "--check-images", help="Check that image tags exist in their registries."),
    output: str = typer.Option("text", "--output", "-o", help="text | json | markdown"),
    fail_on: str | None = typer.Option("high", "--fail-on", help="Exit 2 at or above this severity (none to disable)."),
    from_file: str | None = FROM,
) -> None:
    """Check a change against the LIVE cluster before deploying: missing secrets, capacity, risky diffs, OOM risk."""
    from kubesift.cluster.client import KubeClient
    from kubesift.lint import LintError, load_documents
    from kubesift.preflight import preflight as run_preflight

    try:
        docs = load_documents(paths, values)
    except LintError as exc:
        _fail(str(exc))
    snap = _snapshot(None, from_file, with_logs=False)
    s = state.settings
    client = KubeClient(kubeconfig=s.kubeconfig, context=s.context, timeout=s.request_timeout) if server_dry_run else None
    with err.status("Comparing with the live cluster…"):
        res = run_preflight(docs, snap, s, default_namespace=namespace, client=client,
                            server_dry_run=server_dry_run, check_images=check_images)
    if output == "json":
        print(json.dumps(res.to_dict(), indent=2, default=str))
    elif output in ("md", "markdown"):
        print(_preflight_markdown(res))
    else:
        from kubesift.report import print_findings
        console.print(Text(f"Preflight against {res.target} ({res.server_version or '?'})", style="bold"))
        for c in res.changes:
            if c["action"] != "unchanged":
                detail = f": {'; '.join(c['details'][:4])}" if c["details"] else ""
                console.print(Text(f"  {c['action']:7} {c['resource']}{detail}",
                                   style="cyan" if c["action"] == "create" else "magenta"))
        print_findings(console, res.findings, title="Preflight findings")
    _exit_on(res.findings, None if (fail_on or "").lower() == "none" else fail_on)


def _preflight_markdown(res: Any) -> str:
    blockers = [f for f in res.findings if f.severity.rank >= 3]
    verdict = f"❌ {len(blockers)} blocker(s)" if blockers else "✅ no blockers"
    lines = [f"### KubeSift preflight: {verdict}",
             f"Target cluster `{res.target}` ({res.server_version or 'unknown version'})", ""]
    changed = [c for c in res.changes if c["action"] != "unchanged"]
    if changed:
        lines += ["**Changes**", ""]
        for c in changed:
            detail = f" — {'; '.join(c['details'][:4])}" if c["details"] else ""
            lines.append(f"- `{c['action']}` {c['resource']}{detail}")
        lines.append("")
    if res.findings:
        lines += ["| Severity | Check | Resource | Finding |", "|---|---|---|---|"]
        lines += [f"| {f.severity.value} | {f.check} | `{f.resource}` | {f.message.replace('|', '/')} |"
                  for f in res.findings]
    return "\n".join(lines)


@app.command()
def diff(
    since: str | None = typer.Option(None, "--since", help="Compare now with the cluster as it was this long ago (e.g. 2h)."),
    from_file: str | None = typer.Option(None, "--from", help="Earlier snapshot file (from `snapshot save`)."),
    to_file: str | None = typer.Option(None, "--to", help="Later snapshot file (default: live cluster)."),
    namespace: str | None = NS,
    output: str = OUT,
) -> None:
    """Time travel: what is different between two moments (stored history or snapshot files)."""
    from kubesift.timetravel import diff as do_diff
    from kubesift.timetravel import digest
    from kubesift.util import now, parse_duration

    later = _load_snapshot_file(to_file) if to_file else _snapshot(namespace, with_logs=False)
    if from_file:
        earlier = digest(_load_snapshot_file(from_file))
    elif since:
        store = _store()
        earlier = store.digest_before(later.context, now() - parse_duration(since)) if store else None
        if earlier is None:
            _fail(f"no stored history for {later.context} from {since} ago. History is recorded by "
                  "`kubesift snapshot record` and by the in-cluster service's background scans.")
    else:
        _fail("use --since 2h (stored history) or --from snapshot.json")
    current = digest(later)
    items = do_diff(earlier, current)
    if namespace:
        items = [i for i in items if f"/{namespace}/" in i["resource"] or i["kind"] == "Node"]
    if output == "json":
        print(json.dumps({"from": earlier.get("time"), "to": current.get("time"), "changes": items}, indent=2))
        return
    console.print(Text(f"{str(earlier.get('time', '?'))[:16]} → {str(current.get('time', '?'))[:16]} UTC · "
                       f"{len(items)} difference(s)", style="bold"))
    style = {"added": "green", "removed": "red", "changed": "yellow"}
    for i in items:
        console.print(Text(f"  {i['change']:8} {i['resource']}", style=style[i["change"]]))
        for d in i["details"][:6]:
            console.print(Text(f"           {d}", style="dim"))


@snapshot_app.command("record")
def snapshot_record(namespace: str | None = NS) -> None:
    """Store a compact digest of the cluster now, for `kubesift diff --since` later."""
    from kubesift.timetravel import digest

    snap = _snapshot(namespace, with_logs=False)
    store = _store()
    if store is None:
        _fail("history store unavailable")
    stored = store.save_digest(digest(snap))
    console.print("Recorded." if stored else "Nothing changed since the last record.")


@app.command()
def postmortem(
    namespace: str | None = NS,
    since: str = typer.Option("24h", "--since", help="How far back the incident window goes."),
    report_id: str | None = typer.Option(None, "--report", help="Build from a stored investigation instead of now."),
    output: str = typer.Option("markdown", "--output", "-o", help="markdown | json"),
    llm: bool = LLM,
    from_file: str | None = FROM,
) -> None:
    """Draft a blameless postmortem: impact, timeline, root cause, triggering change, action items."""
    from kubesift.analysis.engine import investigate
    from kubesift.postmortem import build, to_markdown
    from kubesift.util import parse_duration

    store = _store()
    snap = None
    if report_id:
        report = store.get_report(report_id) if store else None
        if report is None:
            _fail(f"no report {report_id}")
    else:
        snap = _snapshot(namespace, from_file)
        report = investigate(state.settings, snap, use_llm=llm, window=parse_duration(since), store=store)
    pm = build(report, snap, store.list_audit(200) if store else [], state.settings if llm else None)
    print(json.dumps(pm, indent=2) if output == "json" else to_markdown(pm))


@drill_app.command("list")
def drill_list() -> None:
    """Available drills."""
    from kubesift.drills import DRILLS

    t = Table(title="Drills")
    for col in ("ID", "Scenario", "What the on-call engineer sees"):
        t.add_column(col)
    for d in DRILLS.values():
        t.add_row(d.id, d.title, d.story)
    console.print(t)


@drill_app.command("start")
def drill_start(
    drill_id: str = typer.Argument(..., help="Drill id from `kubesift drill list`."),
    namespace: str = typer.Option("kubesift-drill", "--namespace", "-n"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Break a drill namespace on purpose. Only touches namespaces labelled kubesift.dev/drill=true."""
    from kubesift.cluster.client import KubeClient, KubeError
    from kubesift.drills import DRILLS, start

    if drill_id not in DRILLS:
        _fail(f"unknown drill {drill_id}; see `kubesift drill list`")
    s = state.settings
    client = KubeClient(kubeconfig=s.kubeconfig, context=s.context, timeout=s.request_timeout)
    if not yes and not typer.confirm(f"Create broken workloads in namespace '{namespace}' on {client.context_name}?"):
        raise typer.Exit(1)
    try:
        with err.status("Setting up the incident…"):
            d = start(client, drill_id, namespace)
    except (KubeError, ValueError) as exc:
        _fail(str(exc))
    console.print(Text(f"Incident started: {d.title}", style="bold red"))
    console.print(d.story)
    console.print(Text(f"Investigate it yourself first. Hint: `kubesift drill hint {d.id}`. Answer: "
                       f"`kubesift drill reveal {d.id} -n {namespace}`. Clean up: `kubesift drill stop -n {namespace}`.",
                       style="dim"))


@drill_app.command("hint")
def drill_hint(drill_id: str) -> None:
    """A nudge without giving the answer away."""
    from kubesift.drills import DRILLS

    console.print(DRILLS[drill_id].hint if drill_id in DRILLS else "unknown drill")


@drill_app.command("reveal")
def drill_reveal(drill_id: str, namespace: str = typer.Option("kubesift-drill", "--namespace", "-n")) -> None:
    """Show the answer and whether KubeSift caught it."""
    from kubesift.analysis.engine import investigate
    from kubesift.drills import DRILLS

    d = DRILLS.get(drill_id)
    if d is None:
        _fail("unknown drill")
    report = investigate(state.settings, _snapshot(namespace), use_llm=False)
    caught = [f for f in report.findings if f.check in d.expect]
    console.print(Text(f"Answer: {d.answer}", style="bold green"))
    if caught:
        console.print(Text(f"KubeSift caught it: {caught[0].message}", style="green"))
        if caught[0].related_changes:
            console.print(Text(f"Triggered by: {caught[0].related_changes[0].summary}", style="magenta"))
    else:
        console.print(Text("KubeSift has not detected it yet (some failures take a minute or two to appear).",
                           style="yellow"))


@drill_app.command("stop")
def drill_stop(namespace: str = typer.Option("kubesift-drill", "--namespace", "-n")) -> None:
    """Delete the drill namespace (refuses any namespace not created for drills)."""
    from kubesift.cluster.client import KubeClient, KubeError
    from kubesift.drills import stop

    s = state.settings
    try:
        stop(KubeClient(kubeconfig=s.kubeconfig, context=s.context, timeout=s.request_timeout), namespace)
    except (KubeError, ValueError) as exc:
        _fail(str(exc))
    console.print(f"Deleted drill namespace {namespace}.")


@knowledge_app.command("export")
def knowledge_export(output_file: str = typer.Option("kubesift-knowledge.yaml", "--output", "-o"),
                     check_id: str | None = typer.Option(None, "--check")) -> None:
    """Write team notes and runbooks to a YAML pack you can review and share."""
    import yaml

    from kubesift.knowledge import export_pack

    store = _store()
    if store is None:
        _fail("store unavailable")
    pack = export_pack(store, state.settings.runbooks_dir, check_id)
    Path(output_file).write_text(yaml.safe_dump(pack, sort_keys=False, allow_unicode=True), encoding="utf-8")
    console.print(f"Exported {len(pack['notes'])} notes and {len(pack['runbooks'])} runbooks to {output_file}")


@knowledge_app.command("import")
def knowledge_import(pack_file: str) -> None:
    """Add notes (and runbooks, if runbooks_dir is set) from a knowledge pack. Duplicates are skipped."""
    import yaml

    from kubesift.knowledge import import_pack

    store = _store()
    if store is None:
        _fail("store unavailable")
    try:
        pack = yaml.safe_load(Path(pack_file).read_text(encoding="utf-8")) or {}
        result = import_pack(store, pack, state.settings.runbooks_dir)
    except FileNotFoundError:
        _fail(f"knowledge pack not found: {pack_file}")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        _fail(f"cannot import {pack_file}: {exc}")
    console.print(f"Added {result['notes_added']} notes, skipped {result['notes_skipped']}, "
                  f"wrote {result['runbooks_written']} runbooks.")


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
