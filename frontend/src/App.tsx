import { useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, qs } from "./api";
import { useApp, useAuth } from "./context";
import { UNIT_LABEL } from "./format";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Periods from "./pages/Periods";
import Runs from "./pages/Runs";
import Reports from "./pages/Reports";
import Compare from "./pages/Compare";
import Masters from "./pages/Masters";
import MasterSheet from "./pages/MasterSheet";
import Adjustments from "./pages/Adjustments";
import Audit from "./pages/Audit";
import Admin from "./pages/Admin";
import Profile from "./pages/Profile";
import Welcome from "./pages/Welcome";
import { Loading } from "./components/ui";
import { Icon, type IconName } from "./components/icons";

type NavItem = { to: string; label: string; icon: IconName; group: string; perm?: "admin" };

// Every product-costing feature, in workflow order.
const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: "dashboard", group: "Main" },
  { to: "/master", label: "Master", icon: "sheet", group: "Main" },
  { to: "/periods", label: "Trial balances", icon: "tb", group: "Main" },
  { to: "/adjustments", label: "Adjustments", icon: "adjust", group: "Main" },
  { to: "/runs", label: "Calculations", icon: "calc", group: "Costing" },
  { to: "/reports", label: "P&L reports", icon: "reports", group: "Analysis" },
  { to: "/compare", label: "Compare", icon: "compare", group: "Analysis" },
  { to: "/masters", label: "Masters", icon: "masters", group: "Set-up" },
];
const FOOT: NavItem[] = [
  { to: "/audit", label: "Audit trail", icon: "audit", group: "" },
  { to: "/admin", label: "Administration", icon: "admin", group: "", perm: "admin" },
];

const THEMES: { v: "auto" | "light" | "dark"; icon: IconName; label: string }[] = [
  { v: "auto", icon: "monitor", label: "System theme" },
  { v: "light", icon: "sun", label: "Light theme" },
  { v: "dark", icon: "moon", label: "Dark theme" },
];

function initials(s: string) {
  return s.split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
}

function useOutside(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) close(); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open, close]);
  return ref;
}

/** User card at the bottom of the sidebar, with profile, theme and sign-out. */
function UserCard({ collapsed }: { collapsed: boolean }) {
  const { me, logout } = useAuth();
  const app = useApp();
  const [open, setOpen] = useState(false);
  const ref = useOutside(open, () => setOpen(false));
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  if (!me) return null;
  const next = THEMES[(THEMES.findIndex((t) => t.v === app.theme) + 1) % THEMES.length];
  const cur = THEMES.find((t) => t.v === app.theme) ?? THEMES[0];
  return (
    <div className="user-card" ref={ref}>
      <button className="user-card-btn" onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="menu" title={collapsed ? me.name || me.email : undefined}>
        <span className="avatar">{initials(me.name || me.email)}</span>
        <span className="who-text">
          <strong>{me.name || me.email}</strong>
          <small>{me.role.replace(/_/g, " ")}</small>
        </span>
        <Icon name="chevron" size={15} />
      </button>
      {open ? (
        <div className="menu up" role="menu">
          <div className="menu-head">
            <strong>{me.name || me.email}</strong>
            <span className="muted small">{me.email}</span>
          </div>
          <Link to="/profile" role="menuitem"><Icon name="user" size={16} />Profile</Link>
          <button role="menuitem" onClick={() => app.setTheme(next.v)}><Icon name={cur.icon} size={16} />{cur.label}<span className="muted small right">switch</span></button>
          <button role="menuitem" onClick={logout}><Icon name="logout" size={16} />Sign out</button>
        </div>
      ) : null}
    </div>
  );
}

function Sidebar({ collapsed, onToggle, mobileOpen, onClose }: { collapsed: boolean; onToggle: () => void; mobileOpen: boolean; onClose: () => void }) {
  const { me, can } = useAuth();
  const app = useApp();
  const items = NAV.filter((n) => !n.perm || can(n.perm));
  const groups = [...new Set(items.map((n) => n.group))];
  const hasCompany = !!me?.companies.length;
  return (
    <>
      {mobileOpen ? <div className="side-scrim" onClick={onClose} /> : null}
      <aside className={`side ${collapsed ? "collapsed" : ""} ${mobileOpen ? "open" : ""}`}>
        <div className="side-top">
          <Link to="/" className="side-brand" aria-label="Home">
            <img className="logo-long" src="/brand/logo-long.png" alt="L&T Valves" />
            <img className="logo-short" src="/brand/logo-short.png" alt="L&T" />
          </Link>
          <button className="icon-btn side-toggle" onClick={onToggle} aria-label={collapsed ? "Expand menu" : "Collapse menu"} title={collapsed ? "Expand menu" : "Collapse menu"}>
            <Icon name="panel" size={18} />
          </button>
        </div>
        {hasCompany ? (
          <label className="company-select" title="Company">
            <select value={app.companyId ?? ""} onChange={(e) => app.setCompanyId(Number(e.target.value))} aria-label="Company">
              {me?.companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <span className="company-initial" aria-hidden>{initials(app.company?.name ?? "")}</span>
          </label>
        ) : null}
        <nav className="side-nav" aria-label="Main">
          {hasCompany ? groups.map((g) => (
            <div key={g} className="side-group">
              <div className="side-section">{g}</div>
              {items.filter((n) => n.group === g).map((n) => (
                <NavLink key={n.to} to={n.to} end={n.to === "/"} title={collapsed ? n.label : undefined} onClick={onClose}>
                  <Icon name={n.icon} size={19} /><span className="label">{n.label}</span>
                </NavLink>
              ))}
            </div>
          )) : null}
        </nav>
        <div className="side-foot">
          {FOOT.filter((n) => !n.perm || can(n.perm)).map((n) => (
            <NavLink key={n.to} to={n.to} title={collapsed ? n.label : undefined} onClick={onClose}>
              <Icon name={n.icon} size={19} /><span className="label">{n.label}</span>
            </NavLink>
          ))}
          <UserCard collapsed={collapsed} />
        </div>
      </aside>
    </>
  );
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

/** Header: greeting on the dashboard, page search, period and unit, control alerts. */
function Header({ onMenu }: { onMenu: () => void }) {
  const { me, can } = useAuth();
  const app = useApp();
  const loc = useLocation();
  const nav = useNavigate();
  const [term, setTerm] = useState("");
  const pages = useMemo(() => [...NAV, ...FOOT].filter((n) => !n.perm || can(n.perm)), [can]);
  const dash = useQuery({
    queryKey: ["dashboard", app.companyId, app.periodId, app.unit],
    queryFn: () => api.get<{ current: null | { label: string; run: { id: number }; controls: { errors?: number; warnings?: number } } }>(
      `/api/companies/${app.companyId}/dashboard${qs({ period_id: app.periodId, unit: app.unit })}`),
    enabled: app.companyId !== null,
  });
  const cur = dash.data?.current;
  const alerts = (cur?.controls.errors ?? 0) + (cur?.controls.warnings ?? 0);
  const onDashboard = loc.pathname === "/";
  const go = (e: React.FormEvent) => {
    e.preventDefault();
    const t = term.trim().toLowerCase();
    const hit = pages.find((p) => p.label.toLowerCase() === t) ?? pages.find((p) => p.label.toLowerCase().includes(t));
    if (hit) { nav(hit.to); setTerm(""); }
  };
  const first = me?.name || (me?.email ?? "").split("@")[0];
  return (
    <header className="page-header">
      <button className="icon-btn menu-btn" onClick={onMenu} aria-label="Open menu"><Icon name="menu" /></button>
      <div className="page-header-title">
        {onDashboard ? (
          <>
            <h1>{greeting()}, {first}</h1>
            <p>{cur ? `Here's how ${app.company?.name ?? "the company"} is performing in ${cur.label}.` : "Here's what's happening with product costing today."}</p>
          </>
        ) : null}
      </div>
      <div className="page-header-tools">
        <form className="search" onSubmit={go} role="search">
          <input list="page-list" placeholder="Search pages…" value={term} onChange={(e) => setTerm(e.target.value)} aria-label="Search pages" />
          <datalist id="page-list">{pages.map((p) => <option key={p.to} value={p.label} />)}</datalist>
          <button type="submit" aria-label="Search"><Icon name="search" size={18} /></button>
        </form>
        {me?.companies.length ? (
          <label className="pill-select" title="Period">
            <Icon name="calc" size={15} />
            <select value={app.periodId ?? ""} onChange={(e) => app.setPeriodId(Number(e.target.value))} aria-label="Period">
              {app.periods.map((p) => (
                <option key={p.id} value={p.id}>{p.label || p.code}{p.scenario !== "actual" ? ` (${p.scenario})` : ""}{p.status === "locked" ? " · locked" : ""}</option>
              ))}
            </select>
          </label>
        ) : null}
        <label className="pill-select" title="Display unit">
          <select value={app.unit} onChange={(e) => app.setUnit(e.target.value)} aria-label="Display unit">
            {Object.entries(UNIT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <Link to={cur ? `/runs/${cur.run.id}` : "/runs"} className="bell" aria-label={alerts ? `${alerts} control issues` : "No control issues"} title={alerts ? `${alerts} control issue(s) in the latest calculation` : "All controls passed"}>
          <Icon name="bell" size={19} />
          {alerts ? <span className="dot" /> : null}
        </Link>
      </div>
    </header>
  );
}

export default function App() {
  const { me, loading, can } = useAuth();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("pc_side_collapsed") === "1");
  const [mobileOpen, setMobileOpen] = useState(false);
  const toggle = () => setCollapsed((c) => {
    try { localStorage.setItem("pc_side_collapsed", c ? "0" : "1"); } catch { /* storage blocked */ }
    return !c;
  });
  if (loading) return <Loading what="Starting" />;
  if (!me) return <Login />;
  return (
    <div className={`shell ${collapsed ? "side-collapsed" : ""}`}>
      <Sidebar collapsed={collapsed} onToggle={toggle} mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />
      <div className="main">
        <Header onMenu={() => setMobileOpen(true)} />
        <main className="content">
          {!me.companies.length ? (
            <Routes>
              <Route path="/profile" element={<Profile />} />
              <Route path="*" element={<Welcome />} />
            </Routes>
          ) : (
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/master" element={<MasterSheet />} />
            <Route path="/periods" element={<Periods />} />
            <Route path="/adjustments" element={<Adjustments />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/:runId" element={<Runs />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/compare" element={<Compare />} />
            <Route path="/masters" element={<Masters />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/admin" element={can("admin") ? <Admin /> : <Navigate to="/" />} />
            <Route path="/profile" element={<Profile />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
          )}
        </main>
      </div>
    </div>
  );
}
