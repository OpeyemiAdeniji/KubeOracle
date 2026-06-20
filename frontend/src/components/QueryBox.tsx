"use client";

import { useState, useRef, KeyboardEvent } from "react";

const DEFAULT_SUGGESTIONS = [
  "Is my cluster ready for a production deployment?",
  "Which pods are consuming the most memory?",
  "Why is my pod stuck in Pending?",
  "Are there any security issues in my cluster?",
  "Which deployments have missing health probes?",
  "Are any services missing endpoints?",
];

interface QueryBoxProps {
  onQuery: (question: string) => void;
  loading: boolean;
  disabled?: boolean;
  extraSuggestions?: string[];
}

export default function QueryBox({
  onQuery,
  loading,
  disabled = false,
  extraSuggestions = [],
}: QueryBoxProps) {
  const [question, setQuestion] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const suggestions = [...extraSuggestions, ...DEFAULT_SUGGESTIONS].slice(0, 6);
  const canSubmit = question.trim().length > 0 && !loading && !disabled;

  const submit = () => {
    if (!canSubmit) return;
    onQuery(question.trim());
  };

  const handleKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") submit();
  };

  const fillSuggestion = (text: string) => {
    setQuestion(text);
    inputRef.current?.focus();
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <p style={{ fontSize: "11px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.08em", textAlign: "center", margin: 0 }}>
        Ask anything about your cluster
      </p>

      {/* Input row */}
      <div style={{ display: "flex", gap: "8px", alignItems: "stretch" }}>
        <div style={{ flex: 1, position: "relative" }}>
          <input
            ref={inputRef}
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={handleKey}
            disabled={loading || disabled}
            placeholder="Why is my payment service crashing?"
            style={{
              width: "100%",
              background: "var(--bg-surface)",
              border: "1px solid var(--border)",
              borderRadius: "6px",
              padding: "10px 14px",
              fontSize: "14px",
              color: "var(--text-primary)",
              outline: "none",
              transition: "border-color 0.15s",
              opacity: loading || disabled ? 0.5 : 1,
              cursor: loading || disabled ? "not-allowed" : "text",
            }}
            onFocus={(e) => (e.target.style.borderColor = "var(--primary)")}
            onBlur={(e) => (e.target.style.borderColor = "var(--border)")}
          />
          {loading && (
            <div style={{ position: "absolute", right: "12px", top: "50%", transform: "translateY(-50%)", display: "flex", alignItems: "center", gap: "6px", color: "var(--text-muted)", fontSize: "12px" }}>
              <div className="animate-spin" style={{ width: "13px", height: "13px", border: "1.5px solid var(--border)", borderTopColor: "var(--primary)", borderRadius: "50%" }} />
              Analyzing…
            </div>
          )}
        </div>
        <button
          onClick={submit}
          disabled={!canSubmit}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "6px",
            padding: "10px 18px",
            borderRadius: "6px",
            fontWeight: 600,
            fontSize: "14px",
            color: "#fff",
            background: canSubmit ? "var(--primary)" : "var(--bg-surface-2)",
            border: `1px solid ${canSubmit ? "var(--primary)" : "var(--border)"}`,
            cursor: canSubmit ? "pointer" : "not-allowed",
            opacity: !canSubmit ? 0.5 : 1,
            transition: "all 0.15s",
            flexShrink: 0,
          }}
        >
          {loading ? (
            <>
              <div className="animate-spin" style={{ width: "13px", height: "13px", border: "2px solid rgba(255,255,255,0.3)", borderTopColor: "#fff", borderRadius: "50%" }} />
              Asking
            </>
          ) : (
            <>
              <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              Ask
            </>
          )}
        </button>
      </div>

      {/* Suggestion pills */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
        {suggestions.map((s, i) => (
          <button
            key={i}
            onClick={() => fillSuggestion(s)}
            disabled={loading || disabled}
            style={{
              fontSize: "12px",
              padding: "5px 10px",
              borderRadius: "5px",
              background: "var(--bg-surface)",
              border: "1px solid var(--border)",
              color: "var(--text-muted)",
              cursor: loading || disabled ? "not-allowed" : "pointer",
              opacity: loading || disabled ? 0.4 : 1,
              textAlign: "left",
              transition: "all 0.12s",
            }}
            onMouseEnter={(e) => { if (!loading && !disabled) { (e.currentTarget as HTMLElement).style.borderColor = "var(--primary)"; (e.currentTarget as HTMLElement).style.color = "var(--text-accent)"; } }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = "var(--border)"; (e.currentTarget as HTMLElement).style.color = "var(--text-muted)"; }}
          >
            {i < extraSuggestions.length && (
              <span style={{ color: "var(--text-accent)", marginRight: "3px" }}>↑</span>
            )}
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
