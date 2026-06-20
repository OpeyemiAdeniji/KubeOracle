"use client";

import { useRouter } from "next/navigation";
import { Cluster, Investigation } from "@/types";
import { parseClusterContext, PROVIDER_BADGE } from "@/lib/clusterUtils";

const MAX_VISIBLE = 6;

interface Props {
  clusters: Cluster[];
  investigations: Investigation[];
  loading?: boolean;
}

const HEALTH_DOT: Record<string, { color: string; label: string }> = {
  critical: { color: "#ef4444", label: "Critical" },
  high:     { color: "#f97316", label: "High" },
  medium:   { color: "#facc15", label: "Warning" },
  low:      { color: "#4ade80", label: "Healthy" },
};
const UNKNOWN_DOT = { color: "#3a3a45", label: "Unknown" };

function formatRelative(iso: string): string {
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch { return ""; }
}

function SkeletonCard() {
  return (
    <div style={{
      background: "var(--bg-surface-2)", border: "1px solid var(--border)",
      borderRadius: 8, padding: "14px 16px", height: 84, boxSizing: "border-box",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <div className="skeleton-shimmer" style={{ width: 7, height: 7, borderRadius: "50%" }} />
          <div className="skeleton-shimmer" style={{ height: 12, width: 90, borderRadius: 4 }} />
        </div>
        <div className="skeleton-shimmer" style={{ height: 16, width: 32, borderRadius: 3 }} />
      </div>
      <div className="skeleton-shimmer" style={{ height: 10, width: 64, borderRadius: 3, marginBottom: 10 }} />
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <div className="skeleton-shimmer" style={{ height: 10, width: 48, borderRadius: 3 }} />
        <div className="skeleton-shimmer" style={{ height: 10, width: 52, borderRadius: 3 }} />
      </div>
    </div>
  );
}

interface CardProps {
  cluster: Cluster;
  severity: string | null;
  lastSeen: string | null;
  onNavigate: () => void;
}

function ClusterCard({ cluster, severity, lastSeen, onNavigate }: CardProps) {
  const meta = parseClusterContext(cluster.context);
  const badge = PROVIDER_BADGE[meta.provider];
  const dot = severity ? (HEALTH_DOT[severity] ?? UNKNOWN_DOT) : UNKNOWN_DOT;
  const glows = severity && severity !== "low";

  return (
    <button
      onClick={onNavigate}
      title={cluster.context}
      style={{
        background: "var(--bg-surface-2)",
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: "12px 14px",
        textAlign: "left",
        cursor: "pointer",
        transition: "border-color 0.14s, background 0.14s",
        width: "100%",
        height: 84,
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        boxSizing: "border-box",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.borderColor = "var(--primary)";
        (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.borderColor = "var(--border)";
        (e.currentTarget as HTMLElement).style.background = "var(--bg-surface-2)";
      }}
    >
      {/* Row 1: health dot + display name + provider badge */}
      <div style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0 }}>
        <div style={{
          width: 7, height: 7, borderRadius: "50%", background: dot.color, flexShrink: 0,
          boxShadow: glows ? `0 0 5px ${dot.color}99` : undefined,
        }} />
        <span style={{
          fontSize: 13, fontWeight: 600, color: "var(--text-primary)",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1,
          minWidth: 0,
        }}>
          {meta.displayName}
        </span>
        {cluster.current && (
          <span style={{
            fontSize: 9, fontWeight: 700, color: "var(--text-accent)",
            background: "rgba(124,58,237,0.18)", border: "1px solid rgba(124,58,237,0.35)",
            borderRadius: 3, padding: "1px 5px", flexShrink: 0,
            textTransform: "uppercase", letterSpacing: "0.05em",
          }}>
            active
          </span>
        )}
        <span style={{
          fontSize: 10, fontWeight: 600, color: badge.text,
          background: badge.bg, border: `1px solid ${badge.border}`,
          borderRadius: 3, padding: "1px 6px", flexShrink: 0,
        }}>
          {badge.label}
        </span>
      </div>

      {/* Row 2: region or server */}
      <p style={{
        fontSize: 11, color: "var(--text-muted)", margin: 0,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>
        {meta.region ?? cluster.server ?? cluster.context}
      </p>

      {/* Row 3: health label + timestamp */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: dot.color }}>
          {dot.label}
        </span>
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
          {lastSeen ? formatRelative(lastSeen) : "Never investigated"}
        </span>
      </div>
    </button>
  );
}

export default function ClusterHealthGrid({ clusters, investigations, loading }: Props) {
  const router = useRouter();

  // Build latest severity + timestamp per context from investigation history
  const healthMap: Record<string, { severity: string; lastSeen: string }> = {};
  investigations.forEach((inv) => {
    if (inv.context && !healthMap[inv.context]) {
      healthMap[inv.context] = { severity: inv.severity, lastSeen: inv.created_at };
    }
  });

  if (loading) {
    return (
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 10 }}>
        {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
      </div>
    );
  }

  if (!clusters.length) {
    return (
      <div style={{
        padding: "24px", background: "var(--bg-surface)", border: "1px solid var(--border)",
        borderRadius: 8, textAlign: "center",
      }}>
        <p style={{ fontSize: 13, color: "var(--text-muted)" }}>No clusters found</p>
        <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
          Mount your kubeconfig and restart the backend.
        </p>
      </div>
    );
  }

  const visible = clusters.slice(0, MAX_VISIBLE);
  const remaining = clusters.length - MAX_VISIBLE;

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 10 }}>
        {visible.map((cluster) => {
          const h = healthMap[cluster.context] ?? null;
          return (
            <ClusterCard
              key={cluster.context}
              cluster={cluster}
              severity={h?.severity ?? null}
              lastSeen={h?.lastSeen ?? null}
              onNavigate={() => router.push(`/dashboard/investigate?context=${encodeURIComponent(cluster.context)}`)}
            />
          );
        })}
      </div>

      {/* View all button — always shown if there are any clusters (clean CTA) */}
      <button
        onClick={() => router.push("/dashboard/investigate")}
        style={{
          marginTop: 10, width: "100%", padding: "8px 0",
          background: "var(--bg-surface)", border: "1px solid var(--border)",
          borderRadius: 8, fontSize: 12, color: "var(--text-secondary)",
          cursor: "pointer", transition: "background 0.14s, color 0.14s",
          display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)";
          (e.currentTarget as HTMLElement).style.color = "var(--text-primary)";
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLElement).style.background = "var(--bg-surface)";
          (e.currentTarget as HTMLElement).style.color = "var(--text-secondary)";
        }}
      >
        {remaining > 0
          ? `View all ${clusters.length} clusters in Investigate →`
          : "View all clusters in Investigate →"}
      </button>
    </div>
  );
}
