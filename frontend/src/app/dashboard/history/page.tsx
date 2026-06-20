"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import axios from "axios";
import { insforge } from "@/lib/insforge";
import { useUser } from "../layout";
import { Investigation, SavedQuery } from "@/types";
import HistoryTable from "@/components/HistoryTable";

const API_BASE =
  typeof window !== "undefined"
    ? process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"
    : "http://localhost:8000";

type Tab = "investigations" | "saved-queries";

const SEVERITY_INLINE: Record<string, React.CSSProperties> = {
  critical: { background: "#450a0a", color: "#f87171", border: "1px solid #7f1d1d" },
  high:     { background: "#431407", color: "#fb923c", border: "1px solid #7c2d12" },
  medium:   { background: "#422006", color: "#facc15", border: "1px solid #713f12" },
  low:      { background: "#052e16", color: "#4ade80", border: "1px solid #14532d" },
};

const DOMAIN_LABELS: Record<string, string> = {
  pods: "Pods", logs: "Logs", events: "Events", deployments: "Deployments",
  network: "Network", nodes: "Nodes", resources: "Resources",
  config_secrets: "Config & Secrets", probes: "Probes", rbac: "RBAC", scheduling: "Scheduling",
};

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

// ── Saved Queries Tab ─────────────────────────────────────────────────────────

const PAGE_SIZE = 10;

interface SavedQueriesTabProps {
  userId: string;
}

function SavedQueriesTab({ userId }: SavedQueriesTabProps) {
  const [queries, setQueries] = useState<SavedQuery[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const mountedRef = useRef(true);

  const loadQueries = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API_BASE}/query/history?user_id=${encodeURIComponent(userId)}`, { timeout: 10_000 });
      if (mountedRef.current) setQueries(data.queries ?? []);
    } catch {
      if (mountedRef.current) setQueries([]);
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    mountedRef.current = true;
    loadQueries();

    // Refresh when the Query page successfully saves a new entry
    const onSaved = () => { if (mountedRef.current) loadQueries(); };
    window.addEventListener("savedQueriesUpdated", onSaved);

    return () => {
      mountedRef.current = false;
      window.removeEventListener("savedQueriesUpdated", onSaved);
    };
  }, [loadQueries]);

  const handleDelete = async (id: number) => {
    setDeleting(id);
    try {
      await insforge.database.from("saved_queries").delete().eq("id", id);
      if (mountedRef.current) setQueries((prev) => prev.filter((q) => q.id !== id));
    } finally {
      if (mountedRef.current) setDeleting(null);
    }
  };

  const filtered = search.trim()
    ? queries.filter((q) => q.question.toLowerCase().includes(search.trim().toLowerCase()))
    : queries;

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const paginated = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const setSearchAndReset = (v: string) => { setSearch(v); setPage(1); setExpandedId(null); };

  // Empty state
  if (!loading && queries.length === 0) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "60px 24px", background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8 }}>
        <div style={{ width: 44, height: 44, borderRadius: 8, background: "var(--bg-surface-2)", border: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "center", marginBottom: 14 }}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" style={{ color: "var(--text-muted)" }}>
            <path d="M17 3H7a2 2 0 00-2 2v16l7-3 7 3V5a2 2 0 00-2-2z" />
          </svg>
        </div>
        <p style={{ fontSize: 14, fontWeight: 600, color: "var(--text-secondary)", margin: "0 0 6px" }}>No saved queries yet</p>
        <p style={{ fontSize: 13, color: "var(--text-muted)", textAlign: "center", margin: 0, maxWidth: 320, lineHeight: 1.6 }}>
          Save important query results using the bookmark button on the Query page.
        </p>
      </div>
    );
  }

  return (
    <div>
      {/* Toolbar */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        {/* Search */}
        <div style={{ position: "relative", flex: 1, maxWidth: 320 }}>
          <svg style={{ position: "absolute", left: 9, top: "50%", transform: "translateY(-50%)", pointerEvents: "none", color: "var(--text-muted)" }}
            width="13" height="13" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input type="text" value={search} onChange={(e) => setSearchAndReset(e.target.value)}
            placeholder="Filter by question…"
            style={{ width: "100%", background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: "7px 28px 7px 30px", fontSize: 13, color: "var(--text-primary)", outline: "none" }}
            onFocus={(e) => (e.target.style.borderColor = "var(--primary)")}
            onBlur={(e) => (e.target.style.borderColor = "var(--border)")} />
          {search && (
            <button onClick={() => setSearchAndReset("")}
              style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", display: "flex" }}>
              <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
            </button>
          )}
        </div>

        {/* Manual refresh */}
        <button
          onClick={loadQueries}
          disabled={loading}
          title="Refresh saved queries"
          style={{ flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", width: 30, height: 30, borderRadius: 5, background: "none", border: "1px solid var(--border)", color: "var(--text-muted)", cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.4 : 1, transition: "all 0.12s" }}
          onMouseEnter={(e) => { if (!loading) { (e.currentTarget as HTMLElement).style.borderColor = "var(--primary)"; (e.currentTarget as HTMLElement).style.color = "var(--text-primary)"; } }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = "var(--border)"; (e.currentTarget as HTMLElement).style.color = "var(--text-muted)"; }}
        >
          <svg width="13" height="13" fill="none" viewBox="0 0 24 24" stroke="currentColor" className={loading ? "animate-spin" : ""}>
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        </button>

        <span style={{ fontSize: 12, color: "var(--text-muted)", marginLeft: "auto" }}>
          {loading ? "Loading…" : `${filtered.length} saved query${filtered.length !== 1 ? "s" : ""}`}
        </span>
      </div>

      {/* Table */}
      <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden" }}>
        {/* Header */}
        <div style={{ display: "grid", gridTemplateColumns: "140px 120px 80px 80px 1fr 40px", gap: 0, background: "var(--bg-surface-2)", borderBottom: "1px solid var(--border)", padding: "0 16px" }}>
          {["Date", "Cluster", "Severity", "Conf.", "Question", ""].map((h, i) => (
            <div key={i} style={{ padding: "9px 0", fontSize: 11, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {h}
            </div>
          ))}
        </div>

        {loading ? (
          <div style={{ padding: "24px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="skeleton-shimmer" style={{ height: 36, borderRadius: 4 }} />
            ))}
          </div>
        ) : !paginated.length ? (
          <div style={{ padding: "28px 16px", textAlign: "center" }}>
            <p style={{ fontSize: 13, color: "var(--text-muted)" }}>No results for "{search}"</p>
          </div>
        ) : (
          paginated.map((q) => {
            const sStyle = SEVERITY_INLINE[q.severity] ?? SEVERITY_INLINE.low;
            const isExpanded = expandedId === q.id;
            const isDeleting = deleting === q.id;

            return (
              <div key={q.id} style={{ borderBottom: "1px solid var(--border)" }}>
                {/* Row */}
                <div
                  onClick={() => setExpandedId(isExpanded ? null : q.id)}
                  style={{
                    display: "grid", gridTemplateColumns: "140px 120px 80px 80px 1fr 40px",
                    gap: 0, padding: "0 16px", cursor: "pointer", transition: "background 0.1s",
                  }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = ""; }}
                >
                  <div style={{ padding: "11px 0", fontSize: 12, color: "var(--text-muted)" }}>{formatDate(q.created_at)}</div>
                  <div style={{ padding: "11px 0", fontSize: 12, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", paddingRight: 8 }}
                    title={q.context}>{q.context || "—"}</div>
                  <div style={{ padding: "11px 0" }}>
                    <span style={{ ...sStyle, display: "inline-flex", alignItems: "center", padding: "2px 7px", borderRadius: 3, fontSize: 10, fontWeight: 700, textTransform: "uppercase" }}>
                      {q.severity}
                    </span>
                  </div>
                  <div style={{ padding: "11px 0", fontSize: 12, color: "var(--text-secondary)" }}>{q.confidence}%</div>
                  <div style={{ padding: "11px 0", fontSize: 13, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", paddingRight: 8 }}>
                    {q.question}
                  </div>
                  <div style={{ padding: "6px 0", display: "flex", alignItems: "center", justifyContent: "flex-end" }}>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDelete(q.id); }}
                      disabled={isDeleting}
                      title="Delete saved query"
                      style={{ padding: "5px", borderRadius: 4, background: "none", border: "none", cursor: "pointer", color: "var(--text-muted)", display: "flex", opacity: isDeleting ? 0.4 : 1 }}
                      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = "#f87171"; (e.currentTarget as HTMLElement).style.background = "rgba(239,68,68,0.1)"; }}
                      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = "var(--text-muted)"; (e.currentTarget as HTMLElement).style.background = "none"; }}
                    >
                      <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>
                </div>

                {/* Expanded answer */}
                {isExpanded && (
                  <div style={{ padding: "0 16px 16px", borderTop: "1px solid var(--border)", background: "var(--bg-surface-2)" }}>
                    <p style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.7, margin: "14px 0 0", whiteSpace: "pre-wrap" }}>{q.answer}</p>

                    {q.domains_checked?.length > 0 && (
                      <div style={{ marginTop: 12 }}>
                        <p style={{ fontSize: 11, color: "var(--text-muted)", margin: "0 0 6px" }}>Domains checked</p>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                          {q.domains_checked.map((d) => (
                            <span key={d} style={{ fontSize: 11, padding: "2px 8px", borderRadius: 4, background: "rgba(124,58,237,0.1)", border: "1px solid rgba(124,58,237,0.25)", color: "var(--text-accent)" }}>
                              {DOMAIN_LABELS[d] ?? d}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {q.kubectl_commands?.length > 0 && (
                      <div style={{ marginTop: 12 }}>
                        <p style={{ fontSize: 11, color: "var(--text-muted)", margin: "0 0 6px" }}>Fix commands</p>
                        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                          {q.kubectl_commands.map((cmd, i) => (
                            <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 8, background: "#010409", border: "1px solid #1c2028", borderRadius: 5, padding: "7px 12px" }}>
                              <span style={{ fontSize: 12, fontFamily: "var(--font-mono, monospace)", color: "#52525b", flexShrink: 0, userSelect: "none" }}>$</span>
                              <code style={{ flex: 1, fontSize: 13, color: "#7ee787", fontFamily: "var(--font-mono, monospace)", wordBreak: "break-all", lineHeight: 1.5 }}>{cmd}</code>
                              <button onClick={() => navigator.clipboard.writeText(cmd).catch(() => {})}
                                style={{ flexShrink: 0, padding: "2px 7px", borderRadius: 3, background: "none", border: "1px solid var(--border)", color: "var(--text-muted)", cursor: "pointer", fontSize: 11 }}>
                                copy
                              </button>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Pagination */}
      {!loading && totalPages > 1 && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 12, padding: "8px 0" }}>
          <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={currentPage === 1}
            style={{ fontSize: 12, color: "var(--text-secondary)", background: "none", border: "none", cursor: currentPage === 1 ? "not-allowed" : "pointer", opacity: currentPage === 1 ? 0.3 : 1, padding: "4px 8px" }}>
            ← Previous
          </button>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Page {currentPage} of {totalPages}</span>
          <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={currentPage === totalPages}
            style={{ fontSize: 12, color: "var(--text-secondary)", background: "none", border: "none", cursor: currentPage === totalPages ? "not-allowed" : "pointer", opacity: currentPage === totalPages ? 0.3 : 1, padding: "4px 8px" }}>
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function HistoryPage() {
  const user = useUser();
  const [activeTab, setActiveTab] = useState<Tab>("investigations");
  const [history, setHistory] = useState<Investigation[]>([]);
  const [loading, setLoading] = useState(true);
  const mountedRef = useRef(true);

  const loadHistory = useCallback(async () => {
    if (!user) return;
    setLoading(true);
    const { data } = await insforge.database
      .from("investigations")
      .select()
      .eq("user_id", user.id)
      .order("created_at", { ascending: false })
      .limit(500);
    if (mountedRef.current) {
      setHistory((data as Investigation[]) ?? []);
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    mountedRef.current = true;
    loadHistory();
    return () => { mountedRef.current = false; };
  }, [loadHistory]);

  const handleDeleteItem = async (id: number) => {
    await insforge.database.from("investigations").delete().eq("id", id);
    if (mountedRef.current) loadHistory();
  };

  const handleExportCSV = () => {
    if (!history.length) return;
    const header = ["id", "date", "cluster", "severity", "confidence", "root_cause", "status"];
    const rows = history.map((inv) => [
      inv.id, inv.created_at, inv.context || "",
      inv.severity, inv.confidence,
      `"${(inv.root_cause || "").replace(/"/g, '""')}"`,
      inv.status,
    ]);
    const csv = [header.join(","), ...rows.map((r) => r.join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `kubeoracle-history-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Tab button style
  const tabStyle = (tab: Tab): React.CSSProperties => ({
    padding: "8px 16px", fontSize: 13, fontWeight: activeTab === tab ? 600 : 400,
    color: activeTab === tab ? "var(--text-primary)" : "var(--text-secondary)",
    background: "none", border: "none", cursor: "pointer", position: "relative",
    borderBottom: `2px solid ${activeTab === tab ? "var(--primary)" : "transparent"}`,
    transition: "color 0.15s, border-color 0.15s",
    marginBottom: "-1px",
  });

  return (
    <div style={{ maxWidth: "1100px", margin: "0 auto" }}>
      {/* Page header */}
      <div style={{ marginBottom: "20px", display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
        <div>
          <h1 style={{ fontSize: "20px", fontWeight: 700, color: "var(--text-primary)", margin: "0 0 4px" }}>History</h1>
          <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0 }}>
            Past investigations and saved query results.
          </p>
        </div>
        {activeTab === "investigations" && history.length > 0 && (
          <button
            onClick={handleExportCSV}
            style={{ display: "inline-flex", alignItems: "center", gap: "6px", padding: "7px 14px", background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "6px", color: "var(--text-secondary)", fontSize: "13px", cursor: "pointer", flexShrink: 0 }}
          >
            <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
            Export CSV
          </button>
        )}
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", borderBottom: "1px solid var(--border)", marginBottom: "20px" }}>
        <button style={tabStyle("investigations")} onClick={() => setActiveTab("investigations")}>
          Investigations
          {history.length > 0 && (
            <span style={{ marginLeft: 7, fontSize: 11, fontWeight: 600, padding: "1px 6px", borderRadius: 10, background: "var(--bg-surface-2)", color: "var(--text-muted)" }}>
              {history.length}
            </span>
          )}
        </button>
        <button style={tabStyle("saved-queries")} onClick={() => setActiveTab("saved-queries")}>
          Saved Queries
          <span style={{ marginLeft: 7, fontSize: 11, fontWeight: 600, padding: "1px 6px", borderRadius: 10, background: activeTab === "saved-queries" ? "rgba(124,58,237,0.15)" : "var(--bg-surface-2)", color: activeTab === "saved-queries" ? "var(--text-accent)" : "var(--text-muted)" }}>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" style={{ display: "inline", verticalAlign: "middle" }}>
              <path d="M17 3H7a2 2 0 00-2 2v16l7-3 7 3V5a2 2 0 00-2-2z" />
            </svg>
          </span>
        </button>
      </div>

      {/* Tab content */}
      {activeTab === "investigations" ? (
        <HistoryTable history={history} onDeleteItem={handleDeleteItem} loading={loading} />
      ) : (
        user && <SavedQueriesTab userId={user.id} />
      )}
    </div>
  );
}
