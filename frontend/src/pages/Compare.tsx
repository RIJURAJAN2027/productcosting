import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, qs } from "../api";
import { useApp } from "../context";
import { fmt, pct, UNIT_LABEL } from "../format";
import type { Run } from "../types";
import { Badge, ErrorBox, Loading, Num, Seg, Tabs } from "../components/ui";

interface CmpRow { line: string; label: string; style: { bold?: boolean; fill?: string | null }; a: number; b: number; variance: number; variance_pct: number | null }
interface CmpSide { run_id: number; period: string; scenario: string; label: string }
interface Cmp { unit: string; level: string; plant: string; pg: string; a: CmpSide; b: CmpSide; rows: CmpRow[] }
interface McCmp {
  unit: string;
  periods: { id: number; code: string; label: string; run_id: number; run_status: string }[];
  rows: { plant: string; pg: string; by_period: Record<string, { sales: number; mc: number; mc_pct: number | null }> }[];
}

type Level = "company" | "plant" | "pg";

function runLabel(r: Run) {
  return `${r.period_label || r.period_code}${r.scenario !== "actual" ? ` (${r.scenario})` : ""} · run #${r.id} · ${r.status}`;
}

function PnlCompare() {
  const { companyId, period, unit, company } = useApp();
  const runs = useQuery({
    queryKey: ["runs", companyId],
    queryFn: () => api.get<Run[]>(`/api/companies/${companyId}/runs`),
    enabled: companyId !== null,
  });
  const ok = useMemo(() => (runs.data ?? []).filter((r) => r.status !== "failed"), [runs.data]);
  const [a, setA] = useState<number | null>(null);
  const [b, setB] = useState<number | null>(null);
  const [level, setLevel] = useState<Level>("company");
  const [plant, setPlant] = useState("");
  const [pg, setPg] = useState("");
  useEffect(() => {
    if (!ok.length) return;
    // A = latest run of the selected period; B = the budget run if there is one, else the previous actual period.
    const ra = ok.find((r) => r.period_id === period?.id) ?? ok[0];
    const budget = ok.find((r) => r.scenario !== "actual" && r.id !== ra.id);
    const prev = ok.find((r) => r.scenario === "actual" && r.period_id !== ra.period_id);
    setA(ra.id);
    setB((budget ?? prev ?? ra).id);
  }, [ok, period?.id]);
  const info = useQuery({ queryKey: ["run", a], queryFn: () => api.get<Run>(`/api/runs/${a}`), enabled: !!a });
  const plants = info.data?.plants ?? [];
  const pgs = info.data?.product_groups ?? [];
  useEffect(() => { if (plants.length && !plants.includes(plant)) setPlant(plants[0]); }, [plants]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (pgs.length && !pgs.includes(pg)) setPg(pgs[0]); }, [pgs]); // eslint-disable-line react-hooks/exhaustive-deps
  const q = useQuery({
    queryKey: ["compare", a, b, level, plant, pg, unit],
    queryFn: () => api.get<Cmp>(`/api/compare${qs({ run_a: a, run_b: b, level, plant: level !== "company" ? plant : undefined, pg: level === "pg" ? pg : undefined, unit })}`),
    enabled: !!a && !!b,
  });
  const nf = company?.settings.number_format ?? "indian";
  if (runs.isLoading) return <Loading />;
  if (ok.length < 1) return <div className="empty">Calculate at least two runs (for example an actual and a budget period) to compare them.</div>;
  return (
    <div className="stack" style={{ padding: 12 }}>
      <div className="row">
        <label className="row small">A
          <select value={a ?? ""} onChange={(e) => setA(Number(e.target.value))}>{ok.map((r) => <option key={r.id} value={r.id}>{runLabel(r)}</option>)}</select>
        </label>
        <span className="muted">vs</span>
        <label className="row small">B
          <select value={b ?? ""} onChange={(e) => setB(Number(e.target.value))}>{ok.map((r) => <option key={r.id} value={r.id}>{runLabel(r)}</option>)}</select>
        </label>
        <Seg value={level} onChange={setLevel} items={[{ key: "company", label: "Company" }, { key: "plant", label: "Plant" }, { key: "pg", label: "Plant × PG" }]} />
        {level !== "company" ? <select aria-label="Plant" value={plant} onChange={(e) => setPlant(e.target.value)}>{plants.map((p) => <option key={p}>{p}</option>)}</select> : null}
        {level === "pg" ? <select aria-label="Product group" value={pg} onChange={(e) => setPg(e.target.value)}>{pgs.map((p) => <option key={p}>{p}</option>)}</select> : null}
        <span className="right muted small">{UNIT_LABEL[unit]}</span>
      </div>
      {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
      {q.data ? (
        <div className="table-wrap" style={{ maxHeight: "calc(100vh - 280px)" }}>
          <table className="data">
            <thead>
              <tr>
                <th>Particulars</th>
                <th className="num">A · {q.data.a.label || q.data.a.period}{q.data.a.scenario !== "actual" ? ` (${q.data.a.scenario})` : ""}</th>
                <th className="num">B · {q.data.b.label || q.data.b.period}{q.data.b.scenario !== "actual" ? ` (${q.data.b.scenario})` : ""}</th>
                <th className="num">Variance (A − B)</th>
                <th className="num">Variance %</th>
              </tr>
            </thead>
            <tbody>
              {q.data.rows.map((r, i) => (
                <tr key={i} style={r.style?.bold ? { fontWeight: 650, background: "var(--surface-2)" } : undefined}>
                  <td>{r.label}</td>
                  <td className="num"><Num v={r.a} style={nf} /></td>
                  <td className="num"><Num v={r.b} style={nf} /></td>
                  <td className="num"><Num v={r.variance} style={nf} /></td>
                  <td className="num">{pct(r.variance_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

function McCompare() {
  const { companyId, periods, unit, company } = useApp();
  const actual = periods.filter((p) => p.scenario === "actual");
  const [sel, setSel] = useState<number[]>([]);
  useEffect(() => { setSel(actual.slice(-3).map((p) => p.id)); }, [periods]); // eslint-disable-line react-hooks/exhaustive-deps
  const q = useQuery({
    queryKey: ["mc-cmp", companyId, sel, unit],
    queryFn: () => api.get<McCmp>(`/api/companies/${companyId}/mc-comparison?${sel.map((s) => `period_ids=${s}`).join("&")}&unit=${unit}`),
    enabled: companyId !== null && sel.length > 0,
  });
  const [onlyTotals, setOnlyTotals] = useState(false);
  const nf = company?.settings.number_format ?? "indian";
  const d = q.data;
  const rows = (d?.rows ?? []).filter((r) => !onlyTotals || r.pg === "__TOTAL__");
  const first = d?.periods[0]?.code;
  const last = d?.periods[d.periods.length - 1]?.code;
  return (
    <div className="stack" style={{ padding: 12 }}>
      <div className="row">
        <span className="small muted">Periods:</span>
        {actual.map((p) => (
          <label key={p.id} className="row small">
            <input type="checkbox" checked={sel.includes(p.id)}
              onChange={(e) => setSel(e.target.checked ? [...sel, p.id].sort((x, y) => actual.findIndex((a) => a.id === x) - actual.findIndex((a) => a.id === y)) : sel.filter((x) => x !== p.id))} />
            {p.label || p.code}
          </label>
        ))}
        <label className="row small"><input type="checkbox" checked={onlyTotals} onChange={(e) => setOnlyTotals(e.target.checked)} /> plant totals only</label>
        <span className="right muted small">{UNIT_LABEL[unit]} · MC % = material cost (worked out) ÷ sales</span>
      </div>
      {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
      {d ? (
        <div className="table-wrap" style={{ maxHeight: "calc(100vh - 280px)" }}>
          <table className="data">
            <thead>
              <tr>
                <th rowSpan={2}>Plant</th><th rowSpan={2}>Product group</th>
                {d.periods.map((p) => <th key={p.code} colSpan={3} style={{ textAlign: "center" }}>{p.label || p.code} <Badge status={p.run_status} /></th>)}
                {d.periods.length > 1 ? <th rowSpan={2} className="num">Δ MC % (pp)</th> : null}
              </tr>
              <tr>
                {d.periods.map((p) => [<th key={p.code + "s"} className="num">Sales</th>, <th key={p.code + "m"} className="num">MC</th>, <th key={p.code + "p"} className="num">MC %</th>])}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const delta = first && last && r.by_period[first]?.mc_pct != null && r.by_period[last]?.mc_pct != null
                  ? ((r.by_period[last].mc_pct as number) - (r.by_period[first].mc_pct as number)) * 100 : null;
                return (
                  <tr key={i} style={r.pg === "__TOTAL__" ? { fontWeight: 650, background: "var(--surface-2)" } : undefined}>
                    <td>{r.plant === "__ALL__" ? "Company" : r.plant}</td>
                    <td>{r.pg === "__TOTAL__" ? "Total" : r.pg}</td>
                    {d.periods.map((p) => {
                      const v = r.by_period[p.code];
                      return [
                        <td key={p.code + "s"} className="num"><Num v={v?.sales} style={nf} /></td>,
                        <td key={p.code + "m"} className="num"><Num v={v?.mc} style={nf} /></td>,
                        <td key={p.code + "p"} className="num">{pct(v?.mc_pct)}</td>,
                      ];
                    })}
                    {d.periods.length > 1 ? (
                      <td className="num">{delta === null ? "" : (
                        <span className="status-line" style={{ justifyContent: "flex-end", color: Math.abs(delta) < 0.05 ? "var(--text-2)" : delta > 0 ? "var(--bad)" : "var(--good)" }}>
                          <span aria-hidden>{Math.abs(delta) < 0.05 ? "■" : delta > 0 ? "▲" : "▼"}</span>{fmt(delta, 1)}
                        </span>
                      )}</td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!rows.length ? <div className="empty">No calculated runs for the selected periods.</div> : null}
        </div>
      ) : null}
      <div className="muted small">▲ red = material cost share went up (worse), ▼ green = went down (better) between the first and last selected period.</div>
    </div>
  );
}

export default function Compare() {
  const [tab, setTab] = useState<"pnl" | "mc">("pnl");
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Compare</h1>
          <p>Period-on-period and budget-vs-actual comparison of any two calculation runs, and material-cost % across periods.</p>
        </div>
      </div>
      <div className="card flush">
        <div style={{ padding: "8px 12px 0" }}>
          <Tabs value={tab} onChange={setTab} items={[{ key: "pnl", label: "P&L: run A vs run B" }, { key: "mc", label: "MC % across periods" }]} />
        </div>
        {tab === "pnl" ? <PnlCompare /> : <McCompare />}
      </div>
    </>
  );
}
