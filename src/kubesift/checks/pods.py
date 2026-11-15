"""Pod runtime failures: crash loops, OOM kills, image pulls, pending pods and more."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from kubesift.checks.base import Context, check
from kubesift.models import Category, Finding, Severity
from kubesift.util import age, containers_of, fmt_bytes, human_duration, meta, parse_quantity, parse_time

EXIT_CODES = {
    0: "exited successfully, but the pod expects a long-running process (restartPolicy: Always)",
    1: "application error (unhandled exception or failed startup check)",
    2: "misuse of shell builtin or invalid arguments",
    126: "command found but not executable (permissions or wrong file)",
    127: "command not found (wrong command/entrypoint or missing binary in the image)",
    128: "invalid exit argument",
    134: "aborted (SIGABRT), often an assertion or fatal runtime error",
    137: "killed with SIGKILL (out of memory, or killed after a failed liveness probe)",
    139: "segmentation fault (SIGSEGV)",
    143: "terminated with SIGTERM (shutdown, eviction or failed liveness probe)",
}

LOG_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'is forbidden: User "system:serviceaccount:[^"]+"', re.I),
     "The app calls the Kubernetes API and its ServiceAccount is not allowed to: add a Role/RoleBinding for the "
     "verb and resource named in the log line."),
    (re.compile(r"exec format error", re.I),
     "The image was built for a different CPU architecture (e.g. arm64 image on amd64 nodes). "
     "Rebuild with `docker buildx build --platform linux/amd64,linux/arm64`."),
    (re.compile(r"KeyError: '([A-Z][A-Z0-9_]+)'|environment variable \"?([A-Z][A-Z0-9_]+)\"? (?:is )?(?:not set|missing|required|undefined)|([A-Z][A-Z0-9_]{2,}) (?:must be set|is required|is not set|not defined)", re.I),
     "The application expects environment variable {0}, which is not set on the container."),
    (re.compile(r"(?:connection refused|ECONNREFUSED)[^\n]*?(\b[\w.-]+:\d{2,5}\b)?", re.I),
     "A dependency{0} refused the connection: it is down, not ready yet, or listening on another port."),
    (re.compile(r"no such host|Name or service not known|Temporary failure in name resolution|could not resolve host|getaddrinfo ENOTFOUND", re.I),
     "DNS lookup failed: the service name is wrong, in another namespace (use name.namespace.svc), or DNS/egress is blocked."),
    (re.compile(r"Read-only file system", re.I),
     "The app writes to a read-only filesystem; mount an emptyDir for that path or relax readOnlyRootFilesystem."),
    (re.compile(r"permission denied", re.I),
     "Permission denied: check runAsUser/fsGroup and file ownership in the image."),
    (re.compile(r"address already in use|EADDRINUSE", re.I),
     "The port is already in use: two processes or containers bind the same port."),
    (re.compile(r"java\.lang\.OutOfMemoryError|JavaScript heap out of memory|MemoryError", re.I),
     "The runtime ran out of heap: raise the memory limit and set the heap size relative to it (e.g. -XX:MaxRAMPercentage=75)."),
    (re.compile(r"x509: certificate|certificate verify failed|SSL: CERTIFICATE_VERIFY_FAILED|certificate has expired", re.I),
     "TLS verification failed: an expired certificate or a missing CA bundle in the image."),
    (re.compile(r"(?:password authentication failed|Access denied for user|authentication failed|401 Unauthorized)", re.I),
     "A dependency rejected the credentials: the Secret value is wrong or was rotated."),
    (re.compile(r"no such file or directory", re.I),
     "A file or binary the container needs does not exist: check the command, working dir and volume mounts."),
]


def log_hints(ctx: Context, ns: str, pod: str, container: str) -> tuple[list[str], list[str]]:
    entry = ctx.snap.logs.get(f"{ns}/{pod}/{container}")
    if not entry:
        return [], []
    lines = entry.get("errors") or entry.get("tail") or []
    text = "\n".join(lines)
    hints = []
    for pattern, template in LOG_HINTS:
        m = pattern.search(text)
        if not m:
            continue
        group = next((g for g in m.groups() if g), "")
        if "{0}" not in template:
            hints.append(template)
        elif template.startswith("The application"):
            hints.append(template.format(group or "(name not found in logs)"))
        else:
            hints.append(template.format(f" ({group})" if group else ""))
    label = "previous run" if entry.get("previous") else "current run"
    evidence = [f"log ({label}): {ln}" for ln in lines[-5:]]
    return hints, evidence


def _statuses(pod: dict[str, Any]) -> Iterator[tuple[dict[str, Any], bool]]:
    status = pod.get("status") or {}
    for cs in status.get("initContainerStatuses") or []:
        yield cs, True
    for cs in status.get("containerStatuses") or []:
        yield cs, False


def _container_spec(pod: dict[str, Any], name: str) -> dict[str, Any]:
    for c in containers_of(pod.get("spec"), include_init=True):
        if c.get("name") == name:
            return c
    return {}


def _pod_cmd(pod: dict[str, Any], what: str = "describe") -> str:
    return f"kubectl {what} pod {meta(pod).get('name')} -n {meta(pod).get('namespace')}"


def is_crash_looping(cs: dict[str, Any]) -> bool:
    """True for CrashLoopBackOff, and also between back-offs, when the container briefly shows
    Error/Completed or Running again (what `kubectl get pods` prints as Error or Running with restarts)."""
    state = cs.get("state") or {}
    if (state.get("waiting") or {}).get("reason") == "CrashLoopBackOff":
        return True
    restarts = cs.get("restartCount", 0)
    current = state.get("terminated") or {}
    if current and current.get("exitCode", 0) != 0 and restarts >= 1:
        return True
    last = (cs.get("lastState") or {}).get("terminated") or {}
    if restarts >= 3 and last and last.get("exitCode", 0) != 0:
        a = age(last.get("finishedAt"))
        return a is not None and a < timedelta(minutes=10)
    return False


@check("pod-crashloop", "Container is crash looping", Category.WORKLOAD, Severity.CRITICAL)
def crashloop(ctx: Context) -> Iterator[Finding]:
    """A container keeps exiting with an error and Kubernetes keeps restarting it."""
    for pod in ctx.snap.all("pods"):
        if meta(pod).get("deletionTimestamp"):
            continue
        for cs, is_init in _statuses(pod):
            if not is_crash_looping(cs):
                continue
            last = (cs.get("lastState") or {}).get("terminated") or (cs.get("state") or {}).get("terminated") or {}
            code = last.get("exitCode")
            reason = last.get("reason", "")
            if reason == "OOMKilled":
                continue  # reported by pod-oomkilled with better advice
            name = cs.get("name", "")
            ns, pname = meta(pod).get("namespace"), meta(pod).get("name")
            hints, log_ev = log_hints(ctx, ns, pname, name)
            evidence = [f"container {name}{' (init)' if is_init else ''}: restarts={cs.get('restartCount', 0)}"]
            if code is not None:
                evidence.append(f"last exit code {code}: {EXIT_CODES.get(code, 'application-specific error')}")
            if last.get("message"):
                evidence.append(f"termination message: {last['message'][:300]}")
            evidence += log_ev
            if code == 137 and _liveness_killed(ctx, pod):
                hints.insert(0, "The liveness probe is failing and the kubelet kills the container; "
                                "the app may be slow to start (add a startupProbe) or the probe checks the wrong port/path.")
            msg = f"Container '{name}' keeps crashing ({cs.get('restartCount', 0)} restarts)"
            msg += f" (exit code {code})." if code is not None else "."
            if hints:
                msg += " " + hints[0]
            yield ctx.finding(
                "pod-crashloop", pod, msg,
                evidence=evidence,
                remediation=" ".join(hints[1:3]) or "Read the previous logs to see why the process exits, fix the cause, then redeploy.",
                commands=[f"kubectl logs {pname} -n {ns} -c {name} --previous --tail=100", _pod_cmd(pod)],
                since=parse_time(last.get("startedAt")) or parse_time((pod.get("status") or {}).get("startTime")),
                node=(pod.get("spec") or {}).get("nodeName"),
            )


def _liveness_killed(ctx: Context, pod: dict[str, Any]) -> bool:
    for e in ctx.snap.events_for("Pod", meta(pod).get("namespace"), meta(pod).get("name", "")):
        if "Liveness probe failed" in (e.get("message") or ""):
            return True
    return False


@check("pod-oomkilled", "Container was killed for using too much memory", Category.RESOURCES, Severity.HIGH)
def oomkilled(ctx: Context) -> Iterator[Finding]:
    """The kernel killed a container because it exceeded its memory limit."""
    for pod in ctx.snap.all("pods"):
        for cs, _ in _statuses(pod):
            last = (cs.get("lastState") or {}).get("terminated") or {}
            cur = (cs.get("state") or {}).get("terminated") or {}
            term = last if last.get("reason") == "OOMKilled" else cur if cur.get("reason") == "OOMKilled" else None
            if not term:
                continue
            name = cs.get("name", "")
            spec = _container_spec(pod, name)
            limit = parse_quantity(((spec.get("resources") or {}).get("limits") or {}).get("memory"))
            ns, pname = meta(pod).get("namespace"), meta(pod).get("name")
            usage = ctx.snap.metrics.get("pods", {}).get(f"{ns}/{pname}/{name}", {}).get("memory")
            evidence = [f"container {name}: restarts={cs.get('restartCount', 0)}, last killed at {term.get('finishedAt')}"]
            if limit:
                evidence.append(f"memory limit {fmt_bytes(limit)}")
            if usage:
                evidence.append(f"current memory use {fmt_bytes(usage)}")
            hints, log_ev = log_hints(ctx, ns, pname, name)
            suggestion = fmt_bytes(limit * 2) if limit else "a value above peak usage"
            waiting = ((cs.get("state") or {}).get("waiting") or {}).get("reason") == "CrashLoopBackOff"
            owner = ctx.snap.owner_of(pod)
            yield ctx.finding(
                "pod-oomkilled", pod,
                f"Container '{name}' was OOMKilled"
                + (f": it needs more than its {fmt_bytes(limit)} memory limit." if limit else ".")
                + (" It is now crash looping." if waiting else ""),
                severity=Severity.CRITICAL if waiting else Severity.HIGH,
                evidence=evidence + log_ev,
                remediation=f"Raise the memory limit (try {suggestion}) and set the request close to real usage, "
                            "or fix a memory leak. For JVM/Node apps also cap the heap below the limit.",
                commands=[
                    f"kubectl set resources {owner.kind.lower()}/{owner.name} -n {ns} -c {name} --limits=memory={suggestion}"
                    if owner.kind in ("Deployment", "StatefulSet", "DaemonSet") and limit else _pod_cmd(pod),
                    f"kubectl top pod {pname} -n {ns} --containers",
                ],
                since=parse_time(term.get("finishedAt")),
                node=(pod.get("spec") or {}).get("nodeName"),
            )


_PULL_HINTS = [
    (re.compile(r"toomanyrequests|rate limit", re.I),
     "Docker Hub rate limit reached. Authenticate pulls with an imagePullSecret, use a pull-through cache, "
     "or mirror the image to your own registry.", Severity.HIGH),
    (re.compile(r"not found|manifest unknown|does not exist", re.I),
     "The image or tag does not exist. Check the spelling and that the tag was pushed.", Severity.CRITICAL),
    (re.compile(r"unauthorized|authentication required|denied|403 Forbidden", re.I),
     "The registry rejected the pull: add or fix the imagePullSecret, or grant the node role access to the registry.",
     Severity.CRITICAL),
    (re.compile(r"no such host|i/o timeout|dial tcp|connection refused|TLS handshake timeout", re.I),
     "Nodes cannot reach the registry: check egress firewall/NAT, proxy settings and DNS.", Severity.CRITICAL),
    (re.compile(r"no match for platform", re.I),
     "The image has no build for the node's CPU architecture.", Severity.CRITICAL),
]


@check("pod-image-pull", "Image cannot be pulled", Category.WORKLOAD, Severity.CRITICAL)
def image_pull(ctx: Context) -> Iterator[Finding]:
    """ErrImagePull / ImagePullBackOff / InvalidImageName."""
    for pod in ctx.snap.all("pods"):
        for cs, _ in _statuses(pod):
            waiting = (cs.get("state") or {}).get("waiting") or {}
            reason = waiting.get("reason", "")
            if reason not in ("ErrImagePull", "ImagePullBackOff", "InvalidImageName", "ErrImageNeverPull"):
                continue
            ns, pname = meta(pod).get("namespace"), meta(pod).get("name", "")
            messages = [waiting.get("message", "")] + [
                e.get("message", "") for e in ctx.snap.events_for("Pod", ns, pname) if e.get("reason") in ("Failed", "BackOff")
            ]
            text = "\n".join(m for m in messages if m)
            advice, sev = "Check the image name, tag and registry credentials.", Severity.CRITICAL
            for pattern, hint, s in _PULL_HINTS:
                if pattern.search(text):
                    advice, sev = hint, s
                    break
            image = cs.get("image") or _container_spec(pod, cs.get("name", "")).get("image", "?")
            yield ctx.finding(
                "pod-image-pull", pod,
                f"Image '{image}' for container '{cs.get('name')}' cannot be pulled ({reason}). {advice}",
                severity=sev,
                evidence=[m[:300] for m in messages if m][:3],
                remediation=advice,
                commands=[_pod_cmd(pod), f"kubectl get events -n {ns} --field-selector involvedObject.name={pname}"],
                since=parse_time(meta(pod).get("creationTimestamp")),
                node=(pod.get("spec") or {}).get("nodeName"),
            )


@check("pod-config-error", "Container cannot start: configuration error", Category.CONFIG, Severity.CRITICAL)
def config_error(ctx: Context) -> Iterator[Finding]:
    """CreateContainerConfigError / CreateContainerError / RunContainerError."""
    for pod in ctx.snap.all("pods"):
        for cs, _ in _statuses(pod):
            waiting = (cs.get("state") or {}).get("waiting") or {}
            reason = waiting.get("reason", "")
            if reason not in ("CreateContainerConfigError", "CreateContainerError", "RunContainerError",
                              "ContainerCannotRun"):
                continue
            msg = waiting.get("message", "")
            advice = "Fix the referenced configuration and the pod will start on its own."
            if "secret" in msg.lower():
                advice = "Create the missing Secret/key (or mark the reference optional), in the same namespace."
            elif "configmap" in msg.lower():
                advice = "Create the missing ConfigMap/key (or mark the reference optional), in the same namespace."
            elif "executable file not found" in msg or "no such file" in msg:
                advice = "The container command does not exist in the image: fix `command`/`args` or the image entrypoint."
            yield ctx.finding(
                "pod-config-error", pod,
                f"Container '{cs.get('name')}' cannot start ({reason}): {msg[:200]}",
                evidence=[msg[:400]] if msg else [],
                remediation=advice,
                commands=[_pod_cmd(pod)],
                since=parse_time(meta(pod).get("creationTimestamp")),
            )


_SCHED_HINTS = [
    (re.compile(r"Insufficient (cpu|memory|ephemeral-storage|nvidia\.com/gpu|[\w./-]+)"),
     "Not enough free {0} on any node. Lower the request, remove idle workloads, or add nodes (check the cluster autoscaler)."),
    (re.compile(r"untolerated taint|had taint|taint\(s\)"),
     "All candidate nodes have taints the pod does not tolerate. Add a toleration or schedule on untainted nodes."),
    (re.compile(r"didn't match Pod's node affinity|node affinity/selector|node\(s\) didn't match node selector"),
     "No node matches the pod's nodeSelector/affinity. Check node labels and the selector."),
    (re.compile(r"unbound immediate PersistentVolumeClaims|persistentvolumeclaim .* not found|volume node affinity conflict"),
     "A volume blocks scheduling: the PVC is unbound or the volume is in another zone than the free nodes."),
    (re.compile(r"Too many pods"),
     "Nodes reached their max pod count (often the CNI's IP limit). Add nodes or raise max pods."),
    (re.compile(r"didn't match pod anti-affinity|didn't satisfy existing pods anti-affinity"),
     "Pod anti-affinity leaves no valid node; there are more replicas than eligible nodes/zones."),
    (re.compile(r"didn't match pod topology spread constraints"),
     "Topology spread constraints cannot be satisfied with the current nodes/zones."),
    (re.compile(r"node\(s\) were unschedulable"),
     "Nodes are cordoned (unschedulable). Uncordon them or add capacity."),
]


@check("pod-pending", "Pod cannot be scheduled", Category.SCHEDULING, Severity.HIGH)
def pending(ctx: Context) -> Iterator[Finding]:
    """Pod stays Pending; explains the scheduler's reason."""
    for pod in ctx.snap.all("pods"):
        status = pod.get("status") or {}
        if status.get("phase") != "Pending" or (pod.get("spec") or {}).get("nodeName"):
            continue
        a = age(meta(pod).get("creationTimestamp"))
        if a is not None and a < timedelta(minutes=2):
            continue
        ns, pname = meta(pod).get("namespace"), meta(pod).get("name", "")
        cond = next((c for c in status.get("conditions") or [] if c.get("type") == "PodScheduled"), {})
        messages = [cond.get("message", "")] + [
            e.get("message", "") for e in ctx.snap.events_for("Pod", ns, pname) if e.get("reason") == "FailedScheduling"
        ]
        text = "\n".join(m for m in messages if m)
        hints = []
        for pattern, template in _SCHED_HINTS:
            m = pattern.search(text)
            if m:
                hints.append(template.format(*(m.groups() or ("",))))
        nominated = status.get("nominatedNodeName")
        yield ctx.finding(
            "pod-pending", pod,
            f"Pod has been Pending for {human_duration(a)}. " + (hints[0] if hints else "The scheduler has not placed it yet."),
            evidence=[m[:400] for m in messages if m][:3] + ([f"nominated node {nominated}"] if nominated else []),
            remediation=" ".join(hints[1:]) or "Run `kubectl describe pod` and read the scheduler events.",
            commands=[_pod_cmd(pod), "kubectl describe nodes | grep -A6 'Allocated resources'"],
            since=parse_time(meta(pod).get("creationTimestamp")),
        )


@check("pod-not-ready", "Pod is running but not ready", Category.WORKLOAD, Severity.HIGH)
def not_ready(ctx: Context) -> Iterator[Finding]:
    """Running for 5+ minutes but failing readiness, so it receives no traffic."""
    for pod in ctx.snap.all("pods"):
        status = pod.get("status") or {}
        if status.get("phase") != "Running" or meta(pod).get("deletionTimestamp"):
            continue
        ready = next((c for c in status.get("conditions") or [] if c.get("type") == "Ready"), None)
        if not ready or ready.get("status") == "True":
            continue
        since = age(ready.get("lastTransitionTime"))
        if since is not None and since < timedelta(minutes=5):
            continue
        bad = [cs for cs in status.get("containerStatuses") or []
               if not cs.get("ready") and (cs.get("state") or {}).get("running")]
        if not bad:
            continue  # crash loops and waiting states are covered by other checks
        ns, pname = meta(pod).get("namespace"), meta(pod).get("name", "")
        probe_events = [e.get("message", "") for e in ctx.snap.events_for("Pod", ns, pname)
                        if "Readiness probe failed" in (e.get("message") or "")]
        evidence = [f"not ready for {human_duration(since)}"] + [m[:300] for m in probe_events[:2]]
        mesh = [c.get("name") for c in bad if c.get("name") in ("istio-proxy", "linkerd-proxy", "envoy", "cilium-envoy")]
        if mesh:
            evidence.insert(0, f"service-mesh sidecar {mesh[0]} is the unready container: check the mesh control "
                               "plane (istiod / linkerd-destination) and the sidecar logs, not the app")
        for cs in bad:
            evidence += log_hints(ctx, ns, pname, cs.get("name", ""))[1][:3]
        yield ctx.finding(
            "pod-not-ready", pod,
            f"Containers {', '.join(c.get('name', '') for c in bad)} are running but fail their readiness probe, "
            "so the pod gets no Service traffic.",
            evidence=evidence,
            remediation="Check that the probe path/port matches the app, that dependencies the readiness check "
                        "calls are reachable, and that timeoutSeconds is long enough.",
            commands=[_pod_cmd(pod), f"kubectl logs {pname} -n {ns} --tail=50"],
            since=parse_time(ready.get("lastTransitionTime")),
            node=(pod.get("spec") or {}).get("nodeName"),
        )


@check("pod-restarting", "Container restarts often", Category.WORKLOAD, Severity.MEDIUM)
def restarting(ctx: Context) -> Iterator[Finding]:
    """Running now, but restarted 5+ times: an intermittent crash or liveness kill."""
    for pod in ctx.snap.all("pods"):
        for cs, _ in _statuses(pod):
            restarts = cs.get("restartCount", 0)
            state = cs.get("state") or {}
            if restarts < 5 or not state.get("running") or is_crash_looping(cs):
                continue
            last = (cs.get("lastState") or {}).get("terminated") or {}
            if last.get("reason") == "OOMKilled":
                continue
            finished = parse_time(last.get("finishedAt"))
            if finished and age(last.get("finishedAt")) > timedelta(hours=24):
                continue  # old restarts, now stable
            code = last.get("exitCode")
            ns, pname = meta(pod).get("namespace"), meta(pod).get("name", "")
            liveness = _liveness_killed(ctx, pod)
            yield ctx.finding(
                "pod-restarting", pod,
                f"Container '{cs.get('name')}' restarted {restarts} times"
                + (f", last exit code {code} ({EXIT_CODES.get(code, 'app-specific')})" if code is not None else "")
                + (". The liveness probe is killing it." if liveness else "."),
                evidence=[f"last restart finished at {last.get('finishedAt')}"] + log_hints(ctx, ns, pname, cs.get("name", ""))[1],
                remediation="If the liveness probe kills it, make the probe cheaper or add a startupProbe; "
                            "otherwise read the previous logs for the crash.",
                commands=[f"kubectl logs {pname} -n {ns} -c {cs.get('name')} --previous --tail=100"],
                since=finished,
                node=(pod.get("spec") or {}).get("nodeName"),
            )


@check("pod-evicted", "Pods were evicted", Category.RESOURCES, Severity.MEDIUM)
def evicted(ctx: Context) -> Iterator[Finding]:
    """The kubelet evicted pods under node pressure (memory, disk, ephemeral storage)."""
    for pod in ctx.snap.all("pods"):
        status = pod.get("status") or {}
        if status.get("reason") != "Evicted":
            continue
        msg = status.get("message", "")
        hint = "Set requests close to real usage so the scheduler does not overpack nodes."
        if "ephemeral-storage" in msg or "ephemeral" in msg:
            hint = "The pod wrote too much to local disk (logs, /tmp, emptyDir). Set an ephemeral-storage limit or use a volume."
        elif "memory" in msg:
            hint = "The node ran low on memory. Pods using more memory than they request are evicted first; raise requests."
        yield ctx.finding(
            "pod-evicted", pod, f"Pod was evicted: {msg[:200]}",
            evidence=[msg[:400]],
            remediation=hint + " Evicted pods stay listed until deleted.",
            commands=[f"kubectl delete pods -n {meta(pod).get('namespace')} --field-selector=status.phase=Failed"],
            since=parse_time(status.get("startTime")),
            node=(pod.get("spec") or {}).get("nodeName"),
        )


@check("pod-stuck-terminating", "Pod is stuck terminating", Category.WORKLOAD, Severity.MEDIUM)
def stuck_terminating(ctx: Context) -> Iterator[Finding]:
    """Deletion requested long ago but the pod is still there (finalizers or an unreachable node)."""
    for pod in ctx.snap.all("pods"):
        deleted = meta(pod).get("deletionTimestamp")
        if not deleted:
            continue
        grace = (pod.get("spec") or {}).get("terminationGracePeriodSeconds") or 30
        a = age(deleted)
        if a is None or a < timedelta(seconds=grace + 300):
            continue
        finalizers = meta(pod).get("finalizers") or []
        node = (pod.get("spec") or {}).get("nodeName")
        node_obj = ctx.snap.get("nodes", None, node or "")
        node_ready = _node_ready(node_obj) if node_obj else None
        reason = (f"finalizers {finalizers} are not being removed" if finalizers
                  else f"node {node} is not ready, so the kubelet cannot confirm shutdown" if node_ready is False
                  else "the kubelet has not confirmed the container stopped")
        yield ctx.finding(
            "pod-stuck-terminating", pod,
            f"Pod has been terminating for {human_duration(a)}: {reason}.",
            evidence=[f"deletionTimestamp {deleted}", f"finalizers: {finalizers or 'none'}"],
            remediation="Find what owns the finalizer and why it is stuck. Force deletion is a last resort "
                        "and can leave the process running on the node.",
            commands=[_pod_cmd(pod, "get") + " -o yaml"],
        )


def _node_ready(node: dict[str, Any]) -> bool | None:
    for c in (node.get("status") or {}).get("conditions") or []:
        if c.get("type") == "Ready":
            return c.get("status") == "True"
    return None


@check("pod-init-blocked", "Init container has not finished", Category.WORKLOAD, Severity.HIGH)
def init_blocked(ctx: Context) -> Iterator[Finding]:
    """An init container is still running after 10 minutes (often waiting on a dependency)."""
    for pod in ctx.snap.all("pods"):
        status = pod.get("status") or {}
        if status.get("phase") != "Pending":
            continue
        for cs in status.get("initContainerStatuses") or []:
            running = (cs.get("state") or {}).get("running")
            if not running:
                continue
            a = age(running.get("startedAt"))
            if a is None or a < timedelta(minutes=10):
                continue
            ns, pname = meta(pod).get("namespace"), meta(pod).get("name", "")
            yield ctx.finding(
                "pod-init-blocked", pod,
                f"Init container '{cs.get('name')}' has been running for {human_duration(a)}; the app containers cannot start.",
                evidence=log_hints(ctx, ns, pname, cs.get("name", ""))[1],
                remediation="Init containers usually wait for a database, migration or service. Check that the dependency exists and is reachable.",
                commands=[f"kubectl logs {pname} -n {ns} -c {cs.get('name')} --tail=50"],
            )
