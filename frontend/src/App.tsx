import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
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

type NavItem = { to: string; label: string; short: string; icon: IconName; group: string; perm?: "admin" };

// Every product-costing feature, in workflow order: inputs → calculation → analysis → set-up.
const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", short: "Dashboard", icon: "dashboard", group: "Overview" },
  { to: "/master", label: "Master dashboard", short: "Master", icon: "sheet", group: "Inputs" },
  { to: "/periods", label: "Periods & uploads", short: "Trial balances", icon: "upload", group: "Inputs" },
  { to: "/adjustments", label: "Adjustments & inputs", short: "Adjustments", icon: "adjust", group: "Inputs" },
  { to: "/runs", label: "Calculations", short: "Calculations", icon: "calc", group: "Costing" },
  { to: "/reports", label: "Reports", short: "P&L reports", icon: "reports", group: "Analysis" },
  { to: "/compare", label: "Compare", short: "Compare", icon: "compare", group: "Analysis" },
  { to: "/masters", label: "Masters", short: "Masters", icon: "masters", group: "Set-up" },
  { to: "/audit", label: "Audit trail", short: "Audit trail", icon: "audit", group: "Set-up" },
  { to: "/admin", label: "Administration", short: "Admin", icon: "admin", group: "Set-up", perm: "admin" },
];

const THEMES: { v: "auto" | "light" | "dark"; icon: IconName; label: string }[] = [
  { v: "auto", icon: "monitor", label: "System theme" },
  { v: "light", icon: "sun", label: "Light theme" },
  { v: "dark", icon: "moon", label: "Dark theme" },
];

function initials(s: string) {
  return s.split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
}

function UserMenu() {
  const { me, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  if (!me) return null;
  return (
    <div className="user-menu" ref={ref}>
      <button className="user-chip" onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="menu">
        <span className="avatar">{initials(me.name || me.email)}</span>
        <span className="who-text">
          <strong>{me.name || me.email}</strong>
          <small>{me.role.replace(/_/g, " ")}</small>
        </span>
        <Icon name="chevron" size={14} />
      </button>
      {open ? (
        <div className="menu" role="menu">
          <div className="menu-head">
            <strong>{me.name || me.email}</strong>
            <span className="muted small">{me.email} · {me.tenant.name}</span>
          </div>
          <Link to="/profile" role="menuitem"><Icon name="user" size={16} />Profile</Link>
          <button role="menuitem" onClick={logout}><Icon name="logout" size={16} />Sign out</button>
        </div>
      ) : null}
    </div>
  );
}

function AppBar({ onToggleSidebar }: { onToggleSidebar: () => void }) {
  const { me, can } = useAuth();
  const app = useApp();
  const next = THEMES[(THEMES.findIndex((t) => t.v === app.theme) + 1) % THEMES.length];
  const cur = THEMES.find((t) => t.v === app.theme) ?? THEMES[0];
  const hasCompany = !!me?.companies.length;
  return (
    <header className="appbar">
      <div className="appbar-top">
        <button className="icon-btn" onClick={onToggleSidebar} aria-label="Toggle navigation"><Icon name="menu" /></button>
        <Link to="/" className="brand-long" aria-label="Home">
          <img src="/brand/logo-long.png" alt="L&T Valves" />
        </Link>
        <span className="brand-divider" aria-hidden />
        <div className="app-title">
          <strong>Product Costing &amp; Profitability</strong>
          <small>{app.company?.name ?? me?.tenant.name}</small>
        </div>
        <span className="grow" />
        <div className="filters">
          {hasCompany ? (<>
            <label className="filter">
              <span>Company</span>
              <select value={app.companyId ?? ""} onChange={(e) => app.setCompanyId(Number(e.target.value))}>
                {me?.companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>
            <label className="filter">
              <span>Period</span>
              <select value={app.periodId ?? ""} onChange={(e) => app.setPeriodId(Number(e.target.value))}>
                {app.periods.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label || p.code} {p.scenario !== "actual" ? `(${p.scenario})` : ""} {p.status === "locked" ? "🔒" : ""}
                  </option>
                ))}
              </select>
            </label>
          </>) : null}
          <label className="filter">
            <span>Unit</span>
            <select value={app.unit} onChange={(e) => app.setUnit(e.target.value)}>
              {Object.entries(UNIT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
        </div>
        <button className="icon-btn" onClick={() => app.setTheme(next.v)} aria-label={`${cur.label} – switch to ${next.label.toLowerCase()}`} title={cur.label}>
          <Icon name={cur.icon} />
        </button>
        <UserMenu />
      </div>
      {hasCompany ? (
        <nav className="modulebar" aria-label="Features">
          {NAV.filter((n) => !n.perm || can(n.perm)).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"} className="module">
              <Icon name={n.icon} size={16} />{n.short}
            </NavLink>
          ))}
          <span className="grow" />
          {can("edit") ? (<>
            <Link className="quick" to="/periods" title="Upload trial balances"><Icon name="upload" size={15} /><span>Upload TB</span></Link>
            <Link className="quick" to="/runs" title="Run calculation"><Icon name="play" size={14} /><span>Run calculation</span></Link>
          </>) : null}
          <Link className="quick primary" to="/reports" title="Reports and exports"><Icon name="download" size={15} /><span>Reports</span></Link>
        </nav>
      ) : null}
    </header>
  );
}

function Sidebar({ collapsed }: { collapsed: boolean }) {
  const { me, can } = useAuth();
  const items = NAV.filter((n) => !n.perm || can(n.perm));
  const groups = [...new Set(items.map((n) => n.group))];
  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
      <Link to="/" className="brand">
        <img src="/brand/logo-short.png" alt="L&T" />
        <div className="brand-text">Product Costing<small>& Profitability</small></div>
      </Link>
      <nav className="nav">
        {me?.companies.length ? groups.map((g) => (
          <div key={g}>
            <div className="section">{g}</div>
            {items.filter((n) => n.group === g).map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === "/"} title={collapsed ? n.label : undefined}>
                <Icon name={n.icon} />
                <span className="label">{n.label}</span>
              </NavLink>
            ))}
          </div>
        )) : null}
      </nav>
      <div className="spacer" />
      <div className="sidebar-foot">
        <div className="tenant">{me?.tenant.name}</div>
        <div className="muted small">Product costing v1</div>
      </div>
    </aside>
  );
}

export default function App() {
  const { me, loading, can } = useAuth();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("pc_nav_collapsed") === "1");
  const toggle = () => {
    setCollapsed((c) => {
      try { localStorage.setItem("pc_nav_collapsed", c ? "0" : "1"); } catch { /* storage blocked */ }
      return !c;
    });
  };
  if (loading) return <Loading what="Starting" />;
  if (!me) return <Login />;
  return (
    <div className={`shell ${collapsed ? "nav-collapsed" : ""}`}>
      <Sidebar collapsed={collapsed} />
      <div className="main">
        <AppBar onToggleSidebar={toggle} />
        <main className="content">
          {!me.companies.length ? (
            <Routes>
              <Route path="/profile" element={<Profile />} />
              <Route path="*" element={<Welcome />} />
            </Routes>
          ) : (
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/periods" element={<Periods />} />
            <Route path="/adjustments" element={<Adjustments />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/:runId" element={<Runs />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/compare" element={<Compare />} />
            <Route path="/master" element={<MasterSheet />} />
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
