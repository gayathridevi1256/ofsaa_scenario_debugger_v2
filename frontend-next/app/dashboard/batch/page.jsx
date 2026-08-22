"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { Navbar } from "@/components/layout/navbar";
import { jobsAPI } from "@/lib/api-client";

const STATUS_MAP = {
  completed: { color: "var(--success)", bg: "var(--success-dim)", label: "Completed" },
  failed: { color: "var(--danger)", bg: "var(--danger-dim)", label: "Failed" },
  running: { color: "var(--accent)", bg: "var(--accent-subtle)", label: "Running" },
  pending: { color: "var(--text-muted)", bg: "var(--bg-overlay)", label: "Queued" },
};

function BatchPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { user, logout } = useAuth();
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);

  const ids = (searchParams.get("ids") || "").split(",").filter(Boolean);

  const handleDownloadAll = () => {
    if (exporting) return;
    setExporting(true);
    jobsAPI.downloadBatchReport(ids).finally(() => setExporting(false));
  };

  useEffect(() => {
    if (!ids.length) return;
    let active = true;

    const fetchAll = () => {
      Promise.all(ids.map((id) => jobsAPI.getJob(id).catch(() => null))).then((results) => {
        if (!active) return;
        const list = results.filter(Boolean).map((j) => ({
          job_id: j.job_id,
          scenario: j.scenario_name || j.log_filename || "Unknown",
          status: j.status,
          batch_date: j.batch_date,
        }));
        setJobs(list);
        setLoading(false);

        const allDone = list.every((j) => j.status === "completed" || j.status === "failed");
        if (!allDone) {
          setTimeout(fetchAll, 3000);
        }
      });
    };

    fetchAll();
    return () => { active = false; };
  }, [ids.join(",")]);

  const done = jobs.filter((j) => j.status === "completed" || j.status === "failed").length;
  const running = jobs.find((j) => j.status === "running");

  return (
    <div className="page">
      <Navbar user={user} onLogout={logout} />
      <main className="page-content">
        <div style={{ marginBottom: 24, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <h1 style={{ fontSize: "1.25rem", fontWeight: 700, marginBottom: 4 }}>Batch Run</h1>
            <p style={{ color: "var(--text-muted)", fontSize: "0.825rem" }}>
              {jobs.length > 0 ? `${done} of ${jobs.length} complete` : loading ? "Loading…" : "No jobs found"}
            </p>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-secondary btn-sm" onClick={() => router.push("/dashboard")}>
            Back to Dashboard
          </button>
          {done === jobs.length && jobs.length > 0 && (
            <button className="btn btn-primary btn-sm" onClick={handleDownloadAll} disabled={exporting}>
              {exporting ? "Generating PDF…" : "Download All PDF"}
            </button>
          )}
          </div>
        </div>

        {loading && <div style={{ display: "flex", justifyContent: "center", padding: 40 }}><div className="spinner" /></div>}

        {!loading && jobs.length > 0 && (
          <div className="card" style={{ overflow: "hidden" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.825rem" }}>
              <thead>
                <tr style={{ background: "var(--bg-overlay)", borderBottom: "1px solid var(--border)" }}>
                  <th style={{ padding: "10px 16px", textAlign: "left", fontWeight: 600, color: "var(--text-muted)" }}>#</th>
                  <th style={{ padding: "10px 16px", textAlign: "left", fontWeight: 600, color: "var(--text-muted)" }}>Scenario</th>
                  <th style={{ padding: "10px 16px", textAlign: "left", fontWeight: 600, color: "var(--text-muted)" }}>Status</th>
                  <th style={{ padding: "10px 16px", textAlign: "left", fontWeight: 600, color: "var(--text-muted)" }}>Batch Date</th>
                  <th style={{ padding: "10px 16px", textAlign: "center", fontWeight: 600, color: "var(--text-muted)" }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job, idx) => {
                  const cfg = STATUS_MAP[job.status] || STATUS_MAP.pending;
                  return (
                    <tr key={job.job_id} style={{ borderBottom: "1px solid var(--border)", background: idx % 2 === 0 ? "transparent" : "var(--bg-overlay)" }}>
                      <td style={{ padding: "10px 16px", color: "var(--text-muted)" }}>{idx + 1}</td>
                      <td style={{ padding: "10px 16px", fontWeight: 500 }}>{job.scenario}</td>
                      <td style={{ padding: "10px 16px" }}>
                        <span style={{
                          display: "inline-flex", alignItems: "center", gap: 6,
                          background: cfg.bg, color: cfg.color, padding: "2px 10px",
                          borderRadius: 999, fontSize: "0.75rem", fontWeight: 600,
                        }}>
                          {job.status === "running" && <div className="spinner spinner-sm" />}
                          {cfg.label}
                        </span>
                      </td>
                      <td style={{ padding: "10px 16px", color: "var(--text-muted)", fontSize: "0.8rem" }}>
                        {job.batch_date || "—"}
                      </td>
                      <td style={{ padding: "10px 16px", textAlign: "center" }}>
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => router.push(`/jobs/${job.job_id}?batch=${ids.join(",")}`)}
                          disabled={job.status === "pending"}
                        >
                          View
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}

export default function BatchPage() {
  return (
    <Suspense fallback={<div style={{ display: "flex", justifyContent: "center", padding: 40 }}><div className="spinner" /></div>}>
      <BatchPageInner />
    </Suspense>
  );
}