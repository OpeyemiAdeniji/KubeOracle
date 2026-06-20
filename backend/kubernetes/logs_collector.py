import re
from loguru import logger
from kubernetes.kubectl_executor import run_kubectl

# Patterns that indicate something went wrong in a log line
_ERROR_PATTERNS = re.compile(
    r"(?i)("
    r"exception|traceback|error|fatal|critical|panic|killed"
    r"|connection refused|connection failed|connection timed out"
    r"|no such.*env|env.*not (set|found)|missing.*env"
    r"|out of memory|oomkilled|cannot allocate"
    r"|timed? out|deadline exceeded"
    r"|database.*connect|could not connect.*database|db.*error"
    r"|permission denied|access denied|unauthorized|forbidden"
    r"|address already in use|bind.*failed|port.*in use"
    r")"
)


def _fetch_logs(namespace: str, pod: str, previous: bool = False) -> list[str]:
    args = [
        "logs", "-n", namespace, pod,
        "--tail=100",
        "--all-containers=true",
    ]
    if previous:
        args.append("--previous")
    result = run_kubectl(args)
    if not result.success:
        return []
    return result.stdout.splitlines()


def _relevant_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if _ERROR_PATTERNS.search(line)]


def collect_logs(problematic_pods: list[dict]) -> dict:
    if not problematic_pods:
        return {
            "success": True,
            "message": "No problematic pods — skipping log collection",
            "pods_checked": 0,
            "pod_logs": [],
        }

    logger.info(f"Collecting logs from {len(problematic_pods)} problematic pod(s)...")

    pod_logs = []

    for pod_info in problematic_pods:
        name = pod_info["name"]
        namespace = pod_info["namespace"]
        status = pod_info["status"]

        current_lines = _fetch_logs(namespace, name)
        previous_lines = _fetch_logs(namespace, name, previous=True)

        relevant_current = _relevant_lines(current_lines)
        relevant_previous = _relevant_lines(previous_lines)

        # Fallback: include last 10 lines if no patterns matched
        raw_tail = current_lines[-10:] if not relevant_current and current_lines else []

        entry: dict = {
            "pod": name,
            "namespace": namespace,
            "status": status,
            "relevant_errors": relevant_current or relevant_previous,
            "raw_tail": raw_tail,
            "has_previous_logs": bool(previous_lines),
        }

        if relevant_previous and not relevant_current:
            entry["note"] = "Errors found in previous container (container has since restarted)"

        pod_logs.append(entry)
        logger.debug(f"  {namespace}/{name}: {len(entry['relevant_errors'])} error lines")

    return {
        "success": True,
        "pods_checked": len(pod_logs),
        "pod_logs": pod_logs,
    }
