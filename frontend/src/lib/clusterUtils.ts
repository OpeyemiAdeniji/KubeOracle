export type CloudProvider = "eks" | "kind" | "minikube" | "k8s";

export interface ClusterMeta {
  displayName: string;
  provider: CloudProvider;
  region: string | null;
}

/**
 * Extracts a human-readable name, cloud provider, and region from a
 * raw Kubernetes context name.
 *
 * Handles:
 *   - AWS EKS ARN:   arn:aws:eks:us-east-1:107876700304:cluster/Netflix  → "Netflix", eks, us-east-1
 *   - eksctl format: alice@my-cluster.us-west-2.eksctl.io                → "my-cluster", eks, us-west-2
 *   - kind:          kind-kubernetes-cluster                              → "kind-kubernetes-cluster", kind, null
 *   - minikube:      minikube                                            → "minikube", minikube, null
 *   - plain:         my-cluster                                          → "my-cluster", k8s, null
 */
export function parseClusterContext(context: string): ClusterMeta {
  // AWS EKS ARN: arn:aws:eks:<region>:<account>:cluster/<name>
  const arnMatch = context.match(/^arn:aws:eks:([^:]+):[^:]+:cluster\/(.+)$/);
  if (arnMatch) {
    return { displayName: arnMatch[2], provider: "eks", region: arnMatch[1] };
  }

  // eksctl format: [user@]<cluster>.<region>.eksctl.io
  if (context.includes("eksctl.io")) {
    const host = context.includes("@") ? context.split("@")[1] : context;
    const parts = host.split(".");
    const eksIdx = parts.indexOf("eksctl");
    const displayName = parts[0] ?? context;
    // Region is the segment immediately before "eksctl"
    const region = eksIdx >= 1 ? parts[eksIdx - 1] : null;
    return { displayName, provider: "eks", region };
  }

  // kind cluster
  if (context.startsWith("kind-")) {
    return { displayName: context, provider: "kind", region: null };
  }

  // minikube
  if (context === "minikube" || context.startsWith("minikube-")) {
    return { displayName: context, provider: "minikube", region: null };
  }

  // Fallback: use context as-is
  return { displayName: context, provider: "k8s", region: null };
}

export const PROVIDER_BADGE: Record<CloudProvider, { label: string; bg: string; text: string; border: string }> = {
  eks:      { label: "EKS",      bg: "rgba(255,153,0,0.12)",  text: "#FF9900", border: "rgba(255,153,0,0.3)" },
  kind:     { label: "kind",     bg: "rgba(96,165,250,0.12)", text: "#60a5fa", border: "rgba(96,165,250,0.3)" },
  minikube: { label: "minikube", bg: "rgba(74,222,128,0.12)", text: "#4ade80", border: "rgba(74,222,128,0.3)" },
  k8s:      { label: "K8s",      bg: "rgba(139,148,158,0.1)", text: "#8b949e", border: "rgba(139,148,158,0.2)" },
};
