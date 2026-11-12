import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, setUnauthorizedHandler, token } from "./api";
import type { Company, Me, Period } from "./types";

// ---------------------------------------------------------------- auth
interface AuthCtx {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  can: (perm: "view" | "edit" | "approve" | "admin") => boolean;
  refresh: () => void;
}
const Auth = createContext<AuthCtx>(null as unknown as AuthCtx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const qc = useQueryClient();

  const refresh = useCallback(() => {
    if (!token.get()) {
      setMe(null);
      setLoading(false);
      return;
    }
    api
      .get<Me>("/api/auth/me")
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    // SSO callback lands on /#/sso?token=...
    const m = /[?&]token=([^&]+)/.exec(window.location.hash);
    if (m) {
      token.set(decodeURIComponent(m[1]));
      window.location.hash = "#/";
    }
    setUnauthorizedHandler(() => setMe(null));
    refresh();
  }, [refresh]);

  const login = async (email: string, password: string) => {
    const r = await api.post<{ access_token: string; user: Me }>("/api/auth/login", { email, password });
    token.set(r.access_token);
    setMe(r.user);
  };
  const logout = () => {
    token.clear();
    setMe(null);
    qc.clear();
  };
  const can = (perm: "view" | "edit" | "approve" | "admin") => !!me && (me.is_superadmin || me.permissions.includes(perm));
  return <Auth.Provider value={{ me, loading, login, logout, can, refresh }}>{children}</Auth.Provider>;
}
export const useAuth = () => useContext(Auth);

// ---------------------------------------------------------------- selected company / period / unit
interface AppCtx {
  companyId: number | null;
  setCompanyId: (id: number) => void;
  company: Company | undefined;
  periods: Period[];
  periodId: number | null;
  setPeriodId: (id: number | null) => void;
  period: Period | undefined;
  unit: string;
  setUnit: (u: string) => void;
  theme: "auto" | "light" | "dark";
  setTheme: (t: "auto" | "light" | "dark") => void;
}
const App = createContext<AppCtx>(null as unknown as AppCtx);

function useStored<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try {
      const s = localStorage.getItem(key);
      return s === null ? initial : (JSON.parse(s) as T);
    } catch {
      return initial;
    }
  });
  const set = (x: T) => {
    setV(x);
    try {
      localStorage.setItem(key, JSON.stringify(x));
    } catch {
      /* storage blocked */
    }
  };
  return [v, set];
}

export function AppProvider({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  const [companyId, setCompanyIdRaw] = useStored<number | null>("pc_company", null);
  const [periodId, setPeriodId] = useStored<number | null>("pc_period", null);
  const [unitPref, setUnit] = useStored<string>("pc_unit", "");
  const [theme, setThemeRaw] = useStored<"auto" | "light" | "dark">("pc_theme", "auto");

  useEffect(() => {
    if (!me) return;
    const ids = me.companies.map((c) => c.id);
    if (ids.length && (companyId === null || !ids.includes(companyId))) setCompanyIdRaw(ids[0]);
  }, [me]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const companyQ = useQuery({
    queryKey: ["company", companyId],
    queryFn: () => api.get<Company>(`/api/companies/${companyId}`),
    enabled: !!me && companyId !== null,
  });
  const periodsQ = useQuery({
    queryKey: ["periods", companyId],
    queryFn: () => api.get<Period[]>(`/api/companies/${companyId}/periods`),
    enabled: !!me && companyId !== null,
  });
  const periods = useMemo(() => periodsQ.data ?? [], [periodsQ.data]);
  useEffect(() => {
    if (!periods.length) return;
    if (periodId === null || !periods.some((p) => p.id === periodId)) {
      const actual = periods.filter((p) => p.scenario === "actual");
      setPeriodId((actual[0] ?? periods[0]).id);
    }
  }, [periods, periodId]); // eslint-disable-line react-hooks/exhaustive-deps

  const setCompanyId = (id: number) => {
    setCompanyIdRaw(id);
    setPeriodId(null);
  };
  const unit = unitPref || companyQ.data?.settings.display_unit || "crore";
  const value: AppCtx = {
    companyId,
    setCompanyId,
    company: companyQ.data,
    periods,
    periodId,
    setPeriodId,
    period: periods.find((p) => p.id === periodId),
    unit,
    setUnit,
    theme,
    setTheme: setThemeRaw,
  };
  return <App.Provider value={value}>{children}</App.Provider>;
}
export const useApp = () => useContext(App);

// ---------------------------------------------------------------- toasts
interface Toast {
  id: number;
  text: string;
  kind: "info" | "error";
}
const ToastCtx = createContext<(text: string, kind?: "info" | "error") => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: "info" | "error" = "info") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, kind }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), kind === "error" ? 8000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);
