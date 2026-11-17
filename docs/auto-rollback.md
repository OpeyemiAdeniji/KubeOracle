# Auto-rollback

The in-cluster service can undo a rollout that just broke its Deployment, without waiting for a person, then
check that the rollback fixed it and tell you. It is **off** unless you name the namespaces.

## When it acts

Only when all of these hold, on a background scan:

- `rbac.allowWrites: true` and the namespace is listed in `autoRollback.namespaces` (`["*"]` means every namespace
  except the system ones);
- an incident is **critical or high**, and its likely trigger is a **rollout of that Deployment in the last
  30 minutes**;
- the new pods show the version itself is broken: crash loop, image pull failure, config error (missing
  Secret/ConfigMap/key), OOM kill, or a rollout stuck past its progress deadline;
- there is an earlier, **different** version to go back to;
- the Deployment is **not managed by Argo CD or Flux**: they would re-apply the broken version at the next
  sync. For those, the notification says so and you roll back in the GitOps tool.

It then plans the rollback (to the previous different version), runs a server-side dry run, applies it, verifies
(waits for the rollout, watches the pods, re-runs the checks) and sends the outcome to your notification channels:
*"Auto-rolled back shop/checkout: verified healthy"* or *"… still failing after rollback"* with the problems.

Each bad rollout is handled **once**, even with several replicas: if the rollback doesn't help, KubeSift does not
keep trying. Everything is in the audit log with the actor `auto-rollback`.

## Turn it on

```yaml
rbac:
  allowWrites: true
autoRollback:
  namespaces: [shop, payments]
scan:
  interval: 60          # how quickly a bad rollout is noticed
```

## What it never does

- Act on an old rollout, a Deployment it can't roll back, or anything that isn't a Deployment.
- Act in `kube-system` and the other system namespaces, even with `"*"`.
- Restart, scale, delete or change anything else. The other fixes always need a person.

Deployments managed by Argo CD or Flux are skipped (the next sync would re-apply the broken version); the
notification names the change that caused the incident so you can roll back in the GitOps tool.
