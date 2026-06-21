# Prompt 05 — Integration Testing & Deployment

## What This Prompt Was Trying to Achieve

Make KubeOracle testable against real Kubernetes failure scenarios and production-deployable with multi-cluster support. This prompt added a `GET /clusters` endpoint so the frontend could list available kubeconfig contexts, a `ClusterSelector` component so users could switch between clusters, thread-local context propagation through all 11 inspectors without changing their signatures, and a full set of reproducible failure scenario YAMLs for manual end-to-end testing.

---

## Key Requirements Covered

- **`GET /clusters`** — reads `kubectl config view -o json`, extracts all available contexts and the current active context, returns them to the frontend. No extra dependencies: uses the same `kubectl_executor` subprocess wrapper already in place
- **Thread-local cluster context** in `kubectl_executor.py`:
  - `set_context(context: str)` stores the cluster context in `threading.local()`
  - `run_kubectl()` reads the thread-local context and appends `--context=<name>` to every command when set
  - All 11 inspectors continue to call `run_kubectl()` unchanged — zero modifications to inspector code
- **`POST /investigate`** updated to accept a `context` field; passes it to `run_full_investigation()` which calls `set_context()` at the start
- Cluster unreachability short-circuit improved: when `kubectl` cannot reach the chosen context, the response includes the context name in the error message and the exact `kubectl` commands to diagnose it
- **`ClusterSelector.tsx`** frontend component:
  - Fetches `/clusters` on dashboard load
  - Auto-selects the active kubeconfig context
  - Dropdown to switch between contexts before triggering an investigation
  - Sends the selected `context` name with every `POST /investigate` request
- **Docker Compose kubeconfig volume**: the `docker-compose.yml` includes a commented-out volume mount (`~/.kube:/root/.kube:ro`) with instructions for users to uncomment it when running against a real cluster
- **`test-scenarios/`** directory — 6 reproducible Kubernetes failure YAMLs for local testing:

| File | Failure Scenario |
|------|-----------------|
| `01-crashloop-missing-env.yaml` | CrashLoopBackOff caused by missing required environment variable |
| `02-imagepull-wrong-tag.yaml` | ImagePullBackOff caused by a non-existent image tag |
| `03-oomkilled-low-memory.yaml` | OOMKilled — container memory limit set too low for the workload |
| `04-service-selector-mismatch.yaml` | Service with no endpoints — label selector doesn't match any pod |
| `05-missing-probe-crashloop.yaml` | CrashLoop caused by a missing readiness probe killing traffic prematurely |
| `06-pending-insufficient-resources.yaml` | Pod stuck Pending — requests exceed available node capacity |

- **`apply-scenario.sh`** — applies a chosen scenario YAML by number and tails events
- **`cleanup.sh`** — deletes all test-scenario namespaces to restore a clean cluster state

---

## Tech Stack and Components Introduced

| Component | Detail |
|-----------|--------|
| Cluster listing | `kubectl config view -o json` parsed in `clusters.py` route |
| Context isolation | `threading.local()` in `kubectl_executor.py` |
| Test tooling | Bash scripts (`apply-scenario.sh`, `cleanup.sh`) + Kubernetes YAML manifests |
| Docker volume | Commented kubeconfig mount in `docker-compose.yml` |

---

## Expected Result

After running this prompt:

- The frontend cluster selector populates from the real kubeconfig and remembers the selection
- All 11 inspectors automatically use the selected cluster context without any code change
- Each test scenario reliably triggers a specific failure that KubeOracle can detect and diagnose
- Running `./test-scenarios/apply-scenario.sh 1` deploys the CrashLoop scenario; KubeOracle diagnoses it as a missing environment variable
- `./test-scenarios/cleanup.sh` removes all test namespaces cleanly
- Docker deployment with a real cluster only requires uncommenting one line in `docker-compose.yml`
