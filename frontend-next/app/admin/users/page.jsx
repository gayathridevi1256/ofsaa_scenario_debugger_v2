"use client";

import { useState, useEffect } from "react";
import { useAuth } from "@/hooks/use-auth";
import { Navbar } from "@/components/layout/navbar";
import { adminAPI, getErrorMessage } from "@/lib/api-client";

export default function AdminUsersPage() {
  const { user, logout } = useAuth();
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    adminAPI.listUsers().then(setUsers).catch((e) => setError(getErrorMessage(e))).finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ display: "flex", justifyContent: "center", padding: 80 }}><div className="spinner" /></div>;

  return (
    <div className="page">
      <Navbar user={user} onLogout={logout} />
      <main className="page-content">
        <h1 style={{ fontSize: "1.5rem", fontWeight: 700, marginBottom: 4 }}>User Management</h1>
        <p style={{ color: "var(--text-muted)", fontSize: "0.875rem", marginBottom: 32 }}>Manage application users</p>
        {error && <div className="alert alert-error">{error}</div>}
        <div className="card">
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                {["Username", "Full Name", "Email", "Role", "Status"].map((h) => (
                  <th key={h} style={{ textAlign: "left", padding: "8px 12px", color: "var(--text-muted)", fontWeight: 600, textTransform: "uppercase", fontSize: "0.7rem" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "10px 12px", fontFamily: "var(--font-mono)" }}>{u.username}</td>
                  <td style={{ padding: "10px 12px" }}>{u.full_name || "—"}</td>
                  <td style={{ padding: "10px 12px" }}>{u.email || "—"}</td>
                  <td style={{ padding: "10px 12px" }}><span className={`badge ${u.role === "admin" ? "badge-warning" : "badge-muted"}`}>{u.role}</span></td>
                  <td style={{ padding: "10px 12px" }}><span className={`badge ${u.is_active ? "badge-success" : "badge-danger"}`}>{u.is_active ? "Active" : "Inactive"}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </main>
    </div>
  );
}
