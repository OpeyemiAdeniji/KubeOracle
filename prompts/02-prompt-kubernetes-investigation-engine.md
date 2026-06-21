# Prompt 02 — Kubernetes Investigation Engine

## What This Prompt Was Trying to Achieve

Build the core data-collection layer — a suite of Kubernetes inspectors that gather raw cluster state by running `kubectl` commands via subprocess. The engine had to work without the official Kubernetes Python SDK, relying entirely on the `kubectl` CLI already present in the environment. Each inspector targets a specific domain (pods, logs, events, deployments, network, nodes, resources, config/secrets, probes, RBAC, scheduling) and returns a structured Python dict that later gets fed to the AI reasoning layer.

---

## Key Requirements Covered

- `kubectl_executor.py` — a central subprocess wrapper that runs any `kubectl` command, optionally parses JSON output, and returns a typed `KubectlResult` dataclass (success, stdout, stderr, parsed)
- 11 domain inspectors, each focused and independently callable:

| Inspector | File | What It Checks |
|-----------|------|----------------|
| Pods | `pod_inspector.py` | Phase, restart counts, crash states (CrashLoopBackOff, OOMKilled, ImagePullBackOff, etc.), init containers, stuck terminating, missing resource limits |
| Logs | `logs_collector.py` | Recent logs from problematic pods, error/exception keyword extraction |
| Events | `events_analyzer.py` | Warning events across all namespaces, critical event detection |
| Deployments | `deployment_inspector.py` | Unavailable replicas, rollout status, image pull issues |
| Network | `network_inspector.py` | Services with no matching endpoints, ingress misconfigurations |
| Nodes | `node_inspector.py` | NotReady nodes, resource pressure (memory, disk, PID), unschedulable taints |
| Resources | `resource_inspector.py` | Pods without resource requests/limits, namespace-level resource quota usage |
| Config & Secrets | `config_secret_inspector.py` | Orphaned ConfigMaps and Secrets (not referenced by any pod) |
| Probes | `probe_inspector.py` | Deployments missing liveness or readiness probes |
| RBAC | `rbac_inspector.py` | Overly permissive roles (wildcard verbs/resources), missing ServiceAccounts |
| Scheduling | `scheduling_inspector.py` | Pending pods due to unschedulable conditions, affinity/toleration mismatches |

- `investigation_service.py` — orchestrates all 11 inspectors in sequence, publishes realtime step progress to InsForge, and returns a consolidated investigation dict
- The `publish_step()` helper POSTs to InsForge's `investigation_progress` table via the REST API so the frontend can show live progress

---

## Tech Stack and Components Introduced

| Component | Detail |
|-----------|--------|
| Cluster access | `kubectl` CLI via `subprocess.run()` — no Kubernetes SDK |
| JSON parsing | `kubectl … -o json` piped through `json.loads()` |
| Thread-local context | `threading.local()` in `kubectl_executor.py` for per-request cluster switching |
| Step publishing | HTTP POST to InsForge `/api/database/records/investigation_progress` |
| Logging | `loguru` with structured messages per inspector |

---

## Expected Result

After running this prompt:

- Each inspector can be called independently and returns a structured dict with a `success` flag
- `run_full_investigation()` runs all 11 inspectors and returns a nested dict keyed by domain
- The investigation payload is rich enough to pass directly to the AI reasoning layer in Prompt 03
- If `kubectl` cannot reach the cluster, each inspector gracefully returns `{"success": False, "error": "…"}` rather than raising an exception
- Progress steps are inserted to InsForge in real time, enabling live frontend updates
