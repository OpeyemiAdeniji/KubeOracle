"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { insforge } from "@/lib/insforge";

type AuthMode = "signin" | "signup" | "verify";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<AuthMode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [otp, setOtp] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const clearState = () => { setError(null); setMessage(null); };

  const handleSignIn = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    clearState();
    const { data, error } = await insforge.auth.signInWithPassword({ email, password });
    setLoading(false);
    if (error) { setError(error.message); }
    else if (data) { router.push("/dashboard"); }
  };

  const handleSignUp = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    clearState();
    const { data, error } = await insforge.auth.signUp({ email, password, name });
    setLoading(false);
    if (error) { setError(error.message); }
    else if (data?.requireEmailVerification) {
      setMode("verify");
      setMessage("Check your email for a 6-digit verification code.");
    } else if (data?.accessToken) {
      router.push("/dashboard");
    }
  };

  const handleVerify = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    clearState();
    const { data, error } = await insforge.auth.verifyEmail({ email, otp });
    setLoading(false);
    if (error) { setError(error.message); }
    else if (data) { router.push("/dashboard"); }
  };

  const handleResend = async () => {
    await insforge.auth.resendVerificationEmail({ email });
    setMessage("Verification code resent — check your email.");
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    background: "var(--bg-surface-2)",
    border: "1px solid var(--border)",
    borderRadius: "6px",
    padding: "10px 14px",
    color: "var(--text-primary)",
    fontSize: "14px",
    outline: "none",
    transition: "border-color 0.15s",
  };

  return (
    <div style={{ minHeight: "100vh", background: "var(--bg-primary)", display: "flex", alignItems: "center", justifyContent: "center", padding: "48px 16px" }}>
      <div style={{ width: "100%", maxWidth: "400px" }}>
        {/* Logo */}
        <div style={{ textAlign: "center", marginBottom: "32px" }}>
          <div style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: "52px", height: "52px", borderRadius: "12px", background: "var(--primary)", marginBottom: "16px" }}>
            <svg width="28" height="28" fill="none" viewBox="0 0 24 24" stroke="white">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18" />
            </svg>
          </div>
          <h1 style={{ fontSize: "22px", fontWeight: 700, color: "var(--text-primary)", margin: "0 0 4px" }}>KubeOracle</h1>
          <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0 }}>
            {mode === "verify" ? "Verify your email" : "Sign in to your account"}
          </p>
        </div>

        {/* Card */}
        <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: "10px", padding: "28px" }}>

          {/* Mode toggle */}
          {mode !== "verify" && (
            <div style={{ display: "flex", background: "var(--bg-surface-2)", borderRadius: "7px", padding: "3px", marginBottom: "20px" }}>
              {(["signin", "signup"] as const).map((m) => (
                <button key={m} onClick={() => { setMode(m); clearState(); }}
                  style={{
                    flex: 1, padding: "8px", borderRadius: "5px", fontSize: "13px", fontWeight: 500, border: "none", cursor: "pointer", transition: "all 0.15s",
                    background: mode === m ? "var(--bg-hover)" : "transparent",
                    color: mode === m ? "var(--text-primary)" : "var(--text-muted)",
                  }}>
                  {m === "signin" ? "Sign In" : "Sign Up"}
                </button>
              ))}
            </div>
          )}

          {/* Feedback banners */}
          {error && (
            <div style={{ marginBottom: "16px", padding: "10px 14px", borderRadius: "6px", background: "#450a0a", border: "1px solid #7f1d1d", color: "#f87171", fontSize: "13px" }}>
              {error}
            </div>
          )}
          {message && (
            <div style={{ marginBottom: "16px", padding: "10px 14px", borderRadius: "6px", background: "#2e1065", border: "1px solid #4c1d95", color: "#c4b5fd", fontSize: "13px" }}>
              {message}
            </div>
          )}

          {/* Sign In form */}
          {mode === "signin" && (
            <form onSubmit={handleSignIn} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              <div>
                <label style={{ display: "block", fontSize: "13px", fontWeight: 500, color: "var(--text-secondary)", marginBottom: "6px" }}>Email</label>
                <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
                  style={inputStyle} placeholder="you@example.com"
                  onFocus={(e) => (e.target.style.borderColor = "var(--primary)")}
                  onBlur={(e) => (e.target.style.borderColor = "var(--border)")} />
              </div>
              <div>
                <label style={{ display: "block", fontSize: "13px", fontWeight: 500, color: "var(--text-secondary)", marginBottom: "6px" }}>Password</label>
                <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)}
                  style={inputStyle} placeholder="••••••••"
                  onFocus={(e) => (e.target.style.borderColor = "var(--primary)")}
                  onBlur={(e) => (e.target.style.borderColor = "var(--border)")} />
              </div>
              <button type="submit" disabled={loading}
                style={{ marginTop: "4px", padding: "10px", borderRadius: "6px", background: "var(--primary)", color: "#fff", fontWeight: 600, fontSize: "14px", border: "none", cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.6 : 1, transition: "background 0.15s" }}>
                {loading ? "Signing in…" : "Sign In"}
              </button>
            </form>
          )}

          {/* Sign Up form */}
          {mode === "signup" && (
            <form onSubmit={handleSignUp} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              <div>
                <label style={{ display: "block", fontSize: "13px", fontWeight: 500, color: "var(--text-secondary)", marginBottom: "6px" }}>Name</label>
                <input type="text" value={name} onChange={(e) => setName(e.target.value)}
                  style={inputStyle} placeholder="Your name"
                  onFocus={(e) => (e.target.style.borderColor = "var(--primary)")}
                  onBlur={(e) => (e.target.style.borderColor = "var(--border)")} />
              </div>
              <div>
                <label style={{ display: "block", fontSize: "13px", fontWeight: 500, color: "var(--text-secondary)", marginBottom: "6px" }}>Email</label>
                <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
                  style={inputStyle} placeholder="you@example.com"
                  onFocus={(e) => (e.target.style.borderColor = "var(--primary)")}
                  onBlur={(e) => (e.target.style.borderColor = "var(--border)")} />
              </div>
              <div>
                <label style={{ display: "block", fontSize: "13px", fontWeight: 500, color: "var(--text-secondary)", marginBottom: "6px" }}>
                  Password <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>(min 6 chars)</span>
                </label>
                <input type="password" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)}
                  style={inputStyle} placeholder="••••••••"
                  onFocus={(e) => (e.target.style.borderColor = "var(--primary)")}
                  onBlur={(e) => (e.target.style.borderColor = "var(--border)")} />
              </div>
              <button type="submit" disabled={loading}
                style={{ marginTop: "4px", padding: "10px", borderRadius: "6px", background: "var(--primary)", color: "#fff", fontWeight: 600, fontSize: "14px", border: "none", cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.6 : 1, transition: "background 0.15s" }}>
                {loading ? "Creating account…" : "Create Account"}
              </button>
            </form>
          )}

          {/* Email verification form */}
          {mode === "verify" && (
            <form onSubmit={handleVerify} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: "0 0 4px" }}>
                Enter the 6-digit code sent to <strong style={{ color: "var(--text-primary)" }}>{email}</strong>
              </p>
              <div>
                <label style={{ display: "block", fontSize: "13px", fontWeight: 500, color: "var(--text-secondary)", marginBottom: "6px" }}>Verification Code</label>
                <input type="text" required maxLength={6} value={otp}
                  onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  style={{ ...inputStyle, textAlign: "center", fontSize: "22px", letterSpacing: "0.5em", fontFamily: "var(--font-mono, monospace)" }}
                  placeholder="000000"
                  onFocus={(e) => (e.target.style.borderColor = "var(--primary)")}
                  onBlur={(e) => (e.target.style.borderColor = "var(--border)")} />
              </div>
              <button type="submit" disabled={loading}
                style={{ padding: "10px", borderRadius: "6px", background: "var(--primary)", color: "#fff", fontWeight: 600, fontSize: "14px", border: "none", cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.6 : 1, transition: "background 0.15s" }}>
                {loading ? "Verifying…" : "Verify Email"}
              </button>
              <button type="button" onClick={handleResend}
                style={{ padding: "8px", background: "none", border: "none", color: "var(--text-secondary)", fontSize: "13px", cursor: "pointer" }}>
                Resend code
              </button>
              <button type="button" onClick={() => { setMode("signin"); clearState(); }}
                style={{ padding: "6px", background: "none", border: "none", color: "var(--text-muted)", fontSize: "12px", cursor: "pointer" }}>
                Back to sign in
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
