import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { API_BASE } from "@/lib/disasterDefaults";

export type UserRole = "citizen" | "ngo" | "government";
export type Theme = "system" | "light" | "dark";

export interface User {
  id: string;
  name: string;
  email: string;
  role: UserRole;
  reliability?: number;
  verifiedReports?: number;
  falseReports?: number;
}

export interface Preferences {
  theme: Theme;
  language: string;
  default_disaster_type: string;
  notify_email: boolean;
  notify_push: boolean;
  alert_radius_km: number;
  home_latitude: number | null;
  home_longitude: number | null;
}

interface AuthContextType {
  user: User | null;
  preferences: Preferences | null;
  loading: boolean;
  login: (email: string, password: string, role: UserRole) => Promise<void>;
  signup: (name: string, email: string, password: string, role: UserRole) => Promise<void>;
  logout: () => Promise<void>;
  updatePreferences: (patch: Partial<Preferences>) => Promise<void>;
  authFetch: (path: string, init?: RequestInit) => Promise<Response>;
  isAuthenticated: boolean;
}

const REFRESH_KEY = "resqnet.refresh";

const readRefresh = () => {
  try {
    return localStorage.getItem(REFRESH_KEY);
  } catch {
    return null;
  }
};
const writeRefresh = (token: string | null) => {
  try {
    if (token) localStorage.setItem(REFRESH_KEY, token);
    else localStorage.removeItem(REFRESH_KEY);
  } catch {
    /* storage unavailable: session lasts until reload */
  }
};

type RefreshResult = "ok" | "rejected" | "error" | "none";

const AuthContext = createContext<AuthContextType | null>(null);

function errorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    if (typeof b.detail === "string") return b.detail;
    const first = Object.entries(b)[0];
    if (first) {
      const [field, val] = first;
      const msg = Array.isArray(val) ? String(val[0]) : String(val);
      return field === "non_field_errors" ? msg : `${field}: ${msg}`;
    }
  }
  return fallback;
}

function applyTheme(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [loading, setLoading] = useState(true);
  const accessRef = useRef<string | null>(null);
  const refreshInFlight = useRef<Promise<RefreshResult> | null>(null);
  const epoch = useRef(0);

  const clearSession = useCallback(() => {
    epoch.current += 1;
    accessRef.current = null;
    writeRefresh(null);
    setUser(null);
    setPreferences(null);
  }, []);

  const refreshAccess = useCallback((): Promise<RefreshResult> => {
    if (refreshInFlight.current) return refreshInFlight.current;
    const refresh = readRefresh();
    if (!refresh) return Promise.resolve("none");
    const startedAt = epoch.current;
    refreshInFlight.current = fetch(`${API_BASE}/api/auth/refresh/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh }),
    })
      .then(async (res): Promise<RefreshResult> => {
        if (res.status === 400 || res.status === 401) return "rejected";
        if (!res.ok) return "error";
        const body = await res.json();
        if (epoch.current !== startedAt) return "rejected";
        accessRef.current = body.access;
        if (body.refresh) writeRefresh(body.refresh);
        return "ok";
      })
      .catch((): RefreshResult => "error")
      .finally(() => {
        refreshInFlight.current = null;
      });
    return refreshInFlight.current;
  }, []);

  const authFetch = useCallback(
    async (path: string, init: RequestInit = {}) => {
      const send = () => {
        const headers = new Headers(init.headers);
        if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
          headers.set("Content-Type", "application/json");
        }
        if (accessRef.current) headers.set("Authorization", `Bearer ${accessRef.current}`);
        return fetch(`${API_BASE}${path}`, { ...init, headers });
      };
      const sentEpoch = epoch.current;
      const sentAccess = accessRef.current;
      const sentRefresh = readRefresh();
      let res = await send();
      if (res.status !== 401 || epoch.current !== sentEpoch) return res;

      let outcome: RefreshResult = "ok";
      if (accessRef.current === sentAccess) {
        outcome = await refreshAccess();
        const current = readRefresh();
        if (outcome === "rejected" && current && current !== sentRefresh) outcome = await refreshAccess();
      }
      if (outcome === "error" || epoch.current !== sentEpoch) return res;
      if (outcome === "ok") res = await send();
      if (res.status === 401) clearSession();
      return res;
    },
    [refreshAccess, clearSession],
  );

  const loadProfile = useCallback(async () => {
    const startedAt = epoch.current;
    const [meRes, prefRes] = await Promise.all([authFetch("/api/auth/me/"), authFetch("/api/auth/me/preferences/")]);
    if (!meRes.ok) throw new Error("Could not load profile");
    const me = await meRes.json();
    if (epoch.current !== startedAt) return;
    setUser({
      id: String(me.id),
      name: me.first_name || me.username,
      email: me.email,
      role: me.role,
      reliability: Math.round(me.reliability_score ?? 0),
      verifiedReports: 0,
      falseReports: 0,
    });
    if (prefRes.ok) {
      const prefs: Preferences = await prefRes.json();
      if (epoch.current !== startedAt) return;
      setPreferences(prefs);
      applyTheme(prefs.theme);
    }
  }, [authFetch]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (readRefresh() && (await refreshAccess()) === "ok") await loadProfile();
      } catch {
        /* transient failure: keep the stored token so a reload can retry */
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshAccess, loadProfile]);

  const login = useCallback(
    async (email: string, password: string, role: UserRole) => {
      const res = await fetch(`${API_BASE}/api/auth/login/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, role }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          res.status === 429 ? "Too many attempts. Please wait a minute." : errorMessage(body, "Sign in failed"),
        );
      }
      epoch.current += 1;
      accessRef.current = body.access;
      writeRefresh(body.refresh);
      try {
        await loadProfile();
      } catch (err) {
        clearSession();
        throw err;
      }
    },
    [loadProfile, clearSession],
  );

  const signup = useCallback(
    async (name: string, email: string, password: string, role: UserRole) => {
      const res = await fetch(`${API_BASE}/api/auth/register/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ first_name: name, email, password, password_confirm: password, role }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          res.status === 429 ? "Too many attempts. Please wait a minute." : errorMessage(body, "Sign up failed"),
        );
      }
      await login(email, password, role);
    },
    [login],
  );

  const logout = useCallback(async () => {
    const refresh = readRefresh();
    clearSession();
    if (refresh) {
      await fetch(`${API_BASE}/api/auth/logout/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh }),
      }).catch(() => undefined);
    }
  }, [clearSession]);

  const updatePreferences = useCallback(
    async (patch: Partial<Preferences>) => {
      const startedAt = epoch.current;
      const res = await authFetch("/api/auth/me/preferences/", { method: "PATCH", body: JSON.stringify(patch) });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorMessage(body, "Could not save preferences"));
      if (epoch.current !== startedAt) return;
      setPreferences(body);
      applyTheme(body.theme);
    },
    [authFetch],
  );

  return (
    <AuthContext.Provider
      value={{
        user,
        preferences,
        loading,
        login,
        signup,
        logout,
        updatePreferences,
        authFetch,
        isAuthenticated: !!user,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be within AuthProvider");
  return ctx;
}
