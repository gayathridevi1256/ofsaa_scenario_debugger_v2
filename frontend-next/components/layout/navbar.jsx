"use client";

import Link from "next/link";

export function Navbar({ user, onLogout, hideUser }) {
  return (
    <nav style={{ background: "var(--bg-surface)", borderBottom: "1px solid var(--border)", padding: "12px 20px" }}>
      <div style={{ maxWidth: 1100, margin: "0 auto", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Link href="/dashboard" style={{ fontWeight: 700, fontSize: "1rem", color: "var(--text-primary)", textDecoration: "none" }}>
            Scenario Debugger
          </Link>
          <span style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>v1.0</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {!hideUser && user && (
            <>
              <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>{user.full_name || user.username}</span>
              <span className={`badge ${user.role === "admin" ? "badge-warning" : "badge-muted"}`}>{user.role}</span>
            </>
          )}
          {!hideUser && <button className="btn btn-secondary btn-sm" onClick={onLogout}>Logout</button>}
        </div>
      </div>
    </nav>
  );
}
