"use client";

import { useState, useEffect, useRef, useCallback, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import axios from "axios";
import { insforge } from "@/lib/insforge";
import { useUser } from "../layout";
import {
  AppStatus, Cluster, Diagnosis, Investigation, INVESTIGATION_STEPS, Step, StepStatus,
} from "@/types";
import ClusterSelector from "@/components/ClusterSelector";
import InvestigationPanel from "@/components/InvestigationPanel";
import DiagnosisCard from "@/components/DiagnosisCard";

const STEP_INTERVAL_MS = 2400;
const API_BASE =
  typeof window !== "undefined"
    ? process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"
    : "http://localhost:8000";

function makeSteps(defaultStatus: StepStatus = "pending"): Step[] {
  return INVESTIGATION_STEPS.map((s) => ({ ...s, status: defaultStatus }));
}

function HealthyCard() {
  return (
    <div style={{ background: "var(--bg-surface)", border: "1px solid #14532d", borderRadius: "8px", padding: "24px", height: "100%", display: "flex", flexDirection: "column", justifyContent: "center" }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: "16px" }}>
        <div style={{ width: "44px", height: "44px", borderRadius: "8px", background: "#052e16", border: "1px solid #14532d", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
          <svg width="22" height="22" fill="none" viewBox="0 0 24 24" stroke="#4ade80">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </div>
        <div>
          <h3 style={{ fontSize: "16px", fontWeight: 600, color: "#4ade80", margin: "0 0 6px" }}>Cluster Healthy</h3>
          <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: "0 0 4px" }}>No critical Kubernetes issues detected.</p>
          <p style={{ fontSize: "13px", color: "var(--text-muted)", margin: 0 }}>
            All 13 inspectors returned clean results. Cluster appears to be running normally.
          </p>
        </div>
      </div>
    </div>
  );
}

function UnreachableCard({ context }: { context: string }) {
  return (
    <div style={{ background: "var(--bg-surface)", border: "1px solid #7f1d1d", borderRadius: "8px", padding: "24px", height: "100%", display: "flex", flexDirection: "column", justifyContent: "center" }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: "16px" }}>
        <div style={{ width: "44px", height: "44px", borderRadius: "8px", background: "#450a0a", border: "1px solid #7f1d1d", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
          <svg width="22" height="22" fill="none" viewBox="0 0 24 24" stroke="#f87171">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </div>
        <div>
          <h3 style={{ fontSize: "16px", fontWeight: 600, color: "#f87171", margin: "0 0 6px" }}>Cannot Reach Cluster</h3>
          <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: "0 0 10px" }}>
            kubectl cannot connect to{" "}
            {context ? (
              <code style={{ color: "#f87171", background: "#450a0a", padding: "1px 5px", borderRadius: "3px" }}>{context}</code>
            ) : "the cluster"}.
          </p>
          <p style={{ fontSize: "12px", color: "var(--text-muted)", margin: "0 0 4px" }}>Please verify:</p>
          <ul style={{ margin: 0, paddingLeft: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: "3px" }}>
            <li style={{ fontSize: "12px", color: "var(--text-muted)" }}>• kubeconfig is mounted in <code style={{ color: "var(--text-secondary)" }}>docker-compose.yml</code></li>
            <li style={{ fontSize: "12px", color: "var(--text-muted)" }}>• Cluster is running: <code style={{ color: "var(--text-secondary)" }}>kubectl cluster-info{context ? ` --context=${context}` : ""}</code></li>
            <li style={{ fontSize: "12px", color: "var(--text-muted)" }}>• kubectl permissions are configured correctly</li>
          </ul>
        </div>
      </div>
    </div>
  );
}

function InvestigateContent() {
  const user = useUser();
  const searchParams = useSearchParams();

  const [appStatus, setAppStatus] = useState<AppStatus>("idle");
  const [steps, setSteps] = useState<Step[]>(makeSteps());
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [isHealthy, setIsHealthy] = useState(false);
  const [clusterReachable, setClusterReachable] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [clustersLoading, setClustersLoading] = useState(true);
  const [clustersError, setClustersError] = useState<string | null>(null);
  const [selectedContext, setSelectedContext] = useState("");
  const [investigations, setInvestigations] = useState<Investigation[]>([]);

  const stepTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stepCounterRef = useRef(0);
  const activeSessionRef = useRef("");
  const realtimeReadyRef = useRef(false);
  const mountedRef = useRef(true);

  const loadClusters = useCallback(async () => {
    setClustersLoading(true);
    setClustersError(null);
    try {
      const { data } = await axios.get(`${API_BASE}/clusters`, { timeout: 10_000 });
      const list: Cluster[] = data.clusters || [];
      if (!mountedRef.current) return;
      setClusters(list);
      if (data.error && !list.length) setClustersError(data.error);

      const urlCtx = searchParams.get("context");
      if (urlCtx && list.some((c) => c.context === urlCtx)) {
        setSelectedContext(urlCtx);
      } else {
        const current = list.find((c) => c.current);
        if (current) setSelectedContext(current.context);
        else if (list.length > 0) setSelectedContext(list[0].context);
      }
    } catch (err) {
      if (!mountedRef.current) return;
      if (axios.isAxiosError(err) && !err.response) {
        setClustersError("Cannot reach backend — is docker compose running?");
      } else {
        setClustersError("Failed to load clusters");
      }
    } finally {
      if (mountedRef.current) setClustersLoading(false);
    }
  }, [searchParams]);

  const loadInvestigations = useCallback(async () => {
    if (!user) return;
    const { data } = await insforge.database
      .from("investigations")
      .select()
      .eq("user_id", user.id)
      .order("created_at", { ascending: false })
      .limit(20);
    if (mountedRef.current) setInvestigations((data as Investigation[]) ?? []);
  }, [user]);

  useEffect(() => {
    mountedRef.current = true;
    loadClusters();
    loadInvestigations();

    const setupRealtime = async () => {
      if (!user) return;
      try {
        await insforge.realtime.connect();
        realtimeReadyRef.current = true;
        await insforge.realtime.subscribe(`investigations:${user.id}`);
        insforge.realtime.on("investigation_saved", () => {
          if (mountedRef.current) loadInvestigations();
        });
        insforge.realtime.on("step_update", (payload: any) => {
          if (!mountedRef.current) return;
          if (payload?.session_id !== activeSessionRef.current) return;
          const step: string = payload.step;
          const status: StepStatus = payload.status;
          setSteps((prev) => prev.map((s) => (s.id === step ? { ...s, status } : s)));
        });
      } catch (err) {
        console.warn("InsForge realtime unavailable, using simulated progress:", err);
      }
    };

    setupRealtime();

    return () => {
      mountedRef.current = false;
      if (realtimeReadyRef.current) {
        try { insforge.realtime.disconnect(); } catch (_) {}
      }
    };
  }, [loadClusters, loadInvestigations, user]);

  const stopSimulation = useCallback(() => {
    if (stepTimerRef.current) { clearTimeout(stepTimerRef.current); stepTimerRef.current = null; }
  }, []);

  const startSimulation = useCallback(() => {
    stopSimulation();
    stepCounterRef.current = 0;
    const advance = () => {
      if (!mountedRef.current) return;
      const idx = stepCounterRef.current;
      if (idx >= INVESTIGATION_STEPS.length) return;
      setSteps((prev) =>
        prev.map((s, i) => {
          if (s.status === "complete" || s.status === "failed") return s;
          if (i < idx) return { ...s, status: "complete" };
          if (i === idx) return { ...s, status: "active" };
          return s;
        })
      );
      stepCounterRef.current = idx + 1;
      stepTimerRef.current = setTimeout(advance, STEP_INTERVAL_MS);
    };
    stepTimerRef.current = setTimeout(advance, 80);
  }, [stopSimulation]);

  const completeAllSteps = useCallback(() => {
    stopSimulation();
    setSteps(makeSteps("complete"));
  }, [stopSimulation]);

  const handleInvestigate = async () => {
    if (appStatus === "investigating") return;
    const sessionId = crypto.randomUUID();
    activeSessionRef.current = sessionId;

    setSteps(makeSteps("pending"));
    setDiagnosis(null);
    setIsHealthy(false);
    setClusterReachable(true);
    setError(null);
    setAppStatus("investigating");
    startSimulation();

    if (realtimeReadyRef.current) {
      try { insforge.realtime.subscribe(`investigation:${sessionId}`); } catch (_) {}
    }

    try {
      const { data } = await axios.post(
        `${API_BASE}/investigate`,
        { session_id: sessionId, context: selectedContext },
        { timeout: 180_000 }
      );

      completeAllSteps();
      setAppStatus("success");

      if (!data.cluster_reachable) {
        setClusterReachable(false);
        setIsHealthy(false);
      } else if (data.healthy) {
        setIsHealthy(true);
        setDiagnosis(data.diagnosis);
      } else {
        setDiagnosis(data.diagnosis);
        setIsHealthy(false);
      }

      if (user) {
        await insforge.database.from("investigations").insert([{
          user_id: user.id,
          session_id: sessionId,
          context: selectedContext,
          root_cause: data.diagnosis?.root_cause || "",
          severity: data.diagnosis?.severity || "low",
          confidence: data.diagnosis?.confidence || 0,
          status: data.cluster_reachable ? "success" : "unreachable",
          full_diagnosis: data.diagnosis,
        }]);
        loadInvestigations();
      }
    } catch (err: unknown) {
      stopSimulation();
      setAppStatus("error");
      if (axios.isAxiosError(err)) {
        if (!err.response) {
          setError("Cannot connect to the backend. Make sure docker compose is running on port 8000.");
        } else {
          const detail = err.response.data?.detail || err.response.data?.message || JSON.stringify(err.response.data) || "";
          setError(`Backend error ${err.response.status}${detail ? ": " + detail : ""}`);
        }
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("Investigation failed — unknown error.");
      }
    } finally {
      if (realtimeReadyRef.current) {
        try { insforge.realtime.unsubscribe(`investigation:${sessionId}`); } catch (_) {}
      }
    }
  };

  const isInvestigating = appStatus === "investigating";
  const showPanel = appStatus !== "idle";

  const rightCard = (() => {
    if (appStatus === "investigating") {
      return (
        <div style={{ height: "100%", background: "var(--bg-surface)", border: "1px dashed var(--border)", borderRadius: "8px", display: "flex", alignItems: "center", justifyContent: "center", minHeight: "200px" }}>
          <div style={{ textAlign: "center" }}>
            <div className="w-8 h-8 border-2 rounded-full animate-spin mx-auto mb-3"
              style={{ borderColor: "var(--border)", borderTopColor: "var(--primary)", width: "28px", height: "28px" }} />
            <p style={{ fontSize: "13px", color: "var(--text-muted)" }}>
              {selectedContext ? `Investigating ${selectedContext}…` : "Investigating cluster…"}
            </p>
          </div>
        </div>
      );
    }
    if (appStatus === "success" && !clusterReachable) return <UnreachableCard context={selectedContext} />;
    if (appStatus === "success" && isHealthy) return <HealthyCard />;
    if ((appStatus === "success" || appStatus === "error") && diagnosis) return <DiagnosisCard diagnosis={diagnosis} />;
    return null;
  })();

  return (
    <div style={{ maxWidth: "1100px", margin: "0 auto" }}>
      {/* Page header */}
      <div style={{ marginBottom: "24px" }}>
        <h1 style={{ fontSize: "20px", fontWeight: 700, color: "var(--text-primary)", margin: "0 0 4px" }}>Investigate</h1>
        <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0 }}>
          Run 13 automated inspectors across 11 domains, then use AI to identify the root cause.
        </p>
      </div>

      {/* Cluster selector */}
      <div style={{ maxWidth: "560px", marginBottom: "20px" }}>
        <ClusterSelector
          clusters={clusters}
          selected={selectedContext}
          onSelect={setSelectedContext}
          loading={clustersLoading}
          error={clustersError}
          investigations={investigations}
        />
      </div>

      {/* Investigate button */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: "12px", marginBottom: "24px" }}>
        <button
          onClick={handleInvestigate}
          disabled={isInvestigating || (!clustersLoading && !clusters.length)}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "8px",
            padding: "10px 24px",
            borderRadius: "6px",
            fontWeight: 600,
            fontSize: "14px",
            color: "#fff",
            background: isInvestigating ? "var(--primary-hover)" : "var(--primary)",
            border: "none",
            cursor: isInvestigating || (!clustersLoading && !clusters.length) ? "not-allowed" : "pointer",
            opacity: isInvestigating || (!clustersLoading && !clusters.length) ? 0.7 : 1,
            transition: "background 0.15s",
          }}
        >
          {isInvestigating ? (
            <>
              <div className="w-4 h-4 border-2 rounded-full animate-spin"
                style={{ borderColor: "rgba(255,255,255,0.4)", borderTopColor: "#fff", width: "16px", height: "16px" }} />
              Investigating{selectedContext ? ` ${selectedContext}` : " cluster"}…
            </>
          ) : (
            <>
              <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
              </svg>
              Investigate{selectedContext ? ` ${selectedContext}` : " Cluster"}
            </>
          )}
        </button>

        {error && (
          <div style={{ maxWidth: "560px", padding: "10px 14px", borderRadius: "6px", background: "#450a0a", border: "1px solid #7f1d1d", color: "#f87171", fontSize: "13px" }}>
            <strong style={{ color: "#fca5a5" }}>Error: </strong>{error}
          </div>
        )}
      </div>

      {/* Investigation panel + result */}
      {showPanel && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: "20px" }} className="animate-slide-up">
          <InvestigationPanel steps={steps} status={appStatus} />
          <div>{rightCard}</div>
        </div>
      )}
    </div>
  );
}

export default function InvestigatePage() {
  return (
    <Suspense fallback={
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "60px" }}>
        <div className="w-6 h-6 border-2 rounded-full animate-spin"
          style={{ borderColor: "var(--border)", borderTopColor: "var(--primary)" }} />
      </div>
    }>
      <InvestigateContent />
    </Suspense>
  );
}
