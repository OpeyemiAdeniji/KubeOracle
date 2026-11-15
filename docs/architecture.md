# Architecture

```
            ┌────────── interfaces ──────────┐
 CLI / kubectl plugin   REST API + dashboard   MCP server   GitHub Action / pre-commit
            └───────────────┬────────────────┘
                            ▼
   collect ─► Snapshot ─► checks (60) ─► changes ─► correlate ─► explain ─► Report
   (k8s API,   (redacted,   deterministic   rollouts,   incidents,   rules or LLM
    logs,       JSON-able)   findings with   config,    likely       (validated against
    metrics)                 evidence        Helm, Argo  trigger      the evidence)
```

**Snapshot** (`cluster/snapshot.py`). One parallel read of ~35 resource types through the Kubernetes API
(no kubectl needed), plus metrics-server data and the previous logs of failing containers. Secrets are reduced
to key names, type and certificate expiry at read time; ConfigMaps to key names; log lines are redacted. A
snapshot can be saved (`kubeoracle snapshot save`) and analysed later with `--from`, which also makes bug
reports reproducible.

**Checks** (`checks/`). Plain functions that read a snapshot and return `Finding`s: severity, the resource and
its owning workload, a message that says what is wrong in plain words, the evidence copied from the cluster,
a fix and commands. *Problems* are broken now; *risks* will hurt later. Checks that don't need runtime status
also run on manifest files.

**Changes** (`analysis/changes.py`). Rollouts from ReplicaSet revisions (with image, env, resources and probe
diffs), spec/data edits from managedFields, Helm releases (decoded from their release Secrets), Argo CD syncs,
HPA scaling and node events.

**Correlate** (`analysis/correlate.py`). Links each problem to the change most likely to have caused it (same
workload rollout, an edited ConfigMap/Secret it uses, the Helm release that owns it, its node) and groups
findings into incidents: pods on a broken node become one infra incident, a workload's pods and its
Deployment one app incident, with causes listed before symptoms.

**Explain** (`analysis/engine.py`). Without an LLM, a rule-based diagnosis ranks incidents and offers the
rollback of a triggering change. With an LLM, the incidents, changes and evidence are sent (redacted, size
capped) with a strict JSON schema. The reply is validated: root causes that cite no real finding are capped at
40% confidence. Any LLM failure falls back to the rule-based diagnosis.

**Safety.** Read-only by default. `kubeoracle fix` and the API/MCP fix endpoints show a plan, run a
server-side dry run, require confirmation (or `allow_writes`), and write an audit entry.
