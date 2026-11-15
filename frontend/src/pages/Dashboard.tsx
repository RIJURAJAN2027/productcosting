import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, qs } from "../api";
import { useApp } from "../context";
import { fmt, pct, UNIT_LABEL } from "../format";
import type { Kpis, Run } from "../types";
import { ErrorBox, Loading } from "../components/ui";
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

const UNIT_SHORT: Record<string, string> = { rupees: "", thousand: "'000", lakh: "lakh", million: "mn", crore: "cr" };
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const money = (v: number | undefined, unit: string, d = 2) =>
  v === undefined ? "–" : `${v < 0 ? "−" : ""}₹${fmt(Math.abs(v), d)}${UNIT_SHORT[unit] ? ` ${UNIT_SHORT[unit]}` : ""}`;

// ---------------------------------------------------------------- KPI card
type Change = { text: string; good: boolean; up: boolean } | null;

function KpiCard({ label, value, unit, icon, change, against, foot, to }: {
  label: string; value?: number; unit: string; icon: IconName; change: Change; against: string; foot?: string; to: string;
}) {
  return (
    <Link to={to} className="r-kpi">
      <div className="r-kpi-top">
        <span className="r-kpi-label">{label}</span>
        <span className="r-kpi-icon"><Icon name={icon} size={18} /></span>
      </div>
      <div className="r-kpi-value">
        {value === undefined ? "–" : <>{value < 0 ? "−" : ""}<span className="cur">₹</span>{fmt(Math.abs(value))}<small>{UNIT_SHORT[unit]}</small></>}
      </div>
      <div className="r-kpi-foot">
        {change ? (
          <span className={`r-chip ${change.good ? "good" : "bad"}`}><Icon name="trend" size={13} className={change.up ? "" : "flip"} />{change.text}</span>
        ) : null}
        <span className="muted">{change ? against : foot ?? ""}</span>
      </div>
    </Link>
  );
}

// ---------------------------------------------------------------- reports pack (presentation and downloads)
function ReportsPack() {
  const { companyId, periods, periodId, unit } = useApp();
  const actual = periods;
  const [pid, setPid] = useState<number | null>(periodId);
  useEffect(() => { setPid(periodId); }, [periodId]);
  const runs = useQuery({ queryKey: ["runs", companyId], queryFn: () => api.get<Run[]>(`/api/companies/${companyId}/runs`), enabled: companyId !== null });
  const period = actual.find((p) => p.id === pid);
  const run = (runs.data ?? []).find((r) => r.period_id === pid && r.status === "published")
    ?? (runs.data ?? []).find((r) => r.period_id === pid && r.status !== "failed");
  const [busy, setBusy] = useState<string | null>(null);
  const get = async (what: string, path: string) => {
    setBusy(what);
    try { await api.download(path); } finally { setBusy(null); }
  };
  return (
    <section className="r-card r-pack">
      <div className="r-pack-info">
        <span className="r-pack-icon"><Icon name="ppt" size={22} /></span>
        <div className="r-pack-text">
          <h2>Reports pack</h2>
          <p>{run ? <>Run #{run.id}</> : "No calculation for this period yet"}
            {period?.uploads.BA_TB ? <span className="r-file" title={period.uploads.BA_TB.filename}><Icon name="check" size={13} />Business-area TB</span> : null}
            {period?.uploads.CC_TB ? <span className="r-file" title={period.uploads.CC_TB.filename}><Icon name="check" size={13} />Cost-centre TB</span> : null}</p>
        </div>
      </div>
      <label className="r-select">
        <Icon name="calc" size={15} />
        <select value={pid ?? ""} onChange={(e) => setPid(Number(e.target.value))} aria-label="Period for the reports pack">
          {actual.map((p) => <option key={p.id} value={p.id}>{p.label || p.code}{p.scenario !== "actual" ? ` (${p.scenario})` : ""}</option>)}
        </select>
      </label>
      <div className="r-pack-actions">
        <button disabled={!run || !!busy} onClick={() => get("ppt", `/api/runs/${run!.id}/export/presentation.pptx${qs({ unit })}`)}><Icon name="ppt" size={16} />{busy === "ppt" ? "Preparing…" : "PowerPoint"}</button>
        <button disabled={!run || !!busy} onClick={() => get("pdf", `/api/runs/${run!.id}/export/presentation.pdf${qs({ unit })}`)}><Icon name="doc" size={16} />{busy === "pdf" ? "Preparing…" : "PDF"}</button>
        <button disabled={!run || !!busy} onClick={() => get("xlsx", `/api/runs/${run!.id}/export/pack.xlsx${qs({ unit })}`)}><Icon name="tb" size={16} />{busy === "xlsx" ? "Preparing…" : "Excel"}</button>
        <button className="primary" disabled={!run || !!busy} onClick={() => get("all", `/api/runs/${run!.id}/export/all.zip${qs({ unit })}`)}><Icon name="zip" size={16} />{busy === "all" ? "Preparing…" : "Download all"}</button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- chart tooltip
function ChartTip({ active, payload, label, unit }: { active?: boolean; payload?: { name: string; value: number; color: string }[]; label?: string; unit: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="r-tip">
      <div className="r-tip-title">{label}</div>
      {payload.map((p) => (
        <div key={p.name} className="r-tip-row"><i style={{ background: p.color }} />{p.name}<strong>{fmt(p.value)}</strong></div>
      ))}
      <div className="r-tip-unit">{UNIT_LABEL[unit]}</div>
    </div>
  );
}

// ---------------------------------------------------------------- page
type ChartView = "plant" | "period";

export default function Dashboard() {
  const { companyId, periodId, unit, periods, company } = useApp();
  const q = useQuery({
    queryKey: ["dashboard", companyId, periodId, unit],
    queryFn: () => api.get<DashboardData>(`/api/companies/${companyId}/dashboard${qs({ period_id: periodId, unit })}`),
    enabled: companyId !== null,
  });
  const runId = q.data?.current?.run.id;
  const runQ = useQuery({ queryKey: ["run", runId], queryFn: () => api.get<Run>(`/api/runs/${runId}`), enabled: !!runId });
  const [view, setView] = useState<ChartView>("plant");
  const [slide, setSlide] = useState(0);
  useEffect(() => { if ((q.data?.trend.length ?? 0) >= 2) setView("period"); }, [q.data?.trend.length]);

  const issues = useMemo(() => (runQ.data?.controls ?? []).filter((x) => x.level !== "ok")
    .sort((a, b) => (a.level === "error" ? -1 : 1) - (b.level === "error" ? -1 : 1)), [runQ.data]);
  useEffect(() => {
    if (issues.length < 2) return;
    const t = setInterval(() => setSlide((s) => (s + 1) % issues.length), 5000);
    return () => clearInterval(t);
  }, [issues.length]);

  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data) return <Loading />;
  const d = q.data;
  const cur = d.current;
  if (!cur) {
    return (
      <div className="r-card empty">
        No calculated period yet. <Link to="/periods">Upload the trial balances</Link> and calculate.
      </div>
    );
  }
  const c = cur.company;
  const sales = num(c.sales) ?? 0;

  // change against the previous calculated period, compared per month so YTD periods of different length are fair
  const idx = d.trend.findIndex((t) => t.period === cur.period);
  const prev = idx > 0 ? d.trend[idx - 1] : undefined;
  const monthsOf = (code: string) => periods.find((p) => p.code === code && p.scenario === "actual")?.months || 1;
  const mCur = monthsOf(cur.period);
  const mPrev = prev ? monthsOf(prev.period) : 1;
  const budget = cur.budget;
  const against = prev ? `vs ${prev.label}${mCur !== mPrev ? " (per month)" : ""}` : budget ? "vs budget" : "";
  const change = (key: string, higherIsBetter: boolean): Change => {
    const v = num(c[key]);
    if (v === undefined) return null;
    let base: number | undefined;
    let now = v;
    if (prev) { base = num(prev[key]); if (base !== undefined) { base /= mPrev; now = v / mCur; } }
    else if (budget) base = num(budget[key]);
    if (base === undefined || Math.abs(base) < 1e-9) return null;
    const r = (now - base) / Math.abs(base);
    return { text: `${r >= 0 ? "+" : "−"}${Math.abs(r * 100).toFixed(1)}%`, up: r >= 0, good: higherIsBetter ? r >= 0 : r <= 0 };
  };
  const ppChange = (key: string, higherIsBetter: boolean): Change => {
    const v = num(c[key]);
    const base = prev ? num(prev[key]) : budget ? num(budget[key]) : undefined;
    if (v === undefined || base === undefined) return null;
    const pp = (v - base) * 100;
    if (Math.abs(pp) < 0.05) return { text: "0.0 pp", up: true, good: true };
    return { text: `${pp >= 0 ? "+" : "−"}${Math.abs(pp).toFixed(1)} pp`, up: pp >= 0, good: higherIsBetter ? pp >= 0 : pp <= 0 };
  };

  // chart data
  // plants with material figures only (a plant with no sales and a few rupees of cost is noise on a chart)
  const tiny = Math.max(Math.abs(sales) * 0.001, 0.005);
  const plants = cur.plants.filter((p) => Math.abs(p.sales ?? 0) >= tiny || Math.abs(p.pbt ?? 0) >= tiny);
  const plantMin = Math.min(0, ...plants.map((p) => Math.min(p.sales ?? 0, p.pbt ?? 0)));

  // cost structure: where each rupee of sales goes
  const STRUCT: { key: string; label: string; color: string }[] = [
    { key: "material_cost", label: "Material", color: "#0b3d6e" },
    { key: "variable_cost", label: "Variable cost", color: "#1f8fff" },
    { key: "factory_overhead", label: "Factory overhead", color: "#13b56b" },
    { key: "pre_factory", label: "People – factory", color: "#f59e0b" },
    { key: "corporate_overheads", label: "Corporate overhead", color: "#38bdf8" },
    { key: "pre_corporate", label: "People – corporate", color: "#ec4899" },
  ];
  const struct = STRUCT.map((s) => ({ ...s, v: num(c[s.key]) })).filter((s) => s.v !== undefined) as (typeof STRUCT[number] & { v: number })[];

  // product groups
  const material = Math.max(Math.abs(sales) * 0.001, 0.005);
  const status = (p: Kpis): { label: string; cls: string } => {
    const pbt = p.pbt ?? 0;
    if (Math.abs(p.sales ?? 0) < material) return pbt < -0.005 ? { label: "Loss", cls: "bad" } : { label: "No sales", cls: "muted" };
    if (pbt < -0.005) return { label: "Loss", cls: "bad" };
    if ((p.pbt_pct ?? 0) < 0.05) return { label: "Thin margin", cls: "warn" };
    return { label: "Profitable", cls: "good" };
  };
  const pgs = cur.product_groups.filter((p) => Math.abs(p.sales ?? 0) > 1e-9 || Math.abs(p.pbt ?? 0) > 1e-9);
  const counts = pgs.reduce<Record<string, number>>((t, p) => { const s = status(p).label; t[s] = (t[s] ?? 0) + 1; return t; }, {});
  const withSales = pgs.filter((p) => Math.abs(p.sales ?? 0) >= material).length;
  const healthy = counts["Profitable"] ?? 0;
  const health = withSales ? healthy / withSales : 0;
  const topPgs = [...pgs].sort((a, b) => (b.sales ?? 0) - (a.sales ?? 0)).slice(0, 6);
  const R = 52, C = 2 * Math.PI * R;

  const issue = issues.length ? issues[slide % issues.length] : null;
  // control amounts are in the company's calculation unit
  const calcUnit = Object.entries({ rupees: 1, thousand: 1e3, lakh: 1e5, million: 1e6, crore: 1e7 })
    .find(([, v]) => v === company?.settings.calc_unit_divisor)?.[0] ?? "crore";

  return (
    <div className="r-dash">
      <section className="r-kpis">
        <KpiCard label="Sales" value={num(c.sales)} unit={unit} icon="rupee" change={change("sales", true)} against={against} foot={cur.label} to="/reports?tab=plant_wise" />
        <KpiCard label="Gross margin" value={num(c.gross_margin)} unit={unit} icon="trend" change={ppChange("gross_margin_pct", true)} against={against}
          foot={`${pct(c.gross_margin_pct)} of sales`} to="/reports?tab=plant_wise" />
        <KpiCard label="Profit before tax" value={num(c.pbt)} unit={unit} icon="wallet" change={change("pbt", true)} against={against}
          foot={`${pct(c.pbt_pct)} of sales`} to="/reports?tab=plant_wise" />
        <KpiCard label="Material cost" value={num(c.material_cost)} unit={unit} icon="box" change={ppChange("material_cost_pct", false)} against={against}
          foot={`MC ${pct(c.material_cost_pct)} of sales`} to="/reports?tab=mc" />
      </section>

      <ReportsPack />

      <div className="r-grid">
        <section className="r-card r-chart">
          <div className="r-card-head">
            <div>
              <h2>Profitability</h2>
              <p>{view === "plant" ? `Sales and profit before tax by plant · ${cur.label}` : "Sales and profit before tax across calculated periods"}</p>
            </div>
            <label className="r-select">
              <Icon name={view === "plant" ? "factory" : "calc"} size={15} />
              <select value={view} onChange={(e) => setView(e.target.value as ChartView)} aria-label="Chart view">
                <option value="plant">By plant</option>
                <option value="period" disabled={d.trend.length < 2}>By period{d.trend.length < 2 ? " (needs 2 periods)" : ""}</option>
              </select>
            </label>
          </div>
          <div className="r-legend">
            <span><i style={{ background: "var(--r-indigo)" }} />Sales</span>
            <span><i style={{ background: "var(--r-green)" }} />Profit before tax</span>
          </div>
          <ResponsiveContainer width="100%" height={290}>
            {view === "plant" ? (
              <BarChart data={plants} margin={{ top: 10, right: 8, left: 0, bottom: 0 }} barGap={6} barCategoryGap="30%">
                <CartesianGrid stroke="var(--grid)" strokeDasharray="4 6" vertical={false} />
                <XAxis dataKey="plant" tick={{ fill: "var(--text-2)", fontSize: 13 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: "var(--text-3)", fontSize: 12 }} axisLine={false} tickLine={false} width={56} tickFormatter={(v: number) => (v === 0 ? "0" : fmt(v, 0))}
                  domain={[plantMin < 0 ? "auto" : 0, "auto"]} />
                <Tooltip content={<ChartTip unit={unit} />} cursor={{ fill: "var(--r-hover)" }} />
                <ReferenceLine y={0} stroke="var(--border-strong)" />
                <Bar dataKey="sales" name="Sales" fill="var(--r-indigo)" radius={[8, 8, 0, 0]} maxBarSize={46} isAnimationActive={false} />
                <Bar dataKey="pbt" name="Profit before tax" fill="var(--r-green)" radius={[8, 8, 0, 0]} maxBarSize={46} isAnimationActive={false} />
              </BarChart>
            ) : (
              <LineChart data={d.trend} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="var(--grid)" strokeDasharray="4 6" horizontal={false} />
                <XAxis dataKey="label" tick={{ fill: "var(--text-2)", fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: "var(--text-3)", fontSize: 12 }} axisLine={false} tickLine={false} width={56} tickFormatter={(v: number) => (v === 0 ? "0" : fmt(v, 0))} />
                <Tooltip content={<ChartTip unit={unit} />} cursor={{ stroke: "var(--text)", strokeWidth: 1.5 }} />
                <Line type="linear" dataKey="sales" name="Sales" stroke="var(--r-indigo)" strokeWidth={2.2} dot={{ r: 5, fill: "var(--surface)", strokeWidth: 2 }} activeDot={{ r: 6 }} isAnimationActive={false} />
                <Line type="linear" dataKey="pbt" name="Profit before tax" stroke="var(--r-green)" strokeWidth={2.2} dot={{ r: 5, fill: "var(--surface)", strokeWidth: 2 }} activeDot={{ r: 6 }} isAnimationActive={false} />
              </LineChart>
            )}
          </ResponsiveContainer>
        </section>

        <div className="r-side">
          <section className={`r-card r-checks ${issue ? (issue.level === "error" ? "has-error" : "has-warning") : "all-ok"}`}>
            <div className="r-card-head">
              <h2><Icon name="sparkle" size={18} />Calculation checks</h2>
              <Link className="r-btn light" to={`/runs/${cur.run.id}`}>View all<Icon name="chevron" size={14} className="rot-l" /></Link>
            </div>
            <div className="r-check-pill">
              <span className={`r-check-icon ${issue ? issue.level : "ok"}`}><Icon name={issue ? "alert" : "check"} size={18} /></span>
              <div>
                {issue ? (
                  <>
                    <strong>{issue.title}</strong>
                    <small>{issue.level === "error" ? "Error" : "Warning"}{Math.abs(issue.amount) > 1e-9 ? ` · ${money(issue.amount, calcUnit, Math.abs(issue.amount) < 0.01 ? 4 : 2)}` : ""} · run #{cur.run.id}</small>
                  </>
                ) : (
                  <>
                    <strong>All controls passed</strong>
                    <small>Run #{cur.run.id} · {cur.label}</small>
                  </>
                )}
              </div>
            </div>
            {issues.length > 1 ? (
              <div className="r-dots" role="tablist" aria-label="Checks">
                {issues.map((x, i) => (
                  <button key={x.code} className={i === slide % issues.length ? "on" : ""} onClick={() => setSlide(i)} aria-label={x.title} />
                ))}
              </div>
            ) : null}
          </section>

          <section className="r-card">
            <div className="r-card-head">
              <div><h2>Cost structure</h2><p>Share of sales · company</p></div>
            </div>
            <div className="r-bars">
              {struct.map((s) => {
                const share = sales ? s.v / sales : 0;
                return (
                  <div key={s.key} className="r-bar-row">
                    <span className="r-bar-label">{s.label}</span>
                    <span className="r-bar-track"><i style={{ width: `${Math.min(Math.max(share, 0), 1) * 100}%`, background: s.color }} /></span>
                    <span className="r-bar-value">{pct(share)}</span>
                  </div>
                );
              })}
            </div>
          </section>
        </div>
      </div>

      <div className="r-grid">
        <section className="r-card flush">
          <div className="r-card-head pad">
            <div><h2>Product groups</h2><p>Largest by sales · {UNIT_LABEL[unit]}</p></div>
            <Link className="r-btn" to="/reports?tab=all_pg">View all<Icon name="chevron" size={14} className="rot-l" /></Link>
          </div>
          <div className="r-table-wrap">
            <table className="r-table">
              <thead><tr><th>Product group</th><th className="num">Sales</th><th className="num">GM %</th><th className="num">PBT</th><th>Status</th></tr></thead>
              <tbody>
                {topPgs.map((p) => {
                  const st = status(p);
                  const meaningful = Math.abs(p.sales ?? 0) >= material;
                  return (
                    <tr key={p.pg}>
                      <td>{p.pg}</td>
                      <td className="num">{fmt(p.sales)}</td>
                      <td className="num">{meaningful ? pct(p.gross_margin_pct) : "n/m"}</td>
                      <td className={`num ${(p.pbt ?? 0) < -0.005 ? "neg" : ""}`}>{fmt(p.pbt)}</td>
                      <td><span className={`r-pill ${st.cls}`}>{st.label}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section className="r-card r-health">
          <div className="r-card-head">
            <div><h2>Product-group health</h2><p>Profitability of groups with sales</p></div>
          </div>
          <div className="r-health-body">
            <div className="r-ring" aria-label={`${Math.round(health * 100)}% of product groups profitable`}>
              <svg viewBox="0 0 128 128" width="138" height="138" aria-hidden>
                <circle cx="64" cy="64" r={R} fill="none" stroke="var(--surface-3)" strokeWidth="12" />
                <circle cx="64" cy="64" r={R} fill="none" stroke="var(--r-green)" strokeWidth="12" strokeLinecap="round"
                  strokeDasharray={`${C * health} ${C}`} transform="rotate(-90 64 64)" />
              </svg>
              <span className="r-ring-value">{Math.round(health * 100)}%</span>
            </div>
            <span className="r-divider" aria-hidden />
            <ul className="r-health-list">
              <li><span className="r-face good"><Icon name="check" size={14} /></span>Profitable<strong>{counts["Profitable"] ?? 0}</strong></li>
              <li><span className="r-face warn"><Icon name="percent" size={13} /></span>Thin margin<strong>{counts["Thin margin"] ?? 0}</strong></li>
              <li><span className="r-face bad"><Icon name="alert" size={13} /></span>Loss<strong>{counts["Loss"] ?? 0}</strong></li>
            </ul>
          </div>
        </section>
      </div>
    </div>
  );
}
