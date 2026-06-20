export interface Diagnosis {
  root_cause: string;
  explanation: string;
  fix: string;
  kubectl_commands: string[];
  prevention: string;
  contributing_factors: string[];
  severity: "critical" | "high" | "medium" | "low";
  confidence: number;
}

export interface Investigation {
  id: number;
  user_id: string;
  session_id: string;
  root_cause: string;
  severity: string;
  confidence: number;
  namespace: string;
  status: string;
  context: string;
  full_diagnosis: Diagnosis;
  created_at: string;
}

export type StepStatus = "pending" | "active" | "complete" | "failed";

export interface Step {
  id: string;
  label: string;
  status: StepStatus;
}

export type AppStatus = "idle" | "investigating" | "success" | "error";

export interface Cluster {
  name: string;
  context: string;
  server: string;
  current: boolean;
}

export interface QueryResult {
  status: string;
  question: string;
  answer: string;
  investigation: {
    domains_checked: string[];
    target_resource: string | null;
    target_namespace: string;
  };
  kubectl_commands: string[];
  confidence: number;
  severity: "critical" | "high" | "medium" | "low";
}

export interface SavedQuery {
  id: number;
  user_id: string;
  question: string;
  answer: string;
  kubectl_commands: string[];
  domains_checked: string[];
  confidence: number;
  severity: string;
  context: string;
  created_at: string;
}

export const INVESTIGATION_STEPS: Omit<Step, "status">[] = [
  { id: "pods", label: "Checking Pods" },
  { id: "logs", label: "Reading Container Logs" },
  { id: "events", label: "Analyzing Cluster Events" },
  { id: "deployments", label: "Inspecting Deployments" },
  { id: "network", label: "Checking Networking" },
  { id: "nodes", label: "Inspecting Nodes" },
  { id: "resources", label: "Checking Resources & Storage" },
  { id: "config_secrets", label: "Inspecting ConfigMaps & Secrets" },
  { id: "probes", label: "Checking Probes" },
  { id: "rbac", label: "Checking RBAC" },
  { id: "scheduling", label: "Checking Scheduling" },
  { id: "ai_reasoning", label: "AI Reasoning (Claude Sonnet 4.6)" },
  { id: "diagnosis", label: "Generating Diagnosis" },
];
