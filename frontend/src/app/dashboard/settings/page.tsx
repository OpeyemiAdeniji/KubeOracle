"use client";

import { useUser } from "../layout";

function SettingsSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden", marginBottom: "20px" }}>
      <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)" }}>
        <h2 style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>{title}</h2>
      </div>
      <div>{children}</div>
    </div>
  );
}

function SettingsRow({
  label, description, value, tag,
}: {
  label: string; description?: string; value?: string; tag?: string;
}) {
  return (
    <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border-subtle)", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "16px" }}>
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <p style={{ fontSize: "13px", fontWeight: 500, color: "var(--text-primary)", margin: 0 }}>{label}</p>
          {tag && (
            <span style={{ fontSize: "10px", fontWeight: 600, padding: "1px 6px", borderRadius: "3px", background: "rgba(124,58,237,0.15)", border: "1px solid rgba(124,58,237,0.3)", color: "var(--text-accent)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
              {tag}
            </span>
          )}
        </div>
        {description && <p style={{ fontSize: "12px", color: "var(--text-muted)", margin: "3px 0 0" }}>{description}</p>}
      </div>
      {value && (
        <span style={{ fontSize: "13px", color: "var(--text-secondary)", flexShrink: 0, fontFamily: "var(--font-mono, monospace)" }}>
          {value}
        </span>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const user = useUser();

  const email: string = user?.email ?? "—";
  const userId: string = user?.id ?? "—";

  return (
    <div style={{ maxWidth: "720px", margin: "0 auto" }}>
      {/* Page header */}
      <div style={{ marginBottom: "28px" }}>
        <h1 style={{ fontSize: "20px", fontWeight: 700, color: "var(--text-primary)", margin: "0 0 4px" }}>Settings</h1>
        <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0 }}>
          Account and application configuration.
        </p>
      </div>

      {/* Account */}
      <SettingsSection title="Account">
        <SettingsRow label="Email" description="Your sign-in email address" value={email} />
        <SettingsRow label="User ID" description="Your unique account identifier" value={userId.slice(0, 16) + "…"} />
        <SettingsRow label="Auth Provider" description="Authentication method" value="InsForge" />
      </SettingsSection>

      {/* Cluster */}
      <SettingsSection title="Cluster Connection">
        <SettingsRow
          label="kubeconfig"
          description="Mount path inside the backend container — configured in docker-compose.yml"
          value="~/.kube/config"
        />
        <SettingsRow
          label="Backend URL"
          description="API endpoint the frontend connects to"
          value={typeof window !== "undefined" ? (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000") : "http://localhost:8000"}
        />
        <SettingsRow
          label="Investigation timeout"
          description="Maximum time allowed per investigation run"
          value="180s"
        />
      </SettingsSection>

      {/* AI */}
      <SettingsSection title="AI Reasoning">
        <SettingsRow
          label="AI Provider"
          description="LLM routing provider used for reasoning"
          value="OpenRouter"
        />
        <SettingsRow
          label="Default Model"
          description="Configurable via OPENROUTER_MODEL in backend/.env"
          value="claude-sonnet-4-6"
        />
        <SettingsRow
          label="Intent Classification"
          description="Classifies natural language queries to run targeted inspectors"
          tag="enabled"
        />
        <SettingsRow
          label="Targeted Investigation"
          description="For NL queries, only relevant inspectors run — skipping others for speed"
          tag="enabled"
        />
      </SettingsSection>

      {/* About */}
      <SettingsSection title="About KubeOracle">
        <SettingsRow label="Version" value="0.1.0" />
        <SettingsRow label="Frontend" value="Next.js 14 + Tailwind CSS 3.4" />
        <SettingsRow label="Backend" value="FastAPI + Python 3.11" />
        <SettingsRow label="Database" value="InsForge" />
        <SettingsRow
          label="Inspectors"
          description="Automated domain inspectors run during each investigation"
          value="13 inspectors / 11 domains"
        />
      </SettingsSection>
    </div>
  );
}
