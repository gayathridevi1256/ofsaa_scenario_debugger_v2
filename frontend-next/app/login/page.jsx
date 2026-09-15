"use client";

import { useState } from "react";
import { useAuth } from "@/hooks/use-auth";

const FEATURES = [
  "Traces a zero-alert scenario to the exact SQL condition eliminating every row",
  "Recommends threshold changes, verified by re-running the real query",
  "Runs entirely against your own Oracle schema — nothing leaves the box",
];

function DialIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="9" stroke="#fff" strokeWidth="1.6" opacity="0.9" />
      <path d="M12 12 L12 6" stroke="#fff" strokeWidth="2" strokeLinecap="round" transform="rotate(40 12 12)" />
      <circle cx="12" cy="12" r="1.6" fill="#fff" />
    </svg>
  );
}

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
    } catch (err) {
      setError(err?.response?.data?.detail || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-shell">
      {/* ---------------- LEFT: gradient hero panel ---------------- */}
      <div
        className="hero-panel"
        style={{ display: "flex", flexDirection: "column", justifyContent: "center", padding: "56px clamp(28px, 6vw, 72px)" }}
      >
        <div style={{ position: "relative", maxWidth: 440 }}>
          <div className="hero-mark"><DialIcon /></div>
          <p className="hero-eyebrow">OFSAA scenario diagnostics &middot; v2.0</p>
          <h1 style={{ fontFamily: "var(--font-display)", fontSize: "clamp(1.8rem, 3.2vw, 2.4rem)", fontWeight: 700, lineHeight: 1.12, letterSpacing: "-0.02em" }}>
            Scenario<span style={{ color: "#c4b5fd" }}>IQ</span>
          </h1>
          <p style={{ marginTop: 16, fontSize: "1rem", color: "rgba(235, 233, 255, 0.82)", lineHeight: 1.6 }}>
            Why a scenario didn&rsquo;t fire, traced through the real SQL &mdash; and the smallest real change that fixes it.
          </p>
          <ul className="hero-feature-list">
            {FEATURES.map((f, i) => (
              <li key={i}>
                <span className="hero-check">&#10003;</span>
                <span>{f}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* ---------------- RIGHT: sign-in form ---------------- */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", background: "var(--bg-base)", padding: 24 }}>
        <div className="animate-fade-in" style={{ width: "100%", maxWidth: 380 }}>
          <h2 style={{ fontFamily: "var(--font-display)", fontSize: "1.4rem", fontWeight: 700, marginBottom: 6, color: "var(--text-primary)" }}>
            Welcome back
          </h2>
          <p style={{ color: "var(--text-muted)", fontSize: "0.875rem", marginBottom: 28 }}>Sign in to your account to continue</p>

          {error && <div className="alert alert-error" style={{ marginBottom: 18 }}>{error}</div>}

          <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            <div>
              <label className="field-label">Username</label>
              <input className="input" type="text" placeholder="your.username" value={username} onChange={(e) => setUsername(e.target.value)} required />
            </div>
            <div>
              <label className="field-label">Password</label>
              <input className="input" type="password" placeholder="••••••••" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </div>
            <button className="btn btn-primary" type="submit" disabled={loading} style={{ width: "100%", marginTop: 6, padding: "12px 20px" }}>
              {loading ? <><div className="spinner spinner-sm" style={{ borderColor: "rgba(255,255,255,0.35)", borderTopColor: "#fff" }} /> Signing in…</> : "Sign In"}
            </button>
          </form>

          <p style={{ marginTop: 28, fontSize: "0.78rem", color: "var(--text-muted)", textAlign: "center" }}>
            FastAPI &middot; Oracle 19c &middot; a local LLM that never sees your data leave the box
          </p>
        </div>
      </div>
    </div>
  );
}
