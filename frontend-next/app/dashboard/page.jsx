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
        <div className="animate-fade-in page-header">
          <div>
            <p className="eyebrow-label">Dashboard</p>
            <h1 className="page-title">Diagnose a scenario</h1>
            <p className="page-subtitle">Upload a log file and run the diagnostic pipeline against the real environment</p>
          </div>
        </div>
        <UploadZone onJobStarted={() => jobsAPI.listJobs().then((r) => setJobs(Array.isArray(r) ? r : r.jobs || []))} />
        <JobHistory jobs={jobs} loading={loading} error={error} />
        <div style={{ height: 64 }} />
      </main>
    </div>
  );
}
