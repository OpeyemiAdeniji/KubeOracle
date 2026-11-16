# Preflight: check a change against the live cluster

`kubesift lint` checks YAML on its own. `kubesift preflight` also asks the cluster you are about to deploy to,
which catches the failures that only show up after `kubectl apply`:

| Check | Example |
|---|---|
| Missing dependencies | Secret `stripe` / key `key`, ConfigMap, imagePullSecret, ServiceAccount or Namespace not in the target cluster |
| Capacity | Each pod requests more CPU/memory than any ready node has free; ResourceQuota too small |
| Risky diffs vs. the running version | Env vars removed, replicas set to 0, a memory limit **below the app's current usage** (it will be OOMKilled) |
| Storage and routing | StorageClass or IngressClass that doesn't exist, Ingress pointing at a missing Service |
| Disruption | PodDisruptionBudget that will block every node drain |
| Everything `lint` checks | Removed APIs for the cluster's version, probes, limits, security |
| `--check-images` | The image tag exists in its registry (public images; private ones are skipped) |
| `--server-dry-run` | The API server's answer: admission webhooks, Pod Security, quota, schema validation |

```bash
kubesift preflight k8s/ -n prod                         # files, Helm chart or Kustomize dir
helm template my-app ./chart | kubesift preflight - -n prod
kubesift preflight k8s/ --server-dry-run --check-images -o markdown > preflight.md
```

Exit code 2 when something at or above `--fail-on` (default `high`) is found.

Preflight only reads the cluster, except `--server-dry-run`, which sends a server-side apply with `dryRun=All`.
Nothing is created or changed, but the credentials need `patch` permission on the resources.

## In CI (GitHub Actions)

Give the job read access to the target cluster (a kubeconfig in a secret, or your cloud's OIDC login):

```yaml
name: preflight
on: pull_request
jobs:
  preflight:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with: {python-version: "3.12"}
      - run: pip install kubesift
      - run: echo "${{ secrets.KUBECONFIG_PROD_READONLY }}" > kubeconfig
      - run: kubesift --kubeconfig kubeconfig preflight k8s/ -n prod -o markdown > preflight.md
      - if: always()
        run: gh pr comment ${{ github.event.number }} --body-file preflight.md
        env: {GH_TOKEN: "${{ github.token }}"}
```

Use a read-only ServiceAccount token for this kubeconfig (the Helm chart's read ClusterRole is a good template).
