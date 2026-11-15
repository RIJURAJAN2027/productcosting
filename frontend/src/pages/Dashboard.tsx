import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, qs } from "../api";
import { useApp } from "../context";
import { fmt, pct, UNIT_LABEL } from "../format";
import type { Kpis, Run } from "../types";
import { ErrorBox, Loading } from "../components/ui";
import { Icon } from "../components/icons";

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

// unit written after an amount: ₹738.13 cr
const UNIT_SHORT: Record<string, string> = { rupees: "", thousand: "'000", lakh: "lakh", million: "mn", crore: "cr" };
const money = (v: number | null | undefined, unit: string, d = 2) =>
  `${(v ?? 0) < 0 ? "−" : ""}₹${fmt(v == null ? v : Math.abs(v), d)}${UNIT_SHORT[unit] ? ` ${UNIT_SHORT[unit]}` : ""}`;
/** small amounts (controls) keep enough decimals to stay visible */
const smallMoney = (v: number, unit: string) => money(v, unit, Math.abs(v) < 0.01 ? 4 : 2);
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);

function Amount({ v, unit, d = 2 }: { v?: number | null; unit: string; d?: number }) {
  return (
    <span className={`amount ${(v ?? 0) < 0 ? "neg" : ""}`}>
      {(v ?? 0) < 0 ? "−" : ""}<span className="cur">₹</span>{fmt(v == null ? v : Math.abs(v), d)}{UNIT_SHORT[unit] ? <span className="unit">{UNIT_SHORT[unit]}</span> : null}
    </span>
  );
}

/** Variance to budget, coloured by whether it is good news. */
function Variance({ v, b, unit, higherIsBetter = true }: { v?: number; b?: number; unit: string; higherIsBetter?: boolean }) {
  if (v === undefined || b === undefined) return null;
  const diff = v - b;
  const good = higherIsBetter ? diff >= 0 : diff <= 0;
  return (
    <span className={`variance ${good ? "good" : "bad"}`}>
      {diff >= 0 ? "▲" : "▼"} {money(Math.abs(diff), unit)} vs budget
    </span>
  );
}

function KpiTile({ label, value, unit, sub, budget, higherIsBetter, to }: {
  label: string; value?: number; unit: string; sub?: string; budget?: number; higherIsBetter?: boolean; to: string;
}) {
  return (
    <Link to={to} className="kpi-tile">
      <span className="label">{label}</span>
      <span className="value"><Amount v={value} unit={unit} /></span>
      <span className="sub">{sub ?? " "}</span>
      <Variance v={value} b={budget} unit={unit} higherIsBetter={higherIsBetter} />
    </Link>
  );
}

// ---------------------------------------------------------------- profit bridge (Sales → PBT)
type Step = { label: string; value: number; kind: "total" | "up" | "down" };
const BRIDGE: { key: string; label: string; sign?: 1 | -1 }[] = [
  { key: "sales", label: "Sales" },
  { key: "material_cost", label: "Material cost", sign: -1 },
  { key: "variable_cost", label: "Variable cost", sign: -1 },
  { key: "gross_margin", label: "Gross margin" },
  { key: "factory_overhead", label: "Factory overhead", sign: -1 },
  { key: "pre_factory", label: "People cost – factory", sign: -1 },
  { key: "plant_profit", label: "Plant profit" },
  { key: "corporate_overheads", label: "Corporate overhead", sign: -1 },
  { key: "pre_corporate", label: "People cost – corporate", sign: -1 },
  { key: "other_credits", label: "Other income / (expense)", sign: 1 },
  { key: "pbt", label: "Profit before tax" },
];

function bridgeSteps(c: Kpis): Step[] {
  const steps: Step[] = [];
  let running: number | undefined;
  let pending: Step[] = [];
  for (const b of BRIDGE) {
    const v = num(c[b.key]);
    if (v === undefined) continue;
    if (!b.sign) {
      if (running !== undefined) {
        const moved = pending.reduce((t, s) => t + (s.kind === "up" ? s.value : -s.value), 0);
        const rest = v - running - moved;
        if (Math.abs(rest) > 0.005) pending.push({ label: "Other items", value: Math.abs(rest), kind: rest >= 0 ? "up" : "down" });
        steps.push(...pending);
      }
      pending = [];
      steps.push({ label: b.label, value: v, kind: "total" });
      running = v;
    } else if (running !== undefined) {
      const delta = v * b.sign;
      pending.push({ label: b.label, value: Math.abs(delta), kind: delta >= 0 ? "up" : "down" });
    }
  }
  return steps;
}

function ProfitBridge({ c, unit }: { c: Kpis; unit: string }) {
  const steps = bridgeSteps(c);
  // positions on a shared scale
  let run = 0;
  const bars = steps.map((s) => {
    let from: number, to: number;
    if (s.kind === "total") { from = 0; to = s.value; run = s.value; }
    else if (s.kind === "down") { from = run - s.value; to = run; run -= s.value; }
    else { from = run; to = run + s.value; run += s.value; }
    return { ...s, lo: Math.min(from, to), hi: Math.max(from, to) };
  });
  const min = Math.min(0, ...bars.map((b) => b.lo));
  const max = Math.max(...bars.map((b) => b.hi), 1e-9);
  const pos = (x: number) => ((x - min) / (max - min)) * 100;
  const sales = num(c.sales);
  return (
    <div className="bridge" role="table" aria-label="Profit bridge from sales to profit before tax">
      {bars.map((b, i) => (
        <div key={i} className={`bridge-row ${b.kind}`} role="row">
          <span className="b-label" role="cell">{b.label}</span>
          <span className="b-track" role="cell" aria-hidden>
            {min < 0 ? <i className="zero" style={{ left: `${pos(0)}%` }} /> : null}
            <i className="bar" style={{ left: `${pos(b.lo)}%`, width: `${Math.max(pos(b.hi) - pos(b.lo), 0.4)}%` }} />
          </span>
          <span className="b-value" role="cell">
            {b.kind === "down" ? "−" : b.kind === "up" ? "+" : ""}{fmt(b.value)}
            <small>{sales ? pct(b.value / sales) : ""}</small>
          </span>
        </div>
      ))}
      <div className="bridge-foot muted small">{UNIT_LABEL[unit]} · % of sales</div>
    </div>
  );
}

// ---------------------------------------------------------------- page
type SortKey = "pbt" | "sales" | "pbt_pct" | "gross_margin_pct" | "material_cost_pct";

export default function Dashboard() {
  const { companyId, periodId, unit, company } = useApp();
  const [sort, setSort] = useState<SortKey>("pbt");
  const q = useQuery({
    queryKey: ["dashboard", companyId, periodId, unit],
    queryFn: () => api.get<DashboardData>(`/api/companies/${companyId}/dashboard${qs({ period_id: periodId, unit })}`),
    enabled: companyId !== null,
  });
  const runId = q.data?.current?.run.id;
  const runQ = useQuery({ queryKey: ["run", runId], queryFn: () => api.get<Run>(`/api/runs/${runId}`), enabled: !!runId });

  const pgs = useMemo(() => {
    const list = (q.data?.current?.product_groups ?? []).filter((p) => Math.abs(p.sales ?? 0) > 1e-9 || Math.abs(p.pbt ?? 0) > 1e-9);
    return [...list].sort((a, b) => ((num(b[sort]) ?? -Infinity) - (num(a[sort]) ?? -Infinity)));
  }, [q.data, sort]);

  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data) return <Loading />;
  const d = q.data;
  const cur = d.current;
  if (!cur) {
    return (
      <div className="card empty">
        No calculated period yet. <Link to="/periods">Upload the trial balances</Link>, then <Link to="/runs">run a calculation</Link>.
      </div>
    );
  }
  const c = cur.company;
  const b = cur.budget;
  const plants = cur.plants.filter((p) => Math.abs(p.sales ?? 0) > 1e-9 || Math.abs(p.pbt ?? 0) > 1e-9);
  const maxSales = Math.max(...plants.map((p) => p.sales ?? 0), 1e-9);
  // a % of sales is only shown when sales are at least 0.1% of company sales; otherwise it is not meaningful
  const material = Math.max(Math.abs(c.sales ?? 0) * 0.001, 0.005);
  const ofSales = (v: number | null | undefined, sales: number | undefined) => (Math.abs(sales ?? 0) >= material ? pct(v) : "n/m");
  const isLoss = (v: number | undefined) => (v ?? 0) < -0.005;
  const maxPbtAbs = Math.max(...pgs.map((p) => Math.abs(p.pbt ?? 0)), 1e-9);
  const issues = (runQ.data?.controls ?? []).filter((x) => x.level !== "ok").sort((a, b2) => (a.level === "error" ? -1 : 1) - (b2.level === "error" ? -1 : 1));
  const errors = cur.controls.errors ?? 0;
  const warnings = cur.controls.warnings ?? 0;
  const u = unit;
  // control amounts are in the calculation unit (company setting), not the display unit
  const calcUnit = Object.entries({ rupees: 1, thousand: 1e3, lakh: 1e5, million: 1e6, crore: 1e7 })
    .find(([, v]) => v === company?.settings.calc_unit_divisor)?.[0] ?? "crore";
  const head = (k: SortKey, label: string) => (
    <th className="num">
      <button className={`th-sort ${sort === k ? "on" : ""}`} onClick={() => setSort(k)} aria-sort={sort === k ? "descending" : "none"}>
        {label}{sort === k ? " ↓" : ""}
      </button>
    </th>
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{company?.name}</h1>
          <p className="head-meta">
            <span>Run #{cur.run.id}</span>
            <span>Amounts in {UNIT_LABEL[u]}</span>
          </p>
        </div>
        <div className="row">
          <Link className="btn" to={`/runs/${cur.run.id}`}>Run details</Link>
          <Link className="btn primary" to="/reports"><Icon name="reports" size={15} />Open P&amp;L reports</Link>
        </div>
      </div>

      {errors || warnings ? (
        <div className={`alert ${errors ? "error" : "warning"}`} role="alert">
          <Icon name="alert" size={18} />
          <div className="grow">
            <strong>
              {errors ? plural(errors, "control error") : ""}{errors && warnings ? " and " : ""}{warnings ? plural(warnings, "warning") : ""}
              {errors ? " – check before submitting this run" : ""}
            </strong>
            {issues.length ? (
              <ul>
                {issues.slice(0, 4).map((x) => (
                  <li key={x.code}><span className={`lvl ${x.level}`}>{x.level}</span>{x.title}
                    {Math.abs(x.amount) > 1e-9 ? <span className="muted"> · {smallMoney(x.amount, calcUnit)}</span> : null}</li>
                ))}
              </ul>
            ) : null}
          </div>
          <Link className="btn small" to={`/runs/${cur.run.id}`}>Review controls</Link>
        </div>
      ) : null}

      <section className="kpi-strip" aria-label="Key figures">
        <KpiTile label="Sales" value={num(c.sales)} unit={u} budget={num(b?.sales)} to="/reports?tab=plant_wise" />
        <KpiTile label="Material cost" value={num(c.material_cost)} unit={u} sub={`MC ${pct(c.material_cost_pct)} of sales`}
          budget={num(b?.material_cost)} higherIsBetter={false} to="/reports?tab=mc" />
        <KpiTile label="Gross margin" value={num(c.gross_margin)} unit={u} sub={`${pct(c.gross_margin_pct)} of sales`} budget={num(b?.gross_margin)} to="/reports?tab=plant_wise" />
        <KpiTile label="Plant profit" value={num(c.plant_profit)} unit={u} sub={`${pct(c.plant_profit_pct)} of sales`} budget={num(b?.plant_profit)} to="/reports?tab=plant_wise" />
        <KpiTile label="Profit before tax" value={num(c.pbt)} unit={u} sub={`${pct(c.pbt_pct)} of sales`} budget={num(b?.pbt)} to="/reports?tab=plant_wise" />
        {num(c.bep_per_month) !== undefined ? <KpiTile label="Break-even sales" value={num(c.bep_per_month)} unit={u} sub="per month" to="/reports?tab=summary" /> : null}
      </section>

      <div className="dash-grid">
        <div className="card">
          <div className="card-head">
            <div><h2>Profit bridge</h2><div className="muted small">How sales turn into profit before tax · company</div></div>
          </div>
          <ProfitBridge c={c} unit={u} />
        </div>

        <div className="card">
          <div className="card-head">
            <div><h2>Plants</h2><div className="muted small">Sales and profit before tax</div></div>
            <Link className="small" to="/reports?tab=plant_wise">Plant-wise P&amp;L</Link>
          </div>
          <div className="plant-list">
            {plants.map((p) => (
              <Link key={p.plant} to="/reports?tab=pg_wise" className="plant-row">
                <span className="code">{p.plant}</span>
                <span className="track" aria-hidden><i style={{ width: `${Math.max(((p.sales ?? 0) / maxSales) * 100, 0)}%` }} /></span>
                <span className="vals">
                  <span>{fmt(p.sales)} <small>sales</small></span>
                  <span className={isLoss(p.pbt) ? "neg" : ""}>{fmt(p.pbt)} <small>PBT · {ofSales(p.pbt_pct, p.sales)}</small></span>
                </span>
              </Link>
            ))}
            {!plants.length ? <div className="muted">No plant has sales in this period.</div> : null}
            <div className="muted small">{UNIT_LABEL[u]} · plants without sales or profit are hidden</div>
          </div>
        </div>
      </div>

      {d.trend.length >= 2 ? (
        <div className="card">
          <div className="card-head">
            <div><h2>Trend</h2><div className="muted small">Calculated periods · {UNIT_LABEL[u]}</div></div>
            <div className="legend">
              <span><i style={{ background: "var(--series-1)" }} />Sales</span>
              <span><i style={{ background: "var(--series-2)" }} />Gross margin</span>
              <span><i style={{ background: "var(--series-3)" }} />Profit before tax</span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={d.trend} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="label" tick={{ fill: "var(--text-2)", fontSize: 12 }} axisLine={{ stroke: "var(--border)" }} tickLine={false} />
              <YAxis tick={{ fill: "var(--text-2)", fontSize: 12 }} axisLine={false} tickLine={false} width={60} tickFormatter={(v: number) => (v === 0 ? "0" : fmt(v, 0))} />
              <Tooltip formatter={(v: number) => fmt(v)} contentStyle={{ borderRadius: 8, border: "1px solid var(--border)", background: "var(--surface)" }} />
              <Line type="monotone" dataKey="sales" name="Sales" stroke="var(--series-1)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
              <Line type="monotone" dataKey="gross_margin" name="Gross margin" stroke="var(--series-2)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
              <Line type="monotone" dataKey="pbt" name="Profit before tax" stroke="var(--series-3)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : null}

      <div className="card flush">
        <div className="card-head" style={{ padding: "16px 18px 6px" }}>
          <div><h2>Product groups</h2><div className="muted small">All plants · {UNIT_LABEL[u]} · click a column to sort · losses and margins under 5% are flagged · n/m = not meaningful (no sales)</div></div>
          <Link className="small" to="/reports?tab=all_pg">All-PG report</Link>
        </div>
        <div className="table-wrap">
          <table className="data pg-table">
            <thead>
              <tr>
                <th>Product group</th>
                {head("sales", "Sales")}
                {head("material_cost_pct", "MC %")}
                {head("gross_margin_pct", "GM %")}
                {head("pbt", "PBT")}
                {head("pbt_pct", "PBT %")}
                <th className="bar-col" aria-label="PBT compared" />
              </tr>
            </thead>
            <tbody>
              {pgs.map((p) => {
                const pbt = p.pbt ?? 0;
                const meaningful = Math.abs(p.sales ?? 0) >= material;
                const flag = isLoss(pbt) ? "Loss" : meaningful && (p.pbt_pct ?? 0) < 0.05 ? "Thin margin" : !meaningful ? "No sales" : "";
                const w = (Math.abs(pbt) / maxPbtAbs) * 50;
                return (
                  <tr key={p.pg} className={isLoss(pbt) ? "loss" : ""}>
                    <td><strong>{p.pg}</strong>{flag ? <span className={`flag ${isLoss(pbt) ? "bad" : flag === "No sales" ? "info" : "warn"}`}>{flag}</span> : null}</td>
                    <td className="num">{fmt(p.sales)}</td>
                    <td className="num">{ofSales(p.material_cost_pct, p.sales)}</td>
                    <td className="num">{ofSales(p.gross_margin_pct, p.sales)}</td>
                    <td className={`num ${isLoss(pbt) ? "neg" : ""}`}>{fmt(p.pbt)}</td>
                    <td className="num">{ofSales(p.pbt_pct, p.sales)}</td>
                    <td className="bar-col" aria-hidden>
                      <span className="pbt-bar"><i className={pbt < 0 ? "neg" : ""} style={pbt < 0 ? { right: "50%", width: `${w}%` } : { left: "50%", width: `${w}%` }} /></span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
