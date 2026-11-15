# Security

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting ("Security" tab → "Report a vulnerability") rather than a
public issue. You'll get a reply within a few days.

## What KubeOracle reads and sends

- **Cluster access:** read-only (`get`/`list`) on the resources in `deploy/helm/kubeoracle/templates/rbac.yaml`.
  Write access is only needed for the opt-in fix actions.
- **Secrets:** only key names, the Secret type and, for TLS Secrets, the certificate's subject, DNS names and
  expiry are kept. Values are discarded as soon as they are read. Set `rbac.readSecrets=false` to skip Secrets.
- **ConfigMaps:** only key names are kept.
- **Logs:** only error-looking lines and the last lines of failing containers, redacted for tokens, passwords,
  keys, connection-string credentials and emails.
- **LLM providers** receive findings, change summaries and (unless `send_logs_to_llm=false`) the redacted log
  lines. Nothing is sent when no provider is configured. Use a local model to keep everything in your network.
- **Storage:** history, notes and the audit log live in SQLite under `data_dir`, or in PostgreSQL when
  `database_url` is set.

## Hardening the server

- Always set `KUBEORACLE_API_TOKEN`; the server (and `kubeoracle mcp --transport streamable-http`) refuses to
  listen on a non-local address without it. The token protects the API, the Alertmanager webhook and `/mcp`.
- Releases are signed with cosign (keyless) and ship an SBOM and SLSA provenance; see
  [docs/releasing.md](docs/releasing.md#verifying-a-release-what-users-and-security-teams-run).
- Keep `allow_writes` off unless you need remediation from the API or MCP.
- The Helm chart runs as non-root with a read-only root filesystem, no capabilities and the RuntimeDefault
  seccomp profile.
