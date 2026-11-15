# Slack on-call

Investigate from the channel where the incident is being discussed:

```
/sift diagnose shop            what is broken in namespace shop, and why
/sift why checkout shop        a focused answer about one workload
/sift changes shop 2h          what changed in the last two hours
/sift postmortem shop          a postmortem draft
```

When a recent rollout is the likely cause, the answer has a **Roll back (dry run)** button. It runs a
server-side dry run first. An **Apply for real** button appears only when the server has
`allow_writes` enabled **and** the Slack user is listed in `slack_allowed_users` (an empty list means nobody
can apply from Slack). Every action is in the audit log with
the Slack user ID.

## Setup (about 5 minutes)

1. Create a Slack app at <https://api.slack.com/apps> → **From scratch**.
2. **Slash Commands** → *Create New Command*: command `/sift`, request URL
   `https://<your KubeSift URL>/api/v1/slack/commands`.
3. **Interactivity & Shortcuts** → turn on, request URL `https://<your KubeSift URL>/api/v1/slack/actions`.
4. **Install App** to your workspace.
5. Copy **Basic Information → Signing Secret** into the Helm values:

```yaml
slack:
  signingSecret: "<signing secret>"
  allowedUsers: ["U0123ABC"]   # who may apply fixes; empty = nobody (dry runs still work)
rbac:
  allowWrites: false           # true to allow Apply (dry runs always work)
ingress:
  enabled: true                # Slack must reach the service over HTTPS
```

Requests are verified with Slack's signing secret (HMAC-SHA256, rejected after 5 minutes), so the endpoints
don't need the API token. Answers are posted back only to Slack's own `hooks.slack.com` response URLs.
