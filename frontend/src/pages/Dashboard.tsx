import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, Line, LineChart, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis } from "recharts";
import { api, qs } from "../api";
import { useApp } from "../context";
import { fmt, pct, UNIT_LABEL } from "../format";
import type { Kpis } from "../types";
import { Badge, ErrorBox, Loading } from "../components/ui";

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

const SERIES = [
  { key: "sales", label: "Sales", color: "var(--series-1)" },
  { key: "gross_margin", label: "Gross margin", color: "var(--series-2)" },
  { key: "pbt", label: "PBT", color: "var(--series-3)" },
];

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

function Kpi({ label, value, sub, budget, unit, higherIsBetter = true }: {
  label: string; value?: number | null; sub?: string; budget?: number | null; unit: string; higherIsBetter?: boolean;
}) {
  let variance = null;
  if (budget !== undefined && budget !== null && value !== undefined && value !== null) {
    const v = value - budget;
    const good = higherIsBetter ? v >= 0 : v <= 0;
    variance = (
      <div className="sub status-line" style={{ color: good ? "var(--good)" : "var(--bad)" }}>
        <span aria-hidden>{v >= 0 ? "▲" : "▼"}</span>
        {fmt(Math.abs(v))} {good ? "better" : "worse"} than budget
      </div>
    );
  }
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className="value">{fmt(value)}</div>
      <div className="sub">{sub ?? UNIT_LABEL[unit]}</div>
      {variance}
    </div>
  );
}

export default function Dashboard() {
  const { companyId, periodId, unit, company } = useApp();
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
  const last = d.trend[d.trend.length - 1];
  const plants = cur.plants.filter((p) => Math.abs(p.sales ?? 0) > 1e-9 || Math.abs(p.pbt ?? 0) > 1e-9);
  const pgs = cur.product_groups.filter((p) => Math.abs(p.sales ?? 0) > 1e-9).sort((a, b2) => (b2.pbt ?? 0) - (a.pbt ?? 0));
  return (
    <>
      <div className="page-head">
        <div>
          <h1>{company?.name}</h1>
          <p>
            {cur.label} · run #{cur.run.id} <Badge status={cur.run.status} /> · controls:{" "}
            {cur.controls.errors ? <Badge status="error">{cur.controls.errors} error(s)</Badge> : null}{" "}
            {cur.controls.warnings ? <Badge status="warning">{cur.controls.warnings} warning(s)</Badge> : null}{" "}
            {!cur.controls.errors && !cur.controls.warnings ? <Badge status="ok">all passed</Badge> : null}
          </p>
        </div>
        <div className="row">
          <Link className="btn" to={`/runs/${cur.run.id}`}>Run details</Link>
          <Link className="btn primary" to="/reports">Open reports</Link>
        </div>
      </div>

      <div className="kpis">
        <Kpi label="Sales" value={c.sales} budget={b?.sales} unit={unit} />
        <Kpi label="Gross margin" value={c.gross_margin} sub={`${pct(c.gross_margin_pct)} of sales`} budget={b?.gross_margin} unit={unit} />
        <Kpi label="Plant profit" value={c.plant_profit} sub={`${pct(c.plant_profit_pct)} of sales`} budget={b?.plant_profit} unit={unit} />
        <Kpi label="PBT" value={c.pbt} sub={`${pct(c.pbt_pct)} of sales`} budget={b?.pbt} unit={unit} />
        <Kpi label="Material cost" value={c.material_cost} sub={`MC ${pct(c.material_cost_pct)}`} budget={b?.material_cost} unit={unit} higherIsBetter={false} />
        {c.bep_per_month !== undefined ? <Kpi label="Break-even sales / month" value={c.bep_per_month} unit={unit} /> : null}
      </div>

      <div className="grid cols-2">
        <div className="card">
          <div className="card-head">
            <h2>Trend by period</h2>
            <div className="legend" aria-label="Legend">
              {SERIES.map((s) => <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>)}
            </div>
          </div>
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={d.trend} margin={{ top: 8, right: 96, left: 8, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="label" tick={{ fill: "var(--text-2)", fontSize: 12 }} axisLine={{ stroke: "var(--border)" }} tickLine={false} />
              <YAxis tick={{ fill: "var(--text-2)", fontSize: 12 }} axisLine={false} tickLine={false} width={64}
                tickFormatter={(v: number) => (v === 0 ? "0" : fmt(v, 0))} />
              <Tooltip content={<Tip unit={unit} />} cursor={{ stroke: "var(--border-strong)" }} />
              {SERIES.map((s) => (
                <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={2}
                  dot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)", fill: s.color }} activeDot={{ r: 6 }} isAnimationActive={false}>
                  <LabelList dataKey={s.key} content={(props) => {
                    const { x, y, index } = props as { x: number; y: number; index: number };
                    if (index !== d.trend.length - 1) return null;
                    return <text x={x + 8} y={y + 4} fontSize={11.5} fill="var(--text-2)">{s.label}</text>;
                  }} />
                </Line>
              ))}
            </LineChart>
          </ResponsiveContainer>
          <div className="muted small">{UNIT_LABEL[unit]} · latest: sales {fmt(last?.sales)}, PBT {fmt(last?.pbt)}</div>
        </div>

        <div className="card">
          <div className="card-head">
            <h2>Sales and PBT by plant</h2>
            <div className="legend">
              <span><i style={{ background: "var(--series-1)" }} />Sales</span>
              <span><i style={{ background: "var(--series-2)" }} />PBT</span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={plants} margin={{ top: 8, right: 8, left: 8, bottom: 0 }} barGap={2} barCategoryGap="28%">
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="plant" tick={{ fill: "var(--text-2)", fontSize: 12 }} axisLine={{ stroke: "var(--border)" }} tickLine={false} />
              <YAxis tick={{ fill: "var(--text-2)", fontSize: 12 }} axisLine={false} tickLine={false} width={64} tickFormatter={(v: number) => (v === 0 ? "0" : fmt(v, 0))} />
              <Tooltip content={<Tip unit={unit} />} cursor={{ fill: "var(--surface-3)" }} />
              <ReferenceLine y={0} stroke="var(--border-strong)" />
              <Bar dataKey="sales" name="Sales" fill="var(--series-1)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
              <Bar dataKey="pbt" name="PBT" fill="var(--series-2)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid cols-2">
        <div className="card">
          <div className="card-head"><h2>PBT by product group</h2><span className="muted small">{UNIT_LABEL[unit]}</span></div>
          <ResponsiveContainer width="100%" height={Math.max(220, pgs.length * 30)}>
            <BarChart data={pgs} layout="vertical" margin={{ top: 4, right: 40, left: 8, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" horizontal={false} />
              <XAxis type="number" tick={{ fill: "var(--text-2)", fontSize: 12 }} tickFormatter={(v: number) => (v === 0 ? "0" : fmt(v, 0))} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="pg" tick={{ fill: "var(--text-2)", fontSize: 12 }} width={70} axisLine={false} tickLine={false} />
              <Tooltip content={<Tip unit={unit} />} cursor={{ fill: "var(--surface-3)" }} />
              <ReferenceLine x={0} stroke="var(--border-strong)" />
              <Bar dataKey="pbt" name="PBT" radius={4} isAnimationActive={false}>
                {pgs.map((p) => <Cell key={p.pg} fill="var(--series-1)" />)}
                <LabelList dataKey="pbt" position="right" formatter={(v: number) => fmt(v, 1)} style={{ fill: "var(--text-2)", fontSize: 11 }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="card flush">
          <div className="card-head" style={{ padding: "16px 16px 0" }}><h2>Product-group profitability</h2></div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>Product group</th><th className="num">Sales</th><th className="num">MC %</th><th className="num">GM %</th><th className="num">PBT</th><th className="num">PBT %</th></tr>
              </thead>
              <tbody>
                {pgs.map((p) => (
                  <tr key={p.pg}>
                    <td>{p.pg}</td>
                    <td className="num">{fmt(p.sales)}</td>
                    <td className="num">{pct(p.material_cost_pct)}</td>
                    <td className="num">{pct(p.gross_margin_pct)}</td>
                    <td className={`num ${(p.pbt ?? 0) < 0 ? "neg" : ""}`}>{fmt(p.pbt)}</td>
                    <td className="num">{pct(p.pbt_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
