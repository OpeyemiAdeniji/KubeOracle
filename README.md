# KubeSift

**Find out what is broken in Kubernetes, why, and what changed — in one command.**

[Website](https://opeyemiadeniji.github.io/KubeSift/) · [Docs](docs/) · [Production guide](docs/production.md)

KubeSift reads your cluster, runs 60 deterministic checks, links each failure to the change that most likely
caused it (a rollout, a ConfigMap edit, a Helm upgrade, a node going away) and tells you the fix, with the
exact commands. An LLM is optional: it ranks and explains the evidence, it never invents it.

```text
$ kubectl sift diagnose -n shop
╭─ Diagnosis ─────────────────────────────────────────────────────────────────────────────╮
│ 1. Deployment/shop/checkout: Container is crash looping  [85% confidence]                 │
│    Container 'app' is in CrashLoopBackOff (exit code 1). The application expects          │
│    environment variable DATABASE_URL, which is not set on the container. This started     │
│    after: Deployment checkout rolled out revision 2: image checkout:1.4 -> checkout:1.5   │
│    (app: env removed DATABASE_URL).                                                       │
│    → If the change caused it, roll back first, then investigate.                          │
│    $ kubectl rollout undo deployment/checkout -n shop --to-revision=1                     │
╰───────────────────────────────────────────────────────────────────────────────────────────╯
```

## Why

Most incidents follow a change, and most of the time goes into *finding* the cause: jumping between kubectl,
dashboards, logs, Git and chat. KubeSift answers the on-call questions directly:

- **What is broken?** Crash loops (with exit-code and log-based explanations), OOM kills, image pulls
  (including Docker Hub rate limits), pending pods (with the scheduler's real reason), missing ConfigMaps/Secrets,
  Services with no endpoints or the wrong port, dead nodes, unbound volumes, failing Jobs and CronJobs,
  autoscalers that can't scale, broken DNS, NetworkPolicies that block DNS, down admission webhooks that block
  deploys, pod IP exhaustion, expiring certificates.
- **Why, and what changed?** Rollout diffs (image, env, resources, probes), config edits, Helm releases,
  Argo CD syncs, scaling and node events — linked to the problems they caused, with rollback commands.
- **What will hurt next?** Removed APIs for your next upgrade, PDBs that will hang node drains, retired
  ingress-nginx, containerd 1.x and cgroup v1 nodes, kubelet skew, missing probes and limits, privileged
  containers, plaintext secrets in env, single replicas.
- **What is it costing?** Over-requested CPU/memory with right-sizing values from real usage, idle GPUs,
  unused volumes and idle load balancers.

## Install

```bash
pipx install "kubesift[all]"      # CLI + kubectl plugin + server + MCP
kubectl sift doctor               # checks access, permissions, metrics and LLM setup
```

`kubesift` and `kubectl sift` are the same command. It uses your kubeconfig and needs read access only.

In the cluster (dashboard, API, MCP endpoint, Alertmanager receiver, background scans with Slack/Teams alerts).
Images and charts are signed; for high availability (2+ replicas on PostgreSQL) see
[docs/production.md](docs/production.md):

```bash
helm install kubesift oci://ghcr.io/opeyemiadeniji/charts/kubesift -n kubesift --create-namespace \
  --set llm.provider=anthropic --set llm.anthropicApiKey=$ANTHROPIC_API_KEY \
  --set notifications.slackWebhookUrl=$SLACK_WEBHOOK
```

## Use it

| Command | What it does |
|---|---|
| `kubesift diagnose [-n ns] [-v] [--risks]` | Scan, group problems into incidents, explain causes |
| `kubesift ask "why is checkout failing?"` | Plain-English questions, focused on the resources you name |
| `kubesift changes --since 2h` | What changed: rollouts with diffs, config edits, Helm, Argo CD, scaling, nodes |
| `kubesift explain deploy/checkout -n shop` | Everything about one resource: status, pods, events, logs, findings, changes |
| `kubesift audit` | Production-readiness review (reliability, security, resources, upgrade risks) |
| `kubesift upgrade-check --target 1.36` | Blockers for the next Kubernetes version |
| `kubesift cost` | Right-sizing and waste, with monthly savings |
| `kubesift lint ./k8s -t 1.36` | Check manifests, Helm charts or Kustomize dirs before they ship (no cluster needed) |
| `kubesift fix rollback deployment/checkout -n shop` | Guarded fix: plan → server-side dry run → confirm → audit log |
| `kubesift snapshot save` | Redacted snapshot to analyse later with `--from` or attach to a ticket |
| `kubesift note add pod-oomkilled "Raised to 1Gi; leak fixed in 3.3"` | Team memory, shown next to matching findings |
| `kubesift history` | Past investigations |
| `kubesift serve` | Web dashboard and REST API |
| `kubesift mcp` | MCP server for Claude, Cursor, VS Code and other assistants |

Every command takes `-o json`; `diagnose` and `lint` also take `-o markdown` (paste into a ticket or postmortem)
and `lint` takes `-o sarif` (GitHub code scanning). `--fail-on high` makes CI fail on findings.

## Where it fits

- **On call:** `kubectl sift diagnose -n <ns> -v` or ask your AI assistant through MCP ([docs/mcp.md](docs/mcp.md)).
- **Alerts:** point Alertmanager at `/api/v1/webhooks/alertmanager`; KubeSift investigates the alert's namespace
  and posts the diagnosis to Slack/Teams before anyone opens a laptop.
- **CI:** stop problems before they ship.

  ```yaml
  - uses: OpeyemiAdeniji/KubeSift@v2
    with: {paths: k8s/, target-version: "1.36", fail-on: high}
  ```

  or as a pre-commit hook (`id: kubesift-lint`).
- **Upgrades and FinOps:** `upgrade-check` before every version bump; `cost` monthly.

## AI is optional, and careful

| Provider | Setup |
|---|---|
| Anthropic | `export ANTHROPIC_API_KEY=...` (default model `claude-opus-5-5`; `pip install "kubesift[anthropic]"`) |
| OpenAI / OpenRouter | `export OPENAI_API_KEY=...` or `OPENROUTER_API_KEY=...` |
| Local (Ollama, vLLM) | `export KUBESIFT_LLM_PROVIDER=ollama KUBESIFT_LLM_MODEL=llama3.1` |
| None | Works fully on rules: `--no-llm` or `KUBESIFT_LLM_PROVIDER=none` |

- Secret and ConfigMap **values are never kept**: they are reduced to key names the moment they are read, and
  certificates to their expiry date.
- Tokens, passwords, API keys, connection-string credentials and emails are redacted from logs before they are
  stored or sent. `send_logs_to_llm: false` keeps logs out of prompts entirely.
- LLM answers must cite real findings; uncited claims are capped at 40% confidence. Any LLM failure falls back
  to the rule-based diagnosis.
- Read-only by default. Fixes need an explicit confirmation (CLI) or `allow_writes` (server/MCP), always run a
  server-side dry run first and are written to an audit log.

## Docs

- [All checks](docs/checks.md) · [Configuration](docs/configuration.md) · [MCP setup](docs/mcp.md) ·
  [Production deployment](docs/production.md) · [Architecture](docs/architecture.md) ·
  [Releasing](docs/releasing.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## License

[Apache-2.0](LICENSE)

## Develop

```bash
pip install -e ".[dev]"
pytest                          # unit tests on a synthetic broken cluster
./scripts/e2e.sh                # breaks a kind cluster on purpose and checks every failure is explained
```
