import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, LabelList, Line, LineChart, Pie, PieChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, qs } from "../api";
import { useApp } from "../context";
import { fmt, pct, UNIT_LABEL } from "../format";
import type { Kpis } from "../types";
import { Badge, ErrorBox, Loading } from "../components/ui";
import { Icon, type IconName } from "../components/icons";

interface DashboardData {
  unit: string;
  periods: { id: number; code: string; label: string }[];
  trend: ({ period: string; label: string; run_status: string } & Kpis)[];
  current: null | {
    period: string;
    label: string;
    run: { id: number; status: string };
    company: Kpis;
    plants: ({ plant: string } & Kpis)[];
    product_groups: ({ pg: string } & Kpis)[];
    budget: Kpis | null;
    controls: { errors?: number; warnings?: number; ok?: number };
  };
}

function Tip({ active, payload, label, unit }: { active?: boolean; payload?: { name: string; value: number; color: string }[]; label?: string; unit: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tip">
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
      {payload.map((p) => (
        <div key={p.name} className="row" style={{ gap: 6 }}>
          <i style={{ width: 10, height: 10, background: p.color, borderRadius: 2, display: "inline-block" }} />
          <span className="k">{p.name}</span>
          <strong style={{ marginLeft: "auto" }}>{fmt(p.value)} </strong>
        </div>
      ))}
      <div className="muted small">{UNIT_LABEL[unit]}</div>
    </div>
  );
}

const VIEWS = {
  margin: { label: "Sales & margin", series: [
    { key: "sales", label: "Sales", color: "#1f6fc5", id: "gSales" },
    { key: "gross_margin", label: "Gross margin", color: "#f2a516", id: "gGm" },
  ] },
  profit: { label: "Profit", series: [
    { key: "plant_profit", label: "Plant profit", color: "#7c5ce6", id: "gPp" },
    { key: "pbt", label: "PBT", color: "#13a89e", id: "gPbt" },
  ] },
} as const;
type View = keyof typeof VIEWS;

const MIX_COLORS = ["#1f6fc5", "#7c5ce6", "#f2a516", "#13a89e", "#e8537a", "#5aa9f0", "#9aa5b8"];

/** Small statistic with a coloured round icon (strip under the hero chart). */
function MiniStat({ icon, tone, label, value, sub }: { icon: IconName; tone: string; label: string; value: string; sub?: string }) {
  return (
    <div className="mini-stat">
      <span className={`dot-icon ${tone}`}><Icon name={icon} size={18} /></span>
      <div>
        <div className="label">{label}</div>
        <div className="value">{value}</div>
        {sub ? <div className="sub">{sub}</div> : null}
      </div>
    </div>
  );
}

/** Gradient stat card with a white sparkline drawn from the period trend. */
function StatCard({ tone, title, value, sub, data, dataKey, kind = "line" }: {
  tone: string; title: string; value: string; sub: React.ReactNode; data: Record<string, unknown>[]; dataKey: string; kind?: "line" | "bar";
}) {
  return (
    <div className={`stat-card ${tone}`}>
      <div className="stat-text">
        <div className="title">{title}</div>
        <div className="value">{value}</div>
        <div className="sub">{sub}</div>
      </div>
      {data.length > 1 ? (
        <div className="spark" aria-hidden>
          <ResponsiveContainer width="100%" height={56}>
            {kind === "bar" ? (
              <BarChart data={data} margin={{ top: 4, right: 0, left: 0, bottom: 0 }} barCategoryGap="22%">
                <Bar dataKey={dataKey} maxBarSize={16} fill="rgba(255,255,255,.75)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
              </BarChart>
            ) : (
              <LineChart data={data} margin={{ top: 6, right: 4, left: 4, bottom: 6 }}>
                <Line type="monotone" dataKey={dataKey} stroke="rgba(255,255,255,.9)" strokeWidth={2.2} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
              </LineChart>
            )}
          </ResponsiveContainer>
        </div>
      ) : null}
    </div>
  );
}

function Activity({ icon, tone, title, text, when }: { icon: IconName; tone: string; title: React.ReactNode; text: React.ReactNode; when?: string }) {
  return (
    <li className="activity">
      <span className="when">{when}</span>
      <span className={`dot-icon ${tone}`}><Icon name={icon} size={16} /></span>
      <div><div className="title">{title}</div><div className="text">{text}</div></div>
    </li>
  );
}

function pgStatus(p: Kpis): { label: string; cls: string } {
  const pbt = p.pbt ?? 0;
  if (pbt < 0) return { label: "Loss", cls: "loss" };
  if ((p.pbt_pct ?? 0) < 0.05) return { label: "Thin margin", cls: "thin" };
  return { label: "Profitable", cls: "profit" };
}

export default function Dashboard() {
  const { companyId, periodId, unit, company } = useApp();
  const [view, setView] = useState<View>("margin");
  const q = useQuery({
    queryKey: ["dashboard", companyId, periodId, unit],
    queryFn: () => api.get<DashboardData>(`/api/companies/${companyId}/dashboard${qs({ period_id: periodId, unit })}`),
    enabled: companyId !== null,
  });
  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data) return <Loading />;
  const d = q.data;
  const cur = d.current;
  if (!cur) {
    return (
      <div className="card empty">
        No calculated period yet. <Link to="/periods">Upload trial balances</Link> and <Link to="/runs">run a calculation</Link>.
      </div>
    );
  }
  const c = cur.company;
  const b = cur.budget;
  const u = UNIT_LABEL[unit];
  const plants = cur.plants.filter((p) => Math.abs(p.sales ?? 0) > 1e-9 || Math.abs(p.pbt ?? 0) > 1e-9);
  const pgs = cur.product_groups.filter((p) => Math.abs(p.sales ?? 0) > 1e-9).sort((a, b2) => (b2.pbt ?? 0) - (a.pbt ?? 0));
  const series = VIEWS[view].series;

  // Sales mix by plant: top five, the rest grouped.
  const bySales = [...plants].filter((p) => (p.sales ?? 0) > 0).sort((a, b2) => (b2.sales ?? 0) - (a.sales ?? 0));
  const totalSales = bySales.reduce((t, p) => t + (p.sales ?? 0), 0);
  const mix = bySales.slice(0, 5).map((p) => ({ name: p.plant, value: p.sales ?? 0 }));
  const rest = bySales.slice(5).reduce((t, p) => t + (p.sales ?? 0), 0);
  if (rest > 0) mix.push({ name: "Others", value: rest });

  const variance = (v?: number | null, bv?: number | null, higherIsBetter = true) => {
    if (bv === undefined || bv === null || v === undefined || v === null) return <span>No budget for this period</span>;
    const diff = v - bv;
    const good = higherIsBetter ? diff >= 0 : diff <= 0;
    return <span className="var-chip">{diff >= 0 ? "▲" : "▼"} {fmt(Math.abs(diff))} {good ? "better" : "worse"} than budget</span>;
  };
  const errors = cur.controls.errors ?? 0;
  const warnings = cur.controls.warnings ?? 0;
  const loss = pgs.filter((p) => (p.pbt ?? 0) < 0).length;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Overview</div>
          <h1>{company?.name}</h1>
          <p>{cur.label} · run #{cur.run.id} <Badge status={cur.run.status} /></p>
        </div>
        <div className="row">
          <Link className="btn" to={`/runs/${cur.run.id}`}><Icon name="calc" size={15} />Run details</Link>
          <Link className="btn" to="/compare"><Icon name="compare" size={15} />Compare</Link>
          <Link className="btn primary" to="/reports"><Icon name="reports" size={15} />Open reports</Link>
        </div>
      </div>

      <div className="dash-top">
        <div className="card hero">
          <div className="hero-body">
            <div className="hero-figures">
              <div>
                <h3>Company performance</h3>
                <div className="muted small">{cur.label} · {u}</div>
              </div>
              <div>
                <div className="big">{fmt(c.sales)}</div>
                <div className="muted small">Sales this period</div>
              </div>
              <div>
                <div className={`big ${(c.pbt ?? 0) < 0 ? "neg" : ""}`}>{fmt(c.pbt)}</div>
                <div className="muted small">Profit before tax · {pct(c.pbt_pct)} of sales</div>
              </div>
              <Link className="pill-btn" to="/reports">P&amp;L reports</Link>
            </div>
            <div className="hero-chart">
              <div className="row hero-chart-head">
                <div className="seg-text" role="tablist" aria-label="Chart view">
                  {(Object.keys(VIEWS) as View[]).map((k) => (
                    <button key={k} role="tab" aria-selected={view === k} className={view === k ? "active" : ""} onClick={() => setView(k)}>{VIEWS[k].label}</button>
                  ))}
                </div>
                <div className="legend right">
                  {series.map((s) => <span key={s.key}><i style={{ background: s.color, borderRadius: "50%" }} />{s.label}</span>)}
                </div>
              </div>
              <ResponsiveContainer width="100%" height={240}>
                <AreaChart data={d.trend} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <defs>
                    {series.map((s) => (
                      <linearGradient key={s.id} id={s.id} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={s.color} stopOpacity={0.35} />
                        <stop offset="100%" stopColor={s.color} stopOpacity={0.02} />
                      </linearGradient>
                    ))}
                  </defs>
                  <CartesianGrid stroke="var(--grid)" strokeDasharray="4 4" vertical={false} />
                  <XAxis dataKey="label" tick={{ fill: "var(--text-3)", fontSize: 12 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: "var(--text-3)", fontSize: 12 }} axisLine={false} tickLine={false} width={60}
                    tickFormatter={(v: number) => (v === 0 ? "0" : fmt(v, 0))} />
                  <Tooltip content={<Tip unit={unit} />} cursor={{ stroke: "var(--border-strong)", strokeDasharray: "4 4" }} />
                  {series.map((s) => (
                    <Area key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={2.5}
                      fill={`url(#${s.id})`} dot={false} activeDot={{ r: 5, strokeWidth: 2, stroke: "var(--surface)" }} isAnimationActive={false} />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
          <div className="mini-stats">
            <MiniStat icon="trend" tone="rose" label="Gross margin" value={fmt(c.gross_margin)} sub={`${pct(c.gross_margin_pct)} of sales`} />
            <MiniStat icon="factory" tone="violet" label="Plant profit" value={fmt(c.plant_profit)} sub={`${pct(c.plant_profit_pct)} of sales`} />
            <MiniStat icon="box" tone="amber" label="Material cost" value={fmt(c.material_cost)} sub={`MC ${pct(c.material_cost_pct)}`} />
            <MiniStat icon="target" tone="sky" label="Break-even / month" value={c.bep_per_month !== undefined ? fmt(c.bep_per_month) : "–"} sub={u} />
          </div>
        </div>

        <div className="card mix">
          <div className="card-head"><h2>Sales mix by plant</h2><span className="muted small">{u}</span></div>
          <div className="donut">
            <ResponsiveContainer width="100%" height={230}>
              <PieChart>
                <Pie data={mix} dataKey="value" nameKey="name" innerRadius="68%" outerRadius="96%" paddingAngle={1.5} stroke="none" startAngle={90} endAngle={-270} isAnimationActive={false}>
                  {mix.map((m, i) => <Cell key={m.name} fill={MIX_COLORS[i % MIX_COLORS.length]} />)}
                </Pie>
                <Tooltip content={<Tip unit={unit} />} />
              </PieChart>
            </ResponsiveContainer>
            <div className="donut-center"><Icon name="factory" size={26} /></div>
          </div>
          <div className="mix-legend">
            {mix.map((m, i) => (
              <div key={m.name}>
                <div className="pct">{totalSales ? `${Math.round((m.value / totalSales) * 100)}%` : "–"}</div>
                <div className="name"><i style={{ background: MIX_COLORS[i % MIX_COLORS.length] }} />{m.name}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="stat-cards">
        <StatCard tone="navy" title="Sales" value={fmt(c.sales)} sub={variance(c.sales, b?.sales)} data={d.trend} dataKey="sales" />
        <StatCard tone="violet" title="Gross margin %" value={pct(c.gross_margin_pct) || "–"} sub={variance(c.gross_margin, b?.gross_margin)} data={d.trend} dataKey="gross_margin_pct" />
        <StatCard tone="teal" title="PBT %" value={pct(c.pbt_pct) || "–"} sub={variance(c.pbt, b?.pbt)} data={d.trend} dataKey="pbt_pct" kind="bar" />
        <StatCard tone="amber" title="Material cost %" value={pct(c.material_cost_pct) || "–"} sub={variance(c.material_cost, b?.material_cost, false)} data={d.trend} dataKey="material_cost_pct" kind="bar" />
      </div>

      <div className="dash-bottom">
        <div className="card">
          <div className="card-head"><h2>Calculation &amp; controls</h2><Link className="small" to={`/runs/${cur.run.id}`}>Details</Link></div>
          <ul className="activities">
            <Activity icon="calc" tone="sky" when={cur.label} title={<>Run #{cur.run.id} <Badge status={cur.run.status} /></>} text="Latest calculation for the selected period" />
            <Activity icon={errors ? "alert" : "check"} tone={errors ? "rose" : warnings ? "amber" : "teal"} when="Controls"
              title={errors ? `${errors} control error(s)` : warnings ? `${warnings} warning(s)` : "All controls passed"}
              text={errors || warnings ? <Link to={`/runs/${cur.run.id}`}>Review the controls</Link> : "Every rupee is accounted for"} />
            <Activity icon="layers" tone="violet" when="Periods" title={`${d.trend.length} calculated period(s)`}
              text={d.trend.length ? `${d.trend[0].label} – ${d.trend[d.trend.length - 1].label}` : ""} />
            <Activity icon="target" tone="amber" when="Budget" title={b ? "Budget available" : "No budget loaded"}
              text={b ? <Link to="/compare">Compare actual with budget</Link> : <Link to="/periods">Add a budget period</Link>} />
            <Activity icon="box" tone={loss ? "rose" : "teal"} when="Products" title={`${pgs.length} product group(s)`}
              text={loss ? `${loss} making a loss` : "None making a loss"} />
          </ul>
        </div>

        <div className="card flush">
          <div className="card-head" style={{ padding: "16px 18px 4px" }}>
            <div><h2>Product-group profitability</h2><div className="muted small">{cur.label} · {u}</div></div>
            <Link className="btn small" to="/reports">Full report</Link>
          </div>
          <div className="table-wrap" style={{ maxHeight: 440 }}>
            <table className="data pretty">
              <thead>
                <tr><th>Product group</th><th className="num">Sales</th><th className="num">MC %</th><th className="num">GM %</th><th className="num">PBT</th><th className="num">PBT %</th><th>Status</th></tr>
              </thead>
              <tbody>
                {pgs.map((p) => {
                  const st = pgStatus(p);
                  return (
                    <tr key={p.pg}>
                      <td><strong>{p.pg}</strong></td>
                      <td className="num">{fmt(p.sales)}</td>
                      <td className="num">{pct(p.material_cost_pct)}</td>
                      <td className="num">{pct(p.gross_margin_pct)}</td>
                      <td className={`num ${(p.pbt ?? 0) < 0 ? "neg" : ""}`}>{fmt(p.pbt)}</td>
                      <td className="num">{pct(p.pbt_pct)}</td>
                      <td><span className={`status-pill ${st.cls}`}>{st.label}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="grid cols-2">
        <div className="card">
          <div className="card-head">
            <h2>Sales and PBT by plant</h2>
            <div className="legend">
              <span><i style={{ background: "#1f6fc5" }} />Sales</span>
              <span><i style={{ background: "#f2a516" }} />PBT</span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={plants} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barGap={3} barCategoryGap="28%">
              <CartesianGrid stroke="var(--grid)" strokeDasharray="4 4" vertical={false} />
              <XAxis dataKey="plant" tick={{ fill: "var(--text-3)", fontSize: 12 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "var(--text-3)", fontSize: 12 }} axisLine={false} tickLine={false} width={60} tickFormatter={(v: number) => (v === 0 ? "0" : fmt(v, 0))} />
              <Tooltip content={<Tip unit={unit} />} cursor={{ fill: "var(--surface-3)" }} />
              <ReferenceLine y={0} stroke="var(--border-strong)" />
              <Bar dataKey="sales" name="Sales" fill="#1f6fc5" radius={[6, 6, 0, 0]} isAnimationActive={false} />
              <Bar dataKey="pbt" name="PBT" fill="#f2a516" radius={[6, 6, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="card">
          <div className="card-head"><h2>PBT by product group</h2><span className="muted small">{u}</span></div>
          <ResponsiveContainer width="100%" height={Math.max(260, pgs.length * 30)}>
            <BarChart data={pgs} layout="vertical" margin={{ top: 4, right: 48, left: 8, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" strokeDasharray="4 4" horizontal={false} />
              <XAxis type="number" tick={{ fill: "var(--text-3)", fontSize: 12 }} tickFormatter={(v: number) => (v === 0 ? "0" : fmt(v, 0))} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="pg" tick={{ fill: "var(--text-2)", fontSize: 12 }} width={70} axisLine={false} tickLine={false} />
              <Tooltip content={<Tip unit={unit} />} cursor={{ fill: "var(--surface-3)" }} />
              <ReferenceLine x={0} stroke="var(--border-strong)" />
              <Bar dataKey="pbt" name="PBT" radius={6} isAnimationActive={false}>
                {pgs.map((p) => <Cell key={p.pg} fill={(p.pbt ?? 0) < 0 ? "#e8537a" : "#7c5ce6"} />)}
                <LabelList dataKey="pbt" position="right" formatter={(v: number) => fmt(v, 1)} style={{ fill: "var(--text-2)", fontSize: 11 }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </>
  );
}
