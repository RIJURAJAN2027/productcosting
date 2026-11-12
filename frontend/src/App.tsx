import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useApp, useAuth } from "./context";
import { UNIT_LABEL } from "./format";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Periods from "./pages/Periods";
import Runs from "./pages/Runs";
import Reports from "./pages/Reports";
import Compare from "./pages/Compare";
import Masters from "./pages/Masters";
import Adjustments from "./pages/Adjustments";
import Audit from "./pages/Audit";
import Admin from "./pages/Admin";
import Profile from "./pages/Profile";
import Welcome from "./pages/Welcome";
import { Loading } from "./components/ui";

const NAV: { to: string; label: string; icon: string; perm?: "admin" }[] = [
  { to: "/", label: "Dashboard", icon: "◧" },
  { to: "/periods", label: "Periods & uploads", icon: "⇪" },
  { to: "/adjustments", label: "Adjustments & inputs", icon: "±" },
  { to: "/runs", label: "Calculations", icon: "▶" },
  { to: "/reports", label: "Reports", icon: "▤" },
  { to: "/compare", label: "Compare", icon: "⇆" },
  { to: "/masters", label: "Masters", icon: "☰" },
  { to: "/audit", label: "Audit trail", icon: "⌚" },
  { to: "/admin", label: "Administration", icon: "⚙", perm: "admin" },
];

function TopBar() {
  const { me } = useAuth();
  const app = useApp();
  return (
    <div className="topbar">
      {me?.companies.length ? (<>
      <select aria-label="Company" value={app.companyId ?? ""} onChange={(e) => app.setCompanyId(Number(e.target.value))}>
        {me?.companies.map((c) => (
          <option key={c.id} value={c.id}>{c.name}</option>
        ))}
      </select>
      <select aria-label="Period" value={app.periodId ?? ""} onChange={(e) => app.setPeriodId(Number(e.target.value))}>
        {app.periods.map((p) => (
          <option key={p.id} value={p.id}>
            {p.label || p.code} {p.scenario !== "actual" ? `(${p.scenario})` : ""} {p.status === "locked" ? "🔒" : ""}
          </option>
        ))}
      </select>
      </>) : null}
      <select aria-label="Display unit" value={app.unit} onChange={(e) => app.setUnit(e.target.value)}>
        {Object.entries(UNIT_LABEL).map(([k, v]) => (
          <option key={k} value={k}>{v}</option>
        ))}
      </select>
      <span className="grow" />
      <select aria-label="Theme" value={app.theme} onChange={(e) => app.setTheme(e.target.value as "auto" | "light" | "dark")}>
        <option value="auto">Theme: system</option>
        <option value="light">Theme: light</option>
        <option value="dark">Theme: dark</option>
      </select>
    </div>
  );
}

export default function App() {
  const { me, loading, logout, can } = useAuth();
  if (loading) return <Loading what="Starting" />;
  if (!me) return <Login />;
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden><rect width="32" height="32" rx="7" fill="rgba(255,255,255,.15)" /><path d="M8 22V12m6 10V8m6 14v-7m6 7V11" stroke="#fff" strokeWidth="3" strokeLinecap="round" /></svg>
          <div>Product Costing<small>& Profitability</small></div>
        </div>
        <nav className="nav">
          {NAV.filter((n) => !n.perm || can(n.perm)).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}>
              <span aria-hidden style={{ width: 18, textAlign: "center" }}>{n.icon}</span>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="spacer" />
        <div className="who">
          <div style={{ fontWeight: 600 }}>{me.name || me.email}</div>
          <div style={{ opacity: 0.75 }}>{me.role} · {me.tenant.name}</div>
          <div className="row" style={{ marginTop: 6 }}>
            <NavLink to="/profile" style={{ color: "#fff", opacity: 0.85 }}>Profile</NavLink>
            <button className="small ghost" style={{ color: "#fff" }} onClick={logout}>Sign out</button>
          </div>
        </div>
      </aside>
      <div className="main">
        <TopBar />
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
