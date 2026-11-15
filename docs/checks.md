# Checks

KubeSift runs 71 checks. **Problems** are broken now; **risks** will hurt later. Checks marked *lint* also run on manifest files (`kubesift lint`), without a cluster.

Regenerate this page with `python scripts/gen_check_docs.py`.

## Workload

### cronjob-failing

**CronJob's recent runs fail** · high · problem

The last scheduled run failed or there has been no success since the last schedule.

### daemonset-unavailable

**DaemonSet pods missing or unready** · high · problem

Nodes that should run the DaemonSet don't have a ready pod (agents, CNI, log shippers).

### deployment-rollout-stuck

**Rollout is stuck** · high · problem

The Deployment exceeded progressDeadlineSeconds: the new version never became ready.

### deployment-unavailable

**Deployment has fewer ready replicas than desired** · high · problem

Ready replicas are below spec.replicas for more than 3 minutes.

### job-failed

**Job failed** · high · problem

A Job hit its backoffLimit or activeDeadlineSeconds.

### pod-crashloop

**Container is crash looping** · critical · problem

A container keeps exiting with an error and Kubernetes keeps restarting it.

### pod-image-pull

**Image cannot be pulled** · critical · problem

ErrImagePull / ImagePullBackOff / InvalidImageName.

### pod-init-blocked

**Init container has not finished** · high · problem

An init container is still running after 10 minutes (often waiting on a dependency).

### pod-not-ready

**Pod is running but not ready** · high · problem

Running for 5+ minutes but failing readiness, so it receives no traffic.

### pod-restarting

**Container restarts often** · medium · problem

Running now, but restarted 5+ times: an intermittent crash or liveness kill.

### pod-stuck-terminating

**Pod is stuck terminating** · medium · problem

Deletion requested long ago but the pod is still there (finalizers or an unreachable node).

### statefulset-unready

**StatefulSet has unready replicas** · high · problem

Ready replicas below desired; StatefulSets roll one pod at a time, so one bad pod blocks the rest.

## Config

### config-ref-missing

**Referenced ConfigMap or Secret does not exist** · critical · problem

A pod references a ConfigMap/Secret (or a key in one) that is missing from its namespace.

### config-stale-in-pod

**Pods run with old config values** · medium · problem

A ConfigMap/Secret used through env vars changed after the pod started; env vars are only read at start.

### image-pull-secret-missing

**imagePullSecret does not exist** · high · problem

The pod lists an imagePullSecret that is not in its namespace.

### pod-config-error

**Container cannot start: configuration error** · critical · problem

CreateContainerConfigError / CreateContainerError / RunContainerError.

## Network

### gateway-not-accepted

**Gateway or HTTPRoute not accepted** · high · problem

Gateway API objects whose Accepted/Programmed/ResolvedRefs condition is False.

### ingress-backend-missing

**Ingress points to a missing Service or port** · high · problem

An Ingress rule routes to a Service (or port) that does not exist, which returns 503/404.

### ingress-class-missing

**Ingress uses a class with no controller** · high · problem

ingressClassName names an IngressClass that does not exist, so no controller serves it.

### loadbalancer-pending

**LoadBalancer has no external address** · high · problem

A type=LoadBalancer Service has waited 5+ minutes without an IP/hostname.

### netpol-blocks-dns

**NetworkPolicy blocks DNS** · high · problem · lint

An egress NetworkPolicy selects pods but does not allow port 53, so name lookups time out.

### service-no-endpoints

**Service has no ready backends** · high · problem

Traffic to the Service fails: its selector matches no ready pods.

### service-port-mismatch

**Service targetPort does not match any container port** · high · problem · lint

targetPort refers to a named or numbered port the selected pods do not expose.

## Platform

### apiservice-unavailable

**Extension API is unavailable** · high · problem

An aggregated APIService (metrics-server, custom metrics, ...) is down: it breaks HPAs, `kubectl top`,
    API discovery and namespace deletion.

### dns-fragile

**Cluster DNS is a single point of failure** · medium · risk

CoreDNS with one replica, or with a CPU limit (throttling made DNS fail cluster-wide in public postmortems).

### dns-unhealthy

**Cluster DNS is unhealthy** · critical · problem

CoreDNS/kube-dns pods are not ready: every service-name lookup in the cluster may fail.

### namespace-stuck-terminating

**Namespace stuck deleting** · high · problem

A namespace has been Terminating for 10+ minutes, usually a finalizer or an unavailable APIService.

### platform-warning

**Platform problem reported in events** · high · problem

Matches recent Warning events against known platform failure patterns.

### webhook-unavailable

**Admission webhook has no backend** · critical · problem

A webhook with failurePolicy Fail points at a Service with no ready pods: matching API requests are rejected.

## Node

### karpenter-nodeclaim-failed

**Karpenter cannot launch nodes** · high · problem

A Karpenter NodeClaim has not become Ready: capacity errors, launch template or IAM problems.

### node-cordoned

**Node is cordoned** · low · risk

spec.unschedulable is true; often left behind after maintenance.

### node-not-ready

**Node is not ready** · critical · problem

The kubelet stopped reporting or reports itself unhealthy; pods on it are being lost.

### node-pressure

**Node is under resource pressure** · high · problem

MemoryPressure / DiskPressure / PIDPressure: the kubelet is evicting pods.

## Scheduling

### pod-pending

**Pod cannot be scheduled** · high · problem

Pod stays Pending; explains the scheduler's reason.

## Storage

### pvc-pending

**Volume claim is not bound** · high · problem

A PVC is Pending or Lost, so pods using it cannot start.

### pvc-stuck-terminating

**Volume claim stuck deleting** · medium · problem

PVC deletion is blocked by kubernetes.io/pvc-protection because a pod still mounts it.

### volume-mount-failed

**Volume cannot be attached or mounted** · high · problem

FailedMount / FailedAttachVolume events on pods that are not running.

## Resources

### hpa-cannot-scale

**Autoscaler cannot read metrics** · high · problem

ScalingActive=False: missing metrics-server, missing resource requests, or a bad custom metric.

### hpa-flapping

**Autoscaler keeps scaling up and down** · medium · problem

Frequent alternating rescales in the last hour: pods start and stop constantly, hurting latency and cost.

### hpa-maxed-out

**Autoscaler is at its maximum** · medium · problem

The HPA wants more replicas than maxReplicas allows, so load is not being absorbed.

### node-overcommitted

**Node requests are near capacity** · medium · problem

CPU or memory requests above 95% of allocatable: new or rescheduled pods will not fit.

### pod-evicted

**Pods were evicted** · medium · problem

The kubelet evicted pods under node pressure (memory, disk, ephemeral storage).

### pod-oomkilled

**Container was killed for using too much memory** · high · problem

The kernel killed a container because it exceeded its memory limit.

### quota-near-limit

**ResourceQuota almost used up** · medium · risk

At 90%+ of a quota, the next deploy or scale-up will be rejected.

### resources-missing

**No resource requests or memory limit** · medium · risk · lint

Without requests the scheduler overpacks nodes; without a memory limit one pod can starve a node.

## Reliability

### image-dockerhub-anonymous

**Images pulled from Docker Hub without credentials** · low · risk · lint

Anonymous Docker Hub pulls are rate limited per IP; scale-ups and node replacements can fail.

### image-mutable-tag

**Image uses latest or no tag** · medium · risk · lint

`latest` or untagged images change underneath you and make rollbacks impossible.

### probe-liveness-equals-readiness

**Liveness probe is the same as readiness** · medium · risk · lint

When a dependency slows down, identical probes restart every pod at once (cascading failure).

### probe-missing-readiness

**No readiness probe** · medium · risk · lint

Without a readiness probe, traffic reaches pods before they can serve and during shutdown.

### probe-no-startup

**Slow-start liveness without startupProbe** · low · risk · lint

A liveness probe that can fail within 30s of start and no startupProbe kills slow-starting apps.

### reliability-no-pdb

**No PodDisruptionBudget** · low · risk · lint

Without a PDB, node drains during upgrades can evict all replicas at once.

### reliability-single-replica

**Single replica** · low · risk · lint

One replica means downtime on every deploy, node drain and crash.

### replicas-single-node

**All replicas on one node or zone** · medium · risk

Several replicas but every pod is on the same node (or zone): one failure takes the whole app down.

### rollout-drops-traffic

**Rollouts can drop live traffic (502s)** · medium · risk · lint

Pods behind a Service with no preStop delay stop before load balancers stop routing to them,
    so every deploy returns 502s / connection resets for a few seconds.

## Security

### certificate-expiring

**TLS certificate expires soon** · high · problem

TLS Secrets whose certificate expires within 30 days (or already expired).

### certificate-not-ready

**cert-manager Certificate not ready** · high · problem

cert-manager cannot issue or renew a Certificate (DNS/HTTP challenge, issuer or rate-limit problems).

### rbac-cluster-admin-sa

**Service account has cluster-admin** · high · risk

A pod token with cluster-admin means anyone who compromises the pod owns the cluster.

### security-no-network-policy

**Namespaces with no NetworkPolicy** · low · risk

Without any NetworkPolicy every pod can reach every other pod: one compromised pod reaches all of them.

### security-plaintext-secret

**Secret value written directly in env** · high · risk · lint

An env var named like a password/token has a literal value: it is visible to anyone who can read the workload.

### security-privileged

**Privileged or host-level access** · high · risk · lint

privileged, hostNetwork/hostPID/hostIPC, hostPath or dangerous capabilities: a container escape means node takeover.

### security-pss-not-enforced

**Pod Security Standards not enforced** · low · risk

Application namespaces without a pod-security.kubernetes.io/enforce label accept privileged pods.

### security-runs-as-root

**Container may run as root** · low · risk · lint

No runAsNonRoot and allowPrivilegeEscalation not disabled (fails the 'restricted' Pod Security level).

### service-external-ips

**Service uses externalIPs** · low · risk · lint

spec.externalIPs is deprecated (1.36) and was the vector for CVE-2020-8554.

## Upgrade

### api-removed

**Manifest uses a removed or soon-removed API** · high · risk · lint

apiVersions that the target Kubernetes version no longer serves (manifests, Helm releases, last-applied).

### ingress-nginx-retired

**ingress-nginx controller is retired** · high · risk

The community ingress-nginx project was retired in March 2026: no more fixes or security patches.

### kube-proxy-ipvs

**kube-proxy IPVS mode is deprecated** · low · risk

IPVS mode was deprecated in Kubernetes 1.35; nftables is the successor.

### node-containerd-1x

**Node runs containerd 1.x** · medium · risk

Kubernetes 1.36 dropped support for containerd 1.x; nodes must move to containerd 2.x.

### node-version-skew

**Kubelet version too far behind the control plane** · medium · risk

Kubelets may be at most 3 minor versions older than the API server.

### pdb-blocks-drain

**PodDisruptionBudget allows no disruptions** · high · risk

disruptionsAllowed=0 makes node drains hang: node upgrades and autoscaler scale-down get stuck.

### security-gitrepo-volume

**gitRepo volume (disabled in 1.36)** · high · risk · lint

gitRepo volumes are a known remote-code-execution vector and are disabled from Kubernetes 1.36.

## Cost

### gpu-idle

**GPU capacity is idle** · medium · risk

GPU nodes with no pod requesting their GPUs. GPUs are the most expensive idle resource.
