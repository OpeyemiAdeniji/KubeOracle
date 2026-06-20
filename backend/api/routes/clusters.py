import json
import subprocess

from fastapi import APIRouter

router = APIRouter()


@router.get("/clusters")
def get_clusters():
    """
    Return all Kubernetes contexts from the local kubeconfig file.
    Uses `kubectl config view -o json` so no extra dependencies are needed.
    """
    # Prefer the full kubeconfig (all clusters) mounted at /root/.kube/all-config.
    # Fall back to the default kubeconfig (kind-config) if the full one isn't mounted.
    import os
    kubeconfig_path = "/root/.kube/all-config" if os.path.exists("/root/.kube/all-config") else None
    cmd = ["kubectl", "config", "view", "-o", "json"]
    if kubeconfig_path:
        cmd = ["kubectl", "--kubeconfig", kubeconfig_path, "config", "view", "-o", "json"]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return {
            "clusters": [],
            "current_context": "",
            "error": "kubectl not found in PATH — is it installed inside the container?",
        }
    except subprocess.TimeoutExpired:
        return {
            "clusters": [],
            "current_context": "",
            "error": "kubectl config view timed out",
        }
    except Exception as exc:
        return {
            "clusters": [],
            "current_context": "",
            "error": str(exc),
        }

    if proc.returncode != 0:
        err = proc.stderr.strip() or "kubectl config view failed"
        # Provide actionable guidance for the most common Docker scenario
        if "no such file" in err.lower() or "cannot" in err.lower():
            err = (
                "No kubeconfig found. "
                "Mount your kubeconfig in docker-compose.yml: "
                "- ~/.kube/config:/root/.kube/config:ro"
            )
        return {"clusters": [], "current_context": "", "error": err}

    try:
        cfg = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"clusters": [], "current_context": "", "error": "Could not parse kubeconfig JSON"}

    current_context = cfg.get("current-context", "")

    # Build cluster-name → server URL map
    server_map: dict[str, str] = {}
    for cluster_entry in cfg.get("clusters") or []:
        name = cluster_entry.get("name", "")
        server = (cluster_entry.get("cluster") or {}).get("server", "")
        if name:
            server_map[name] = server

    # Build context list
    clusters = []
    for ctx_entry in cfg.get("contexts") or []:
        ctx_name = ctx_entry.get("name", "")
        cluster_name = (ctx_entry.get("context") or {}).get("cluster", "")
        server = server_map.get(cluster_name, "")
        clusters.append({
            "name": ctx_name,
            "context": ctx_name,
            "server": server,
            "current": ctx_name == current_context,
        })

    return {
        "clusters": clusters,
        "current_context": current_context,
    }
