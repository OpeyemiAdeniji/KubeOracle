# Using KubeSift from AI assistants (MCP)

`kubesift mcp` runs a [Model Context Protocol](https://modelcontextprotocol.io) server. Your assistant calls
KubeSift's tools, gets structured evidence back and does the reasoning itself, so **no LLM key is needed on
the KubeSift side**. It uses your current kubeconfig context (or `--context`).

```bash
pip install "kubesift[mcp]"
```

## Claude Code

```bash
claude mcp add kubesift -- kubesift mcp
```

## Claude Desktop, Cursor, Windsurf

Add to `claude_desktop_config.json` / `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "kubesift": { "command": "kubesift", "args": ["mcp"] }
  }
}
```

## VS Code

`.vscode/mcp.json`:

```json
{
  "servers": {
    "kubesift": { "type": "stdio", "command": "kubesift", "args": ["mcp"] }
  }
}
```

## Shared server for a team

The in-cluster service (Helm chart) serves MCP at `/mcp`, protected by the same bearer token as the API:

```bash
claude mcp add --transport http kubesift https://kubesift.example.com/mcp   --header "Authorization: Bearer $KUBESIFT_TOKEN"
```

Or run a standalone HTTP MCP server. On a non-local address it refuses to start without a token:

```bash
KUBESIFT_API_TOKEN=... kubesift mcp --transport streamable-http --host 0.0.0.0 --port 8765
# endpoint: http://<host>:8765/mcp
```

The endpoint is stateless, so it works behind a load balancer with several replicas. For single sign-on,
put it behind oauth2-proxy (see [production.md](production.md#access-and-authentication)).

## Tools

| Tool | What it returns |
|---|---|
| `diagnose` | Problems grouped into incidents, with evidence, likely triggering change, fix and commands |
| `explain_resource` | Status, pods, events, redacted error logs, findings and changes for one resource |
| `recent_changes` | Rollouts with image/env diffs, ConfigMap/Secret edits, Helm upgrades, Argo CD syncs, scaling, node events |
| `cost_report` | Right-sizing suggestions and waste (idle GPUs, unused volumes, idle load balancers) |
| `upgrade_check` | Blockers for a target Kubernetes version |
| `lint_manifests` | Problems in YAML you paste or generate, before it ships |
| `preflight_manifests` | The same YAML checked against the live cluster (missing Secrets, capacity, OOM risk, risky diffs) |
| `cluster_diff` | Time travel: what differs between now and a moment in the past |
| `draft_postmortem` | A blameless postmortem draft in Markdown |
| `list_checks` | Every check and what it detects |
| `add_team_note` | Records what fixed a problem, shown next to future matching findings |
| `open_ticket` | Only when `github_repo` is set: a GitHub issue for an incident (comments on its open issue instead of duplicating) |
| `verify_fix` | Did a fix work? Waits for the rollout, watches the pods, re-checks: resolved, still failing or rolling out |
| `apply_fix` | Only when `KUBESIFT_ALLOW_WRITES=true`: restart, rollback, scale, delete pod, Service selector, memory limit (and `grant` with `KUBESIFT_ALLOW_RBAC_FIXES`). Dry run by default; then `verify_fix` ([fixes](fixes.md)) |

All tools except `add_team_note`, `open_ticket` and `apply_fix` are marked read-only.

When a call can't be answered (the cluster is unreachable or rejects the login, the resource doesn't exist, a
duration or version is written wrong, the YAML doesn't parse), the tool returns an MCP error with the reason and,
where there is one, what to do, so the assistant can tell you or retry with better input. Unexpected internal
errors are reported without their details.

## Example prompts

- "Use kubesift to find out why checkout is down in the shop namespace and tell me what changed before it broke."
- "Run an upgrade check for 1.36 and write me a migration checklist."
- "Lint the manifests in ./k8s and fix the high-severity findings."
