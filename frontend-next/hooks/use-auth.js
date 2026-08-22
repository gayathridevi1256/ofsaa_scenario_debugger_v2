"use client";

import { createContext, useContext, useState, useEffect, useCallback } from "react";
import { useRouter, usePathname } from "next/navigation";
import { authAPI } from "@/lib/api-client";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      setLoading(false);
      if (pathname !== "/login") router.replace("/login");
      return;
    }
    authAPI.getMe()
      .then((u) => { setUser(u); if (pathname === "/login") router.replace("/dashboard"); })
      .catch(() => { localStorage.removeItem("token"); if (pathname !== "/login") router.replace("/login"); })
      .finally(() => setLoading(false));
  }, [pathname]);

  const login = useCallback(async (username, password) => {
    const data = await authAPI.login(username, password);
    localStorage.setItem("token", data.access_token);
    setUser({ username: data.username, full_name: data.full_name, role: data.role });
    router.replace("/dashboard");
    return data;
  }, [router]);

  const logout = useCallback(() => {
    authAPI.logout();
    setUser(null);
    router.replace("/login");
  }, [router]);

  return (
    <AuthContext.Provider value={{ user, login, logout, loading }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <div style={{ display: "flex", justifyContent: "center", padding: 80 }}><div className="spinner" /></div>;
  if (!user) return null;
  return children;
}

export function PublicRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (user) return null;
  return children;
}
