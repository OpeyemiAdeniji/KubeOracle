import json
import subprocess
import threading
from dataclasses import dataclass
from typing import Any

from loguru import logger

from core.config import settings

# Thread-local storage so each investigation thread can carry its own context.
# FastAPI runs sync endpoints in a thread pool — this is safe and avoids
# adding a `context` parameter to every inspector function.
_thread_local = threading.local()


def set_context(context: str) -> None:
    """Set the kubectl --context for the current thread."""
    _thread_local.kubectl_context = context


def get_context() -> str:
    """Return the kubectl --context for the current thread (empty = default)."""
    return getattr(_thread_local, "kubectl_context", "")


@dataclass
class KubectlResult:
    stdout: str
    stderr: str
    return_code: int
    parsed: Any = None

    @property
    def success(self) -> bool:
        return self.return_code == 0


def run_kubectl(args: list[str], parse_json: bool = False) -> KubectlResult:
    cmd = ["kubectl"]
    if settings.kubeconfig_path:
        cmd += [f"--kubeconfig={settings.kubeconfig_path}"]
    context = get_context()
    if context:
        cmd += ["--context", context]
    cmd += args

    logger.debug(f"kubectl {' '.join(args)}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        parsed = None
        if parse_json and proc.returncode == 0 and proc.stdout:
            try:
                parsed = json.loads(proc.stdout)
            except json.JSONDecodeError:
                logger.warning("kubectl output was not valid JSON")

        return KubectlResult(
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
            return_code=proc.returncode,
            parsed=parsed,
        )

    except subprocess.TimeoutExpired:
        logger.error(f"kubectl timed out: {' '.join(args)}")
        return KubectlResult("", "Command timed out after 30s", 1)
    except FileNotFoundError:
        logger.error("kubectl not found in PATH")
        return KubectlResult("", "kubectl not found in PATH", 127)
    except Exception as e:
        logger.error(f"Unexpected kubectl error: {e}")
        return KubectlResult("", str(e), 1)
