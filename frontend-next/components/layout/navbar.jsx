"use client";

import Link from "next/link";

function MarkIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="9" stroke="#fff" strokeWidth="1.6" opacity="0.9" />
      <path d="M12 12 L12 6" stroke="#fff" strokeWidth="2" strokeLinecap="round" transform="rotate(40 12 12)" />
      <circle cx="12" cy="12" r="1.6" fill="#fff" />
    </svg>
  );
}

export function Navbar({ user, onLogout, hideUser }) {
  return (
    <nav className="navbar">
      <div className="navbar-inner">
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <Link href="/dashboard" className="navbar-brand">
            <span className="navbar-mark"><MarkIcon /></span>
            <span className="navbar-title">Scenario<span style={{ color: "var(--accent)" }}>IQ</span></span>
          </Link>
          <span className="navbar-version">v2.0</span>
          {!hideUser && (
            <Link href="/threshold-tuning" className="navbar-link">
              Threshold Tuning
            </Link>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          {!hideUser && user && (
            <>
              <span className="navbar-user">{user.full_name || user.username}</span>
              <span className={`badge ${user.role === "admin" ? "badge-warning" : "badge-muted"}`}>{user.role}</span>
            </>
          )}
          {!hideUser && <button className="btn btn-secondary btn-sm" onClick={onLogout}>Logout</button>}
        </div>
      </div>
    </nav>
  );
}
