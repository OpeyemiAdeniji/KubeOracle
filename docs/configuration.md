# Configuration

Settings come from (lowest to highest priority): defaults, the config file, environment variables, CLI flags.

- Config file: `~/.config/kubesift/config.yaml` (or the path in `KUBESIFT_CONFIG`). Keys are the names below.
- Environment: `KUBESIFT_<NAME>` in upper case, e.g. `KUBESIFT_LLM_PROVIDER=ollama`.

```yaml
# ~/.config/kubesift/config.yaml
llm_provider: anthropic        # auto | anthropic | openai | openrouter | ollama | none
llm_model: claude-opus-5-5
prometheus_url: http://localhost:9090
runbooks_dir: ~/runbooks
cost_cpu_hour: 0.0316
cost_gb_hour: 0.0042
```

## Cluster access

| Name | Default | Notes |
|---|---|---|
| `kubeconfig` | `~/.kube/config` | Also `--kubeconfig` |
| `context` | current context | Also `--context` |
| `request_timeout` | `20` | Seconds per API call |
| `max_items_per_kind` | `5000` | Cap per resource type on very large clusters |

Inside a pod, KubeSift uses the service account automatically. It needs **read** access only; see
`deploy/helm/kubesift/templates/rbac.yaml` for the exact rules. Missing permissions are reported, not fatal.

## LLM

KubeSift never needs an LLM: every finding comes from deterministic checks. An LLM only ranks and explains them.

| Name | Default | Notes |
|---|---|---|
| `llm_provider` | `auto` | `auto` picks the first of `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY` that is set, else `none` |
| `llm_model` | per provider | Anthropic: `claude-opus-5-5` |
| `llm_base_url` | per provider | Any OpenAI-compatible endpoint (vLLM, LiteLLM, Azure gateway), or your Ollama server |
| `llm_api_key` | from provider env var | |
| `llm_effort` | `medium` | Anthropic effort: `low`, `medium`, `high` |
| `llm_timeout` | `180` (`900` for Ollama) | Seconds to wait for one answer. A slow answer is not retried |
| `llm_context` | sized to the prompt | Ollama only: context window in tokens (8192–32768 by default) |
| `send_logs_to_llm` | `true` | `false` keeps log lines out of prompts |

The Anthropic provider sends requests with server-side refusal fallback enabled (`fallbacks: "default"`), so a
declined request is retried on another model in the same call.

**Data sent to the LLM:** findings, evidence, recent change summaries and (optionally) error log lines. Secret
values and ConfigMap values are never collected. Tokens, passwords, keys, connection-string credentials and email
addresses in log lines are redacted before anything is stored or sent. To keep everything local, use Ollama:

```bash
export KUBESIFT_LLM_PROVIDER=ollama KUBESIFT_LLM_MODEL=llama3.1
```

KubeSift talks to Ollama's native API: it turns off hidden reasoning ("thinking") so answers come faster, sizes the
context window to the prompt (Ollama's default window is small and silently drops the start of a long prompt), and
constrains the output to the expected JSON. A diagnosis prompt is a few thousand tokens. On a machine with a GPU a
7–8B model answers in seconds; on a laptop CPU it can take many minutes, so raise `llm_timeout`, pick a smaller
model, use one of Ollama's cloud models, or run with `--no-llm`. If the model does not answer in time KubeSift
shows the rule-based diagnosis instead.

## Usage data and cost

| Name | Default | Notes |
|---|---|---|
| `prometheus_url` | none | Enables 7-day p95 CPU / peak memory right-sizing |
| `prometheus_token` | none | Bearer token for managed Prometheus |
| `cost_cpu_hour` | `0.0316` | USD per vCPU-hour |
| `cost_gb_hour` | `0.0042` | USD per GiB-hour |
| `cost_gpu_hour` | `2.50` | USD per GPU-hour |

Without Prometheus, metrics-server's current usage is used (one sample; treat as a hint).

## Team knowledge

| Name | Default | Notes |
|---|---|---|
| `data_dir` | `~/.kubesift` | SQLite: history, team notes, audit log |
| `database_url` | none | `postgresql://...` to share state between replicas (`pip install "kubesift[postgres]"`) |
| `runbooks_dir` | none | YAML runbooks attached to matching findings (see `src/kubesift/runbooks.py`) |

## Server

| Name | Default | Notes |
|---|---|---|
| `api_token` | none | Protects the API, Alertmanager webhook and `/mcp`; required on a non-local address |
| `cors_origins` | none | Comma-separated |
| `allow_writes` | `false` | Enables `/api/v1/fix` and the MCP `apply_fix` tool |
| `leader_election` | `false` | Only the holder of the `kubesift-scanner` Lease runs background scans (the chart turns it on) |
| `leader_namespace` | pod namespace | Namespace for the Lease |
| `scan_interval` | `0` | Seconds between background scans (0 = off) |
| `cache_ttl` | `20` | Seconds a cluster snapshot is reused across API calls |
| `alert_cooldown` | `600` | Seconds before the same namespace is investigated again for Alertmanager alerts |
| `history_days` | `30` | Stored investigations older than this are deleted |
| `scan_namespace` | all | |
| `notify_min_severity` | `high` | |
| `slack_webhook_url`, `teams_webhook_url`, `webhook_url` | none | Notifications for new problems and Alertmanager investigations |
| `slack_signing_secret` | none | Enables the `/sift` Slack command and fix buttons (see [slack.md](slack.md)) |
| `slack_allowed_users` | none | Slack user IDs allowed to apply fixes (comma-separated); empty means nobody; also needs `allow_writes` |
