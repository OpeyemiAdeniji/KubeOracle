# Fixing, safely, and proving it worked

KubeSift is read-only until you ask it to change something. Every fix follows the same path:

1. **Plan:** what will change, in plain words, plus the equivalent kubectl command.
2. **Server-side dry run:** Kubernetes validates the change without applying it (admission webhooks and quotas
   included).
3. **Your approval:** a prompt in the CLI (`-y` skips it), "Apply for real" in Slack, `dry_run=false` in the API/MCP.
4. **Apply**, written to the audit log.
5. **Verify:** wait for the rollout, watch the new pods for restarts, re-run the checks on what was fixed. The answer
   is **resolved**, **still failing** (with what is still wrong) or **still rolling out**. Also written to the audit log.

## The fixes

| Fix | For | Example |
|---|---|---|
| `rollback` | A rollout broke the app | `kubesift fix rollback deploy/checkout -n shop` |
| `restart` | Stuck pods, a config that is read only at start | `kubesift fix restart deploy/checkout -n shop` |
| `scale` | Capacity, or stopping a broken workload | `kubesift fix scale deploy/worker -n shop --replicas 0` |
| `delete-pod` | One bad pod; its controller makes a new one | `kubesift fix delete-pod pod/web-7d9f-x1 -n shop` |
| `set-selector` | A Service with no endpoints because its selector matches no pods | `kubesift fix set-selector svc/web -n shop --match deploy/web` |
| `set-memory` | OOM kills | `kubesift fix set-memory deploy/worker -n shop --memory 1Gi` |
| `grant` | A proven Kubernetes RBAC denial | `kubesift fix grant sa/worker -n shop --verb list --resource secrets` |

**rollback** goes to the previous *different* version. A `kubectl rollout restart` creates a new revision with
the same pods; rolling back to it would bring back the same broken version, so those revisions are skipped.
`--to-revision N` picks one yourself.

**set-selector** refuses a selector that no pod has (it would still have no endpoints). `--match deploy/web` copies
the workload's selector; `--selector app=web,tier=api` sets it directly.

**set-memory** only raises the limit, and raises the request too if it would end up above the new limit.

**grant** creates one Role and RoleBinding in the account's namespace, labelled
`app.kubernetes.io/managed-by=kubesift`, for the verbs and the single resource you name. It refuses wildcards and
RBAC resources. It is the fix for a denial that KubeSift proved came from Kubernetes RBAC (the `pod-access-denied`
finding shows the exact `kubectl auth can-i` check). Think about whether the account *should* have that permission
before granting it.

What KubeSift can't fix for you: a missing Secret or ConfigMap (it can't know the values; the finding gives the
`kubectl create` command), a bad image tag (use `rollback`), and anything outside the cluster.

## Checking any change: `kubesift verify`

Verification works after any change, not only KubeSift's: `kubectl`, Helm, a GitOps sync.

```bash
kubesift verify deploy/checkout -n shop     # deploy, sts, ds, svc, or a pod (verifies its workload)
kubesift verify deploy/checkout -n shop -o json --timeout 300
```

It exits with 0 only when resolved, so it can gate a pipeline step after a deploy.

## From the service, Slack and AI assistants

The in-cluster service can apply fixes only with `rbac.allowWrites: true`, and `grant` additionally needs
`rbac.allowRbacFixes: true`. From the API, a real apply returns a `verify_id`; poll `GET /api/v1/verify/{id}`.
Slack posts a follow-up after "Apply for real" saying whether it worked. AI assistants get `apply_fix` (only with
writes allowed) and the read-only `verify_fix`.

## Auto-rollback (opt-in)

See [auto-rollback](auto-rollback.md).
