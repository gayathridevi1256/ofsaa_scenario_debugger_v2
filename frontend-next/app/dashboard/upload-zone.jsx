"use client";

import { useState, useRef } from "react";
import { filesAPI, jobsAPI, getErrorMessage } from "@/lib/api-client";
import { useRouter } from "next/navigation";

const MAX_FILES = 20;

export function UploadZone({ onJobStarted }) {
  const router = useRouter();
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploaded, setUploaded] = useState(0);
  const [total, setTotal] = useState(0);
  const [currentFile, setCurrentFile] = useState("");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const fileRef = useRef(null);

  const handleFiles = async (fileList) => {
    const files = Array.from(fileList).slice(0, MAX_FILES).filter((f) => f.name.toLowerCase().endsWith(".log") || f.name.toLowerCase().endsWith(".txt"));
    if (!files.length) {
      setError("No valid .log or .txt files selected");
      return;
    }

    setUploading(true);
    setError("");
    setProgress(0);
    setUploaded(0);
    setTotal(files.length);

    const filePaths = [];
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      setCurrentFile(file.name);
      setUploaded(i);
      try {
        const result = await filesAPI.uploadLog(file, setProgress);
        filePaths.push(result.file_path);
      } catch (err) {
        setError(`Upload failed for ${file.name}: ${getErrorMessage(err)}`);
        setUploading(false);
        return;
      }
    }
    setUploaded(files.length);
    setCurrentFile("");

    try {
      const batch = await jobsAPI.batchRun(filePaths);
      router.push(`/dashboard/batch?ids=${batch.jobs.map((j) => j.job_id).join(",")}`);
      onJobStarted?.();
    } catch (err) {
      setError(`Batch start failed: ${getErrorMessage(err)}`);
    } finally {
      setUploading(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
  };

  const handleSingle = async (file) => {
    if (!file) return;
    setUploading(true);
    setError("");
    setProgress(0);
    try {
      const uploaded = await filesAPI.uploadLog(file, setProgress);
      const result = await jobsAPI.runPipeline(uploaded.file_path);
      if (result.cached) {
        window.location.href = `/jobs/${result.cached_job_id}`;
      } else {
        window.location.href = `/jobs/${result.job_id}`;
      }
      onJobStarted?.();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="card animate-fade-in" style={{ marginBottom: 28 }}>
      <h2 style={{ fontFamily: "var(--font-display)", fontSize: "1.05rem", fontWeight: 700, marginBottom: 14 }}>Upload Log Files</h2>
      {error && <div className="alert alert-error" style={{ marginBottom: 14 }}>{error}</div>}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`dropzone${dragging ? " dragging" : ""}`}
        onClick={() => fileRef.current?.click()}
      >
        <input
          ref={fileRef}
          type="file"
          accept=".log,.txt"
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            const files = Array.from(e.target.files || []);
            if (files.length === 1) handleSingle(files[0]);
            else if (files.length > 1) handleFiles(files);
          }}
        />
        {uploading ? (
          <div>
            <div className="spinner" style={{ margin: "0 auto 14px" }} />
            <div style={{ fontSize: "0.875rem", color: "var(--text-secondary)", fontWeight: 500 }}>
              {currentFile
                ? `Uploading ${uploaded + 1} of ${total}: ${currentFile}`
                : `Starting batch run with ${total} files…`}
            </div>
            <div style={{ width: "100%", maxWidth: 320, margin: "10px auto 0", height: 5, background: "var(--border)", borderRadius: 3, overflow: "hidden" }}>
              <div style={{ width: `${total > 0 ? ((uploaded * 100) / total) : progress}%`, height: "100%", background: "var(--gradient-accent)", borderRadius: 3, transition: "width 0.2s" }} />
            </div>
          </div>
        ) : (
          <>
            <div className="dropzone-icon">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
                <path d="M12 4v11m0-11 4 4m-4-4-4 4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                <path d="M5 16v2.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V16" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            </div>
            <div style={{ fontWeight: 700, color: "var(--text-primary)" }}>Drop .log files here or click to browse</div>
            <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginTop: 5 }}>Select up to {MAX_FILES} files · Max 50MB each</div>
          </>
        )}
      </div>
    </div>
  );
}
