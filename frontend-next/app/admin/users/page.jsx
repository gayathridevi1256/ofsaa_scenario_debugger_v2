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
        <div className="page-header">
          <div>
            <p className="eyebrow-label">Admin</p>
            <h1 className="page-title">User Management</h1>
            <p className="page-subtitle">Manage application users</p>
          </div>
        </div>
        {error && <div className="alert alert-error" style={{ marginBottom: 20 }}>{error}</div>}
        <div className="card card-tight animate-fade-in">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  {["Username", "Full Name", "Email", "Role", "Status"].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.78rem" }}>{u.username}</td>
                    <td style={{ fontWeight: 500 }}>{u.full_name || "—"}</td>
                    <td style={{ color: "var(--text-secondary)" }}>{u.email || "—"}</td>
                    <td><span className={`badge ${u.role === "admin" ? "badge-warning" : "badge-muted"}`}>{u.role}</span></td>
                    <td><span className={`badge ${u.is_active ? "badge-success" : "badge-danger"}`}>{u.is_active ? "Active" : "Inactive"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </main>
    </div>
  );
}
