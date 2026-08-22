"use client";

import { useState, useEffect } from "react";
import { useAuth } from "@/hooks/use-auth";
import { Navbar } from "@/components/layout/navbar";
import { UploadZone } from "./upload-zone";
import { JobHistory } from "./job-history";
import { jobsAPI, getErrorMessage } from "@/lib/api-client";

export default function DashboardPage() {
  const { user, logout } = useAuth();
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    jobsAPI.listJobs().then((r) => setJobs(Array.isArray(r) ? r : r.jobs || [])).catch((err) => setError(getErrorMessage(err))).finally(() => setLoading(false));
  }, []);

  return (
    <div className="page">
      <Navbar user={user} onLogout={logout} />
      <main className="page-content">
        <div className="animate-fade-in">
          <h1 style={{ fontSize: "1.5rem", fontWeight: 700, marginBottom: 4 }}>Dashboard</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "0.875rem", marginBottom: 32 }}>Upload a log file and run the diagnostic pipeline</p>
        </div>
        <UploadZone onJobStarted={() => jobsAPI.listJobs().then((r) => setJobs(Array.isArray(r) ? r : r.jobs || []))} />
        <JobHistory jobs={jobs} loading={loading} error={error} />
        <div style={{ height: 64 }} />
      </main>
    </div>
  );
}
