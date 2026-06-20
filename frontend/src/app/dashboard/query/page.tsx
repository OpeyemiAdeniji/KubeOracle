"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import axios from "axios";
import { useUser } from "../layout";
import { Cluster, QueryResult } from "@/types";
import QueryBox from "@/components/QueryBox";
import QueryResultCard from "@/components/QueryResultCard";

const API_BASE =
  typeof window !== "undefined"
    ? process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"
    : "http://localhost:8000";

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: "#ef4444", high: "#f97316", medium: "#facc15", low: "#4ade80",
};

function BookmarkFilled() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style={{ color: "var(--text-accent)", flexShrink: 0 }}>
      <path d="M17 3H7a2 2 0 00-2 2v16l7-3 7 3V5a2 2 0 00-2-2z" />
    </svg>
  );
}

export default function QueryPage() {
  const user = useUser();
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [clustersLoading, setClustersLoading] = useState(true);
  const [selectedContext, setSelectedContext] = useState("");
  const [queryResult, setQueryResult] = useState<QueryResult | null>(null);
  const [queryLoading, setQueryLoading] = useState(false);
  const [sessionHistory, setSessionHistory] = useState<Array<QueryResult & { ts: string }>>([]);
  // Set of question strings saved to DB in this session
  const [savedQuestions, setSavedQuestions] = useState<Set<string>>(new Set());
  const mountedRef = useRef(true);

  const loadClusters = useCallback(async () => {
    setClustersLoading(true);
    try {
      const { data } = await axios.get(`${API_BASE}/clusters`, { timeout: 10_000 });
      const list: Cluster[] = data.clusters || [];
      if (mountedRef.current) {
        setClusters(list);
        const current = list.find((c) => c.current);
        setSelectedContext(current?.context ?? list[0]?.context ?? "");
      }
    } catch {
      if (mountedRef.current) setClusters([]);
    } finally {
      if (mountedRef.current) setClustersLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    loadClusters();
    return () => { mountedRef.current = false; };
  }, [loadClusters]);

  const handleQuery = async (question: string) => {
    if (queryLoading) return;
    setQueryLoading(true);
    setQueryResult(null);
    try {
      const { data } = await axios.post(
        `${API_BASE}/query`,
        { question, context: selectedContext },
        { timeout: 120_000 }
      );
      const result = data as QueryResult;
      setQueryResult(result);
      setSessionHistory((prev) => [
        { ...result, ts: new Date().toISOString() },
        ...prev.slice(0, 9),
      ]);
    } catch (err: unknown) {
      const msg = axios.isAxiosError(err) && !err.response
        ? "Cannot reach backend — is docker compose running?"
        : "Query failed — please try again.";
      setQueryResult({
        status: "error", question, answer: msg,
        investigation: { domains_checked: [], target_resource: null, target_namespace: "all" },
        kubectl_commands: [], confidence: 0, severity: "low",
      });
    } finally {
      setQueryLoading(false);
    }
  };

  const handleSave = async (result: QueryResult, context: string) => {
    if (!user) throw new Error("Not authenticated — please refresh and sign in");
    // Already saved this session — silently succeed (button already shows "Saved")
    if (savedQuestions.has(result.question)) return;
    const { data } = await axios.post(
      `${API_BASE}/query/save`,
      {
        user_id: user.id,
        question: result.question,
        answer: result.answer,
        kubectl_commands: result.kubectl_commands ?? [],
        domains_checked: result.investigation?.domains_checked ?? [],
        confidence: result.confidence,
        severity: result.severity,
        context,
      },
      { timeout: 10_000 }
    );
    console.log("[handleSave] backend response:", data);
    if (data.status !== "success") throw new Error(data.message ?? "Save failed — unknown error");
    setSavedQuestions((prev) => new Set([...prev, result.question]));
    // Signal the History page to refresh if it's mounted
    window.dispatchEvent(new CustomEvent("savedQueriesUpdated"));
  };

  return (
    <div style={{ maxWidth: "800px", margin: "0 auto" }}>
      {/* Page header */}
      <div style={{ marginBottom: "24px" }}>
        <h1 style={{ fontSize: "20px", fontWeight: 700, color: "var(--text-primary)", margin: "0 0 4px" }}>Natural Language Query</h1>
        <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0 }}>
          Ask anything about your Kubernetes cluster in plain English.
        </p>
      </div>

      {/* Cluster selector */}
      {!clustersLoading && clusters.length > 1 && (
        <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "8px", padding: "12px 16px", marginBottom: "20px" }}>
          <label style={{ fontSize: "12px", color: "var(--text-muted)", display: "block", marginBottom: "8px" }}>Cluster context</label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
            {clusters.map((c) => (
              <button
                key={c.context}
                onClick={() => setSelectedContext(c.context)}
                style={{
                  padding: "5px 12px", borderRadius: "5px", fontSize: "12px",
                  fontWeight: selectedContext === c.context ? 600 : 400,
                  border: `1px solid ${selectedContext === c.context ? "var(--primary)" : "var(--border)"}`,
                  background: selectedContext === c.context ? "rgba(124,58,237,0.12)" : "var(--bg-surface-2)",
                  color: selectedContext === c.context ? "var(--text-accent)" : "var(--text-secondary)",
                  cursor: "pointer", transition: "all 0.12s",
                }}
              >
                {c.name}
                {c.current && <span style={{ marginLeft: "4px", fontSize: "10px", color: "var(--text-accent)" }}>●</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Query box */}
      <QueryBox
        onQuery={handleQuery}
        loading={queryLoading}
        disabled={clustersLoading && clusters.length === 0}
      />

      {/* Current result */}
      {queryResult && (
        <div style={{ marginTop: "20px" }} className="animate-slide-up">
          <QueryResultCard
            result={queryResult}
            context={selectedContext}
            onSave={handleSave}
            initialSaved={savedQuestions.has(queryResult.question)}
          />
        </div>
      )}

      {/* Session history */}
      {sessionHistory.length > 1 && (
        <div style={{ marginTop: "28px" }}>
          <p style={{ fontSize: "12px", color: "var(--text-muted)", margin: "0 0 10px", textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600 }}>
            Session History
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            {sessionHistory.slice(1).map((r, i) => {
              const isSaved = savedQuestions.has(r.question);
              return (
                <button
                  key={i}
                  onClick={() => setQueryResult(r)}
                  style={{
                    background: "var(--bg-surface)", border: "1px solid var(--border)",
                    borderRadius: "7px", padding: "10px 14px", textAlign: "left",
                    cursor: "pointer", display: "flex", alignItems: "center",
                    gap: "10px", transition: "border-color 0.12s", width: "100%",
                  }}
                  onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.borderColor = "var(--primary)")}
                  onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.borderColor = "var(--border)")}
                >
                  <div style={{ width: "6px", height: "6px", borderRadius: "50%", background: SEVERITY_COLORS[r.severity] || "var(--text-muted)", flexShrink: 0 }} />
                  <span style={{ flex: 1, fontSize: "13px", color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {r.question}
                  </span>
                  {isSaved && <BookmarkFilled />}
                  <span style={{ fontSize: "11px", color: "var(--text-muted)", flexShrink: 0 }}>
                    {formatDate(r.ts)}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
