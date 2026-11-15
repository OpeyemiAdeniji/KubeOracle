# Using KubeOracle from AI assistants (MCP)

`kubeoracle mcp` runs a [Model Context Protocol](https://modelcontextprotocol.io) server. Your assistant calls
KubeOracle's tools, gets structured evidence back and does the reasoning itself, so **no LLM key is needed on
the KubeOracle side**. It uses your current kubeconfig context (or `--context`).

```bash
pip install "kubeoracle[mcp]"
```

## Claude Code

```bash
claude mcp add kubeoracle -- kubeoracle mcp
```

## Claude Desktop, Cursor, Windsurf

Add to `claude_desktop_config.json` / `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "kubeoracle": { "command": "kubeoracle", "args": ["mcp"] }
  }
}
```

## VS Code

`.vscode/mcp.json`:

```json
{
  "servers": {
    "kubeoracle": { "type": "stdio", "command": "kubeoracle", "args": ["mcp"] }
  }
}
```

## Shared server for a team

The in-cluster service (Helm chart) serves MCP at `/mcp`, protected by the same bearer token as the API:

```bash
claude mcp add --transport http kubeoracle https://kubeoracle.example.com/mcp   --header "Authorization: Bearer $KUBEORACLE_TOKEN"
```

Or run a standalone HTTP MCP server. On a non-local address it refuses to start without a token:

```bash
KUBEORACLE_API_TOKEN=... kubeoracle mcp --transport streamable-http --host 0.0.0.0 --port 8765
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
| `list_checks` | Every check and what it detects |
| `add_team_note` | Records what fixed a problem, shown next to future matching findings |
| `apply_fix` | Only when `KUBEORACLE_ALLOW_WRITES=true`: restart, rollback, scale, delete pod. Dry run by default |

All tools except `add_team_note` and `apply_fix` are marked read-only.

## Example prompts

- "Use kubeoracle to find out why checkout is down in the shop namespace and tell me what changed before it broke."
- "Run an upgrade check for 1.36 and write me a migration checklist."
- "Lint the manifests in ./k8s and fix the high-severity findings."
