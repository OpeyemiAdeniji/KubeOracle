"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import axios from "axios";
import { insforge } from "@/lib/insforge";
import { useUser } from "./layout";
import { Cluster, Investigation } from "@/types";
import ClusterHealthGrid from "@/components/ClusterHealthGrid";

const API_BASE =
  typeof window !== "undefined"
    ? process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"
    : "http://localhost:8000";

const SEVERITY_COLORS: Record<string, string> = {
  critical: "#ef4444",
  high: "#f97316",
  medium: "#facc15",
  low: "#4ade80",
};

function useCountUp(target: number, duration = 800): number {
  const [count, setCount] = useState(0);
  const frameRef = useRef<number>(0);
  useEffect(() => {
    if (target <= 0) { setCount(0); return; }
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      setCount(Math.round((1 - Math.pow(1 - t, 2)) * target));
      if (t < 1) frameRef.current = requestAnimationFrame(tick);
    };
    frameRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frameRef.current);
  }, [target, duration]);
  return count;
}

function StatCard({ label, value, sub, color }: { label: string; value: string | number; sub?: string; color?: string }) {
  return (
    <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "8px", padding: "16px 18px" }}>
      <p style={{ fontSize: "12px", color: "var(--text-muted)", margin: "0 0 6px" }}>{label}</p>
      <p style={{ fontSize: "22px", fontWeight: 700, color: color || "var(--text-primary)", margin: "0 0 2px", lineHeight: 1 }}>{value}</p>
      {sub && <p style={{ fontSize: "11px", color: "var(--text-muted)", margin: 0 }}>{sub}</p>}
    </div>
  );
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

export default function OverviewPage() {
  const user = useUser();
  const router = useRouter();
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [clustersLoading, setClustersLoading] = useState(true);
  const [history, setHistory] = useState<Investigation[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const mountedRef = useRef(true);

  const loadClusters = useCallback(async () => {
    setClustersLoading(true);
    try {
      const { data } = await axios.get(`${API_BASE}/clusters`, { timeout: 10_000 });
      if (mountedRef.current) setClusters(data.clusters || []);
    } catch {
      if (mountedRef.current) setClusters([]);
    } finally {
      if (mountedRef.current) setClustersLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    if (!user) return;
    setHistoryLoading(true);
    const { data } = await insforge.database
      .from("investigations")
      .select()
      .eq("user_id", user.id)
      .order("created_at", { ascending: false })
      .limit(100);
    if (mountedRef.current) {
      setHistory((data as Investigation[]) ?? []);
      setHistoryLoading(false);
    }
  }, [user]);

  useEffect(() => {
    mountedRef.current = true;
    loadClusters();
    loadHistory();
    return () => { mountedRef.current = false; };
  }, [loadClusters, loadHistory]);

  const total = history.length;
  const avgConf = total > 0 ? Math.round(history.reduce((s, h) => s + h.confidence, 0) / total) : 0;
  const severityCounts = history.reduce((acc, h) => { acc[h.severity] = (acc[h.severity] || 0) + 1; return acc; }, {} as Record<string, number>);
  const mostCommon = Object.entries(severityCounts).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;

  const animTotal = useCountUp(total);
  const animConf = useCountUp(avgConf);

  const recent5 = history.slice(0, 5);

  return (
    <div style={{ maxWidth: "1100px", margin: "0 auto" }}>
      {/* Page header */}
      <div style={{ marginBottom: "24px" }}>
        <h1 style={{ fontSize: "20px", fontWeight: 700, color: "var(--text-primary)", margin: "0 0 4px" }}>Overview</h1>
        <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0 }}>
          Welcome back{user?.email ? `, ${user.email.split("@")[0]}` : ""}. Here's the status of your Kubernetes clusters.
        </p>
      </div>

      {/* Stats row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "12px", marginBottom: "28px" }}>
        <StatCard label="Total Investigations" value={historyLoading ? "—" : animTotal} />
        <StatCard
          label="Most Common Severity"
          value={historyLoading ? "—" : mostCommon ? mostCommon.toUpperCase() : "—"}
          color={mostCommon ? SEVERITY_COLORS[mostCommon] : undefined}
        />
        <StatCard label="Avg Confidence" value={historyLoading ? "—" : total > 0 ? `${animConf}%` : "—"} />
        <StatCard
          label="Active Clusters"
          value={clustersLoading ? "—" : clusters.length}
          sub={clusters.find((c) => c.current)?.name}
        />
      </div>

      {/* Cluster health grid */}
      <div style={{ marginBottom: "28px" }}>
        <ClusterHealthGrid
          clusters={clusters}
          investigations={history}
          loading={clustersLoading}
        />
      </div>

      {/* Quick actions */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "28px" }}>
        <button
          onClick={() => router.push("/dashboard/investigate")}
          style={{ background: "var(--primary)", border: "none", borderRadius: "8px", padding: "14px 18px", color: "#fff", textAlign: "left", cursor: "pointer", display: "flex", alignItems: "center", gap: "10px" }}
        >
          <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
          </svg>
          <div>
            <p style={{ margin: 0, fontSize: "14px", fontWeight: 600 }}>Run Investigation</p>
            <p style={{ margin: 0, fontSize: "12px", opacity: 0.8 }}>Deep-scan your cluster with AI</p>
          </div>
        </button>
        <button
          onClick={() => router.push("/dashboard/query")}
          style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "8px", padding: "14px 18px", color: "var(--text-primary)", textAlign: "left", cursor: "pointer", display: "flex", alignItems: "center", gap: "10px" }}
        >
          <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
          <div>
            <p style={{ margin: 0, fontSize: "14px", fontWeight: 600 }}>Ask a Question</p>
            <p style={{ margin: 0, fontSize: "12px", color: "var(--text-muted)" }}>Natural language cluster queries</p>
          </div>
        </button>
      </div>

      {/* Recent activity */}
      {(recent5.length > 0 || historyLoading) && (
        <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden" }}>
          <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <h2 style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>Recent Activity</h2>
            <button
              onClick={() => router.push("/dashboard/history")}
              style={{ fontSize: "12px", color: "var(--text-accent)", background: "none", border: "none", cursor: "pointer", padding: 0 }}
            >
              View all →
            </button>
          </div>

          {historyLoading ? (
            <div style={{ padding: "12px 0" }}>
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} style={{ padding: "10px 18px", display: "flex", gap: "12px", alignItems: "center" }}>
                  <div className="skeleton-shimmer" style={{ height: "12px", width: "80px", borderRadius: "4px" }} />
                  <div className="skeleton-shimmer" style={{ height: "20px", width: "60px", borderRadius: "4px" }} />
                  <div className="skeleton-shimmer" style={{ height: "12px", flex: 1, borderRadius: "4px" }} />
                </div>
              ))}
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    {["Date", "Cluster", "Severity", "Confidence", "Root Cause"].map((h) => (
                      <th key={h} style={{ padding: "10px 18px", textAlign: "left", fontSize: "11px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {recent5.map((inv) => (
                    <tr key={inv.id} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                      <td style={{ padding: "10px 18px", color: "var(--text-muted)", whiteSpace: "nowrap" }}>{formatDate(inv.created_at)}</td>
                      <td style={{ padding: "10px 18px", color: "var(--text-secondary)", maxWidth: "120px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{inv.context || "—"}</td>
                      <td style={{ padding: "10px 18px" }}>
                        <span style={{ fontSize: "11px", fontWeight: 600, padding: "2px 8px", borderRadius: "4px", textTransform: "uppercase", letterSpacing: "0.04em", color: SEVERITY_COLORS[inv.severity] || "var(--text-secondary)", background: `${SEVERITY_COLORS[inv.severity]}18` }}>
                          {inv.severity}
                        </span>
                      </td>
                      <td style={{ padding: "10px 18px", color: "var(--text-secondary)" }}>{inv.confidence}%</td>
                      <td style={{ padding: "10px 18px", color: "var(--text-secondary)", maxWidth: "280px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{inv.root_cause}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {!historyLoading && history.length === 0 && (
        <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "8px", padding: "40px 24px", textAlign: "center" }}>
          <p style={{ fontSize: "14px", color: "var(--text-secondary)", margin: "0 0 4px" }}>No investigations yet</p>
          <p style={{ fontSize: "12px", color: "var(--text-muted)", margin: "0 0 16px" }}>Run your first investigation to see cluster health history here</p>
          <button
            onClick={() => router.push("/dashboard/investigate")}
            style={{ padding: "8px 20px", background: "var(--primary)", border: "none", borderRadius: "6px", color: "#fff", fontSize: "13px", fontWeight: 600, cursor: "pointer" }}
          >
            Run Investigation
          </button>
        </div>
      )}
    </div>
  );
}
