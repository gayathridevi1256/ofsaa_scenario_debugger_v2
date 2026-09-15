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
        <div className="page-header">
          <div>
            <p className="eyebrow-label">Batch run</p>
            <h1 className="page-title" style={{ fontSize: "1.35rem" }}>
              {jobs.length > 0 ? `${done} of ${jobs.length} complete` : loading ? "Loading…" : "No jobs found"}
            </h1>
          </div>
          <div className="page-header-actions">
          <button className="btn btn-secondary btn-sm" onClick={() => router.push("/dashboard")}>
            ← Back to Dashboard
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
          <div className="card card-tight animate-fade-in">
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th style={{ width: 36 }}>#</th>
                    <th>Scenario</th>
                    <th>Status</th>
                    <th>Batch Date</th>
                    <th style={{ textAlign: "center" }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((job, idx) => {
                    const cfg = STATUS_MAP[job.status] || STATUS_MAP.pending;
                    return (
                      <tr key={job.job_id}>
                        <td style={{ color: "var(--text-muted)" }}>{idx + 1}</td>
                        <td style={{ fontWeight: 600 }}>{job.scenario}</td>
                        <td>
                          <span style={{
                            display: "inline-flex", alignItems: "center", gap: 6,
                            background: cfg.bg, color: cfg.color, padding: "3px 11px",
                            borderRadius: 999, fontSize: "0.72rem", fontWeight: 700,
                          }}>
                            {job.status === "running" && <div className="spinner spinner-sm" />}
                            {cfg.label}
                          </span>
                        </td>
                        <td style={{ color: "var(--text-secondary)", fontSize: "0.8rem", fontFamily: "var(--font-mono)" }}>
                          {job.batch_date || "—"}
                        </td>
                        <td style={{ textAlign: "center" }}>
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