# The dashboard

`kubesift serve` (or the Helm chart) runs a web dashboard next to the REST API.

- **Incidents** grouped by cause, each with the likely trigger, the rollback command and how to verify the fix.
- **Map:** what an incident's workload or Service is connected to (Ingress → Service → workload → Pods → Node,
  plus its ConfigMaps, Secrets, volumes and autoscaler), coloured by health. The broken link is drawn red and
  dashed: a missing Secret, a Service selector that matches no pods, a node that is NotReady.
- **Live:** follows the background scans. New incidents are marked, fixed ones move to **Resolved** with the time.
  Needs background scans (`scan_interval`, chart `scan.interval`).
- **Logs** on pod findings: the last lines, redacted like everything else KubeSift shows, with a container picker
  and the previous (crashed) container. `dashboard_logs: false` (chart `dashboard.logs`) turns it off.
- **Search and filters** by text, severity and namespace.
- Changes, cost, upgrade checks, lint and history in their own tabs.

## Run it

**On your laptop** (uses your kubeconfig and its login plugins):

```bash
pipx install "kubesift[all]"
kubesift serve                      # http://127.0.0.1:8080
KUBESIFT_SCAN_INTERVAL=60 kubesift serve   # with Live mode
```

**With Docker**, for clusters whose kubeconfig works without a login plugin (certificates or a token; kind,
minikube, k3d, most self-managed clusters). A published port means a token is required:

```bash
docker run --rm -p 8080:8080 -v "$HOME/.kube/config:/kube/config:ro" -e KUBECONFIG=/kube/config \
  -e KUBESIFT_API_TOKEN=pick-a-long-random-string ghcr.io/opeyemiadeniji/kubesift:latest
```

For a **kind** cluster, join kind's network and use its internal address:

```bash
kind get kubeconfig --internal > /tmp/kind-config && chmod 644 /tmp/kind-config
docker run --rm --network kind -p 8080:8080 -v /tmp/kind-config:/kube/config:ro -e KUBECONFIG=/kube/config \
  -e KUBESIFT_API_TOKEN=pick-a-long-random-string ghcr.io/opeyemiadeniji/kubesift:latest
```

EKS, GKE and AKS kubeconfigs call a login plugin (`aws`, `gke-gcloud-auth-plugin`, `kubelogin`) that is not in
the image: use `kubesift serve` from pipx, or run KubeSift in the cluster with the Helm chart.

**In the cluster:** see [Running in production](production.md).
