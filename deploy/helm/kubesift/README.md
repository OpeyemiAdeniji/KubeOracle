# KubeSift Helm chart

Runs [KubeSift](https://github.com/OpeyemiAdeniji/KubeSift) inside the cluster: a web dashboard, REST API,
shared MCP endpoint for AI assistants, an Alertmanager receiver, and background scans that post the diagnosis
to Slack or Teams. KubeSift reads the cluster; it changes nothing unless `rbac.allowWrites` is set and a fix
is confirmed.

## Install

```bash
helm install kubesift oci://ghcr.io/opeyemiadeniji/charts/kubesift -n kubesift --create-namespace
```

Open the dashboard:

```bash
kubectl -n kubesift port-forward svc/kubesift 8080:80
```

The install notes print how to read the generated API token.

With AI explanations and Slack alerts:

```bash
helm install kubesift oci://ghcr.io/opeyemiadeniji/charts/kubesift -n kubesift --create-namespace \
  --set llm.provider=anthropic --set llm.anthropicApiKey=$ANTHROPIC_API_KEY \
  --set notifications.slackWebhookUrl=$SLACK_WEBHOOK
```

To keep keys out of Helm values, put them in your own Secret and set `existingSecret` (keys: `api-token`,
`database-url`, `anthropic-api-key`, `openai-api-key`, `openrouter-api-key`, `slack-webhook-url`,
`teams-webhook-url`).

## Main values

| Value | Default | What it does |
|---|---|---|
| `replicaCount` | `1` | 2+ needs `database.url` (PostgreSQL); one replica runs scans via a Lease |
| `database.url` | `""` | PostgreSQL URL; empty uses SQLite on a PersistentVolume |
| `apiToken` | `""` | Token for the dashboard, API and MCP; generated when empty |
| `llm.provider` | `none` | `anthropic`, `openai`, `openrouter`, `ollama` or `none` |
| `llm.timeout` | `0` | Seconds to wait for one AI answer; 0 = default (180, Ollama 900) |
| `scan.interval` | `300` | Seconds between background scans; 0 disables |
| `scan.notifyMinSeverity` | `high` | Lowest severity that triggers a Slack/Teams message |
| `notifications.slackWebhookUrl` | `""` | Slack incoming webhook for scan results |
| `rbac.readSecrets` | `true` | Read Secrets to check references, TLS expiry and Helm releases (values are never kept) |
| `rbac.allowWrites` | `false` | Allow guarded fixes (restart, rollback, scale) from the API and MCP |

All values, high availability and access options: [values.yaml](https://github.com/OpeyemiAdeniji/KubeSift/blob/main/deploy/helm/kubesift/values.yaml)
and the [production guide](https://github.com/OpeyemiAdeniji/KubeSift/blob/main/docs/production.md).

## Verify the image

Images are signed with cosign, keyless, by the release workflow (replace `VERSION`, e.g. `2.1.2`):

```bash
cosign verify ghcr.io/opeyemiadeniji/kubesift:VERSION \
  --certificate-identity "https://github.com/OpeyemiAdeniji/KubeSift/.github/workflows/release.yml@refs/tags/vVERSION" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```
