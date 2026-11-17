# Tickets: an incident as a GitHub issue

KubeSift can turn an incident into a ticket with everything the person picking it up needs: what is wrong, the
evidence, the change that likely caused it (with the rollback command), the fix and its commands, and how to check
afterwards that the fix worked (`kubesift verify`).

**One issue per incident.** Each issue carries a hidden key (cluster + workload + check). While that issue is open,
the same incident gets a "still happening" comment instead of a second issue. Close the issue when it is fixed; if it
comes back later, a new one is opened.

## From the command line

```bash
kubesift ticket -n shop                  # the most severe incident in shop
kubesift ticket -n shop --incident 2     # the second one, as numbered by `kubesift diagnose`
kubesift ticket -n shop --print          # Markdown only: paste it into Jira, Linear, email...
```

## Setup

1. Create a **fine-grained personal access token** (GitHub → Settings → Developer settings) limited to the one
   repository, with **Issues: Read and write**. Nothing else is needed.
2. Tell KubeSift where to open issues:

```bash
export KUBESIFT_GITHUB_REPO=acme/platform
export KUBESIFT_GITHUB_TOKEN=...      # GITHUB_TOKEN also works, e.g. inside GitHub Actions
```

GitHub Enterprise Server: also set `KUBESIFT_GITHUB_API_URL=https://ghe.example.com/api/v3`.

Issues get the labels `kubesift`, `incident` and `severity: …`. GitHub creates missing labels the first time.

## In the cluster (Helm)

```yaml
tickets:
  githubRepo: acme/platform
  githubToken: ""        # or put it in existingSecret under the key github-token
  onAlert: false         # true: open/update an issue for each urgent incident automatically
```

With `onAlert: true`, every Alertmanager investigation and every background scan that finds a **new** problem at
or above `scan.notifyMinSeverity` opens an issue (or comments on its open one). Scans that find nothing new don't
comment, so an unfixed problem doesn't flood the issue.

When `githubRepo` is set, the service also offers:

- **API:** `POST /api/v1/tickets` with `{"namespace": "shop", "incident": 1}`; add `"markdown_only": true` to get
  the text instead.
- **Slack:** an **Open GitHub issue** button under each incident in `/sift diagnose`.
- **MCP:** an `open_ticket` tool for AI assistants (marked as not read-only, so assistants ask first).

## What leaves the cluster

Only the ticket text: incident title, finding messages, evidence lines (already redacted the same way as everywhere
else in KubeSift: no Secret values, masked credentials) and kubectl commands. It is sent only to the GitHub
repository you configure.
