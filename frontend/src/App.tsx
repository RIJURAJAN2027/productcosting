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
  { to: "/master", label: "Master", short: "Master", icon: "sheet", group: "Inputs" },
  { to: "/periods", label: "Periods & uploads", short: "Trial balances", icon: "upload", group: "Inputs" },
  { to: "/adjustments", label: "Adjustments & inputs", short: "Adjustments", icon: "adjust", group: "Inputs" },
  { to: "/runs", label: "Calculations", short: "Calculations", icon: "calc", group: "Costing" },
  { to: "/reports", label: "Reports", short: "P&L reports", icon: "reports", group: "Analysis" },
  { to: "/compare", label: "Compare", short: "Compare", icon: "compare", group: "Analysis" },
  { to: "/masters", label: "Masters", short: "Masters", icon: "masters", group: "Set-up" },
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
  const { me, logout, can } = useAuth();
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
          <Link to="/audit" role="menuitem"><Icon name="audit" size={16} />Audit trail</Link>
          {can("admin") ? <Link to="/admin" role="menuitem"><Icon name="admin" size={16} />Administration</Link> : null}
          <button role="menuitem" onClick={logout}><Icon name="logout" size={16} />Sign out</button>
        </div>
      ) : null}
    </div>
  );
}

function AppBar() {
  const { me, can } = useAuth();
  const app = useApp();
  const next = THEMES[(THEMES.findIndex((t) => t.v === app.theme) + 1) % THEMES.length];
  const cur = THEMES.find((t) => t.v === app.theme) ?? THEMES[0];
  const hasCompany = !!me?.companies.length;
  const items = NAV.filter((n) => !n.perm || can(n.perm));
  const groups = [...new Set(items.map((n) => n.group))];
  return (
    <header className="appbar">
      <div className="appbar-top">
        <Link to="/" className="brand-long" aria-label="Home">
          <img src="/brand/logo-long.png" alt="L&T Valves" />
        </Link>
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
          {groups.map((g, gi) => (
            <div key={g} className="module-group" role="group" aria-label={g}>
              {gi > 0 ? <span className="module-sep" aria-hidden /> : null}
              {items.filter((n) => n.group === g).map((n) => (
                <NavLink key={n.to} to={n.to} end={n.to === "/"} className="module" title={n.label}>
                  <Icon name={n.icon} size={16} />{n.short}
                </NavLink>
              ))}
            </div>
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

export default function App() {
  const { me, loading, can } = useAuth();
  if (loading) return <Loading what="Starting" />;
  if (!me) return <Login />;
  return (
    <div className="shell">
      <div className="main">
        <AppBar />
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
