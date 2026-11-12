import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, qs } from "../api";
import { useApp } from "../context";
import { fmt, pct, UNIT_LABEL } from "../format";
import type { Report, ReportRow, Run } from "../types";
import { Badge, Drawer, ErrorBox, Loading, Num, Tabs } from "../components/ui";

type Tab = "plant_wise" | "pg_wise" | "all_pg" | "pg_totals" | "mc" | "mis" | "summary";
const TABS: { key: Tab; label: string }[] = [
  { key: "plant_wise", label: "Plant-wise P&L" },
  { key: "pg_wise", label: "PG-wise P&L" },
  { key: "all_pg", label: "All PG" },
  { key: "pg_totals", label: "PG totals" },
  { key: "mc", label: "MC %" },
  { key: "mis", label: "MIS check" },
  { key: "summary", label: "Management summary" },
];

interface DrillPart { label: string; value: number; coefficient: number; kind: string; plant: string; code: string; category?: string; component?: string;
  sources: { gl: string | null; gl_desc?: string | null; dimension: string; value: number | null; share?: number; note?: string }[] }
interface Drill { line: string; label: string; plant: string; code: string; unit: string; value: number; explained: number; parts: DrillPart[] }

function DrillDown({ runId, line, plant, code, unit, onClose }: { runId: number; line: string; plant: string; code: string; unit: string; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["drill", runId, line, plant, code, unit],
    queryFn: () => api.get<Drill>(`/api/runs/${runId}/drilldown${qs({ line, plant, code, unit })}`),
  });
  const [open, setOpen] = useState<number | null>(0);
  const d = q.data;
  return (
    <Drawer title={d ? d.label : "Drill-down"} onClose={onClose}
      subtitle={<>{plant} · {code === "__TOTAL__" ? "plant total" : code} · {UNIT_LABEL[unit]}</>}>
      {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
      {d ? (
        <>
          <div className="kpis">
            <div className="kpi"><div className="label">Value</div><div className="value"><Num v={d.value} /></div></div>
            <div className="kpi"><div className="label">Explained by components</div><div className="value"><Num v={d.explained} /></div>
              <div className="sub">{Math.abs(d.value - d.explained) < 1e-6 ? "✓ reconciles" : "difference " + fmt(d.value - d.explained, 4)}</div></div>
          </div>
          <div className="muted small">Each component is a category part (direct, Common allocation, Corporate allocation), a PRE (people cost) item or a material-cost adjustment. Open one to see the GL × business-area / cost-centre rows behind it – allocated parts show the pool's rows × the share that reached this cell, so the rows add up to the component.</div>
          {d.parts.map((p, i) => (
            <div key={i} className="control">
              <div className="row clickable" style={{ cursor: "pointer" }} onClick={() => setOpen(open === i ? null : i)}>
                <span aria-hidden>{open === i ? "▾" : "▸"}</span>
                <strong>{p.label}</strong>
                {p.coefficient !== 1 && p.kind !== "const" ? <span className="badge">× {fmt(p.coefficient, 4).replace(/\.?0+$/, "")}</span> : null}
                <span className="right mono"><Num v={p.value} /></span>
              </div>
              {open === i ? (
                p.sources.length ? (
                  <div className="table-wrap" style={{ maxHeight: 340, marginTop: 8 }}>
                    <table className="data">
                      <thead><tr><th>GL</th><th>Description</th><th>BA / CC</th><th className="num">Share</th><th className="num">Amount</th></tr></thead>
                      <tbody>
                        {p.sources.map((s, j) => (
                          <tr key={j}>
                            <td className="mono">{s.gl ?? ""}</td><td>{s.gl_desc ?? s.note ?? ""}</td><td className="mono">{s.dimension}</td>
                            <td className="num">{s.share !== undefined && s.share !== 1 ? pct(s.share, 3) : ""}</td>
                            <td className="num"><Num v={s.value} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <div className="muted small" style={{ marginTop: 6 }}>No source rows.</div>
              ) : null}
            </div>
          ))}
        </>
      ) : null}
    </Drawer>
  );
}

function PnlTable({ rep, showPct, onDrill, numberFormat, decimals }: {
  rep: Report; showPct: boolean; onDrill: (row: ReportRow, colIdx: number) => void; numberFormat: "indian" | "international"; decimals: number;
}) {
  const cols = rep.columns;
  return (
    <div className="pnl-wrap">
      <table className="pnl">
        <thead>
          <tr>
            <th className="label">{rep.title}</th>
            {cols.map((c, i) => (
              <th key={i} colSpan={showPct ? 2 : 1} className={c.code === "__TOTAL__" || c.plant === "__ALL__" ? "total" : ""}
                style={{ textAlign: "center" }}>
                {rep.report === "plant_wise" || rep.report === "pg_totals" ? (c.plant === "__ALL__" ? "Company" : c.label) : c.label}
              </th>
            ))}
          </tr>
          <tr>
            <th className="label" style={{ fontWeight: 400 }}>{UNIT_LABEL[rep.unit]}</th>
            {cols.map((c, i) => (
              showPct ? [<th key={`${i}v`}>{c.family}</th>, <th key={`${i}p`}>%</th>] : <th key={i}>{c.family}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rep.rows.map((r, ri) => {
            if (r.kind === "blank") return <tr key={ri} className="blank"><td className="label" colSpan={1 + cols.length * (showPct ? 2 : 1)} /></tr>;
            if (r.kind === "header") return <tr key={ri} className="header"><td className="label">{r.label}</td>{cols.map((_, i) => showPct ? [<td key={`${i}a`} />, <td key={`${i}b`} />] : <td key={i} />)}</tr>;
            const cls = [r.style?.bold ? "bold" : "", r.style?.fill ? "filled" : ""].join(" ");
            return (
              <tr key={ri} className={cls}>
                <td className="label" title={r.label} style={{ paddingLeft: 8 + (r.style?.indent ?? 0) * 10, fontStyle: r.style?.italic ? "italic" : undefined }}>{r.label}</td>
                {cols.map((c, i) => {
                  const v = r.values?.[i] ?? 0;
                  const p = r.pct?.[i];
                  const cell = (
                    <td key={`${i}v`} className={`v ${v < 0 ? "neg" : ""} ${c.code === "__TOTAL__" || c.plant === "__ALL__" ? "total" : ""}`}
                      onClick={() => !c.memo && c.plant !== "__ALL__" && !r.ratio && onDrill(r, i)} title={c.memo ? "Corporate costs of this plant before pooling (memo)" : "Click to drill down"}>
                      {fmt(v, decimals, numberFormat)}
                    </td>
                  );
                  return showPct ? [cell, <td key={`${i}p`} className="p">{p !== null && p !== undefined ? pct(p) : ""}</td>] : cell;
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

interface McRow { plant: string; pg: string; sales: number; mc_tb: number; mc_adjustment: number; mc_worked_out: number; mc_pct_tb: number | null; mc_pct: number | null }
interface MisRow { label: string; mis?: number; pnl?: number; difference?: number; ok?: boolean; error?: string; expr?: string }

export default function Reports() {
  const { companyId, period, unit, company } = useApp();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>((params.get("tab") as Tab) || "plant_wise");
  const [plant, setPlant] = useState<string>("");
  const [pg, setPg] = useState<string>("");
  const [showPct, setShowPct] = useState(true);
  const [drill, setDrill] = useState<{ line: string; plant: string; code: string } | null>(null);
  const runs = useQuery({
    queryKey: ["runs", companyId],
    queryFn: () => api.get<Run[]>(`/api/companies/${companyId}/runs`),
    enabled: companyId !== null,
  });
  const periodRuns = useMemo(() => (runs.data ?? []).filter((r) => r.period_id === period?.id && r.status !== "failed"), [runs.data, period]);
  const paramRun = params.get("run");
  const [runId, setRunId] = useState<number | null>(paramRun ? Number(paramRun) : null);
  useEffect(() => {
    if (runId && (runs.data ?? []).some((r) => r.id === runId && r.period_id === period?.id)) return;
    const pub = periodRuns.find((r) => r.status === "published") ?? periodRuns[0];
    setRunId(pub ? pub.id : null);
  }, [periodRuns]); // eslint-disable-line react-hooks/exhaustive-deps
  const runInfo = useQuery({ queryKey: ["run", runId], queryFn: () => api.get<Run>(`/api/runs/${runId}`), enabled: !!runId });
  const plants = runInfo.data?.plants ?? [];
  const pgs = runInfo.data?.product_groups ?? [];
  useEffect(() => { if (plants.length && !plants.includes(plant)) setPlant(plants[0]); }, [plants]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (pgs.length && !pgs.includes(pg)) setPg(pgs[0]); }, [pgs]); // eslint-disable-line react-hooks/exhaustive-deps
  const rep = useQuery({
    queryKey: ["report", runId, tab, plant, pg, unit],
    queryFn: () => api.get<Report & { rows: unknown[] }>(`/api/runs/${runId}/reports/${tab}${qs({ plant: tab === "pg_wise" ? plant : undefined, pg: tab === "pg_totals" ? pg : undefined, unit })}`),
    enabled: !!runId && (tab !== "pg_wise" || !!plant) && (tab !== "pg_totals" || !!pg),
  });
  const nf = company?.settings.number_format ?? "indian";
  const dec = company?.settings.decimals ?? 2;
  const changeTab = (t: Tab) => {
    setTab(t);
    params.set("tab", t);
    setParams(params, { replace: true });
  };
  const exportName = tab === "summary" ? "pack" : tab;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Reports</h1>
          <p>Values come from the selected calculation run. Click any amount to drill down to its categories, allocations and source GL × business-area / cost-centre rows.</p>
        </div>
        <div className="row">
          <select aria-label="Run" value={runId ?? ""} onChange={(e) => setRunId(Number(e.target.value))}>
            {periodRuns.map((r) => <option key={r.id} value={r.id}>Run #{r.id} – {r.status}</option>)}
          </select>
          {runId ? (
            <>
              {["plant_wise", "pg_wise", "all_pg", "pg_totals", "mc", "mis"].includes(tab) ? (
                <button onClick={() => api.download(`/api/runs/${runId}/export/${exportName}.xlsx${qs({ plant: tab === "pg_wise" ? plant : undefined, pg: tab === "pg_totals" ? pg : undefined, unit })}`)}>⬇ Excel</button>
              ) : null}
              <button onClick={() => api.download(`/api/runs/${runId}/export/pack.xlsx${qs({ unit })}`)}>⬇ Full pack (Excel)</button>
              {["plant_wise", "pg_wise", "all_pg", "pg_totals"].includes(tab) ? (
                <button onClick={() => api.download(`/api/runs/${runId}/export/${tab}.pdf${qs({ plant: tab === "pg_wise" ? plant : undefined, pg: tab === "pg_totals" ? pg : undefined, unit })}`)}>⬇ PDF</button>
              ) : null}
            </>
          ) : null}
        </div>
      </div>
      {!runId ? (
        <div className="card empty">No calculation for {period?.label ?? "this period"} yet – run one on the Calculations page.</div>
      ) : (
        <div className="card flush">
          <div style={{ padding: "8px 12px 0" }}>
            <Tabs value={tab} onChange={changeTab} items={TABS} />
          </div>
          <div className="row" style={{ padding: "10px 12px" }}>
            {runInfo.data ? <Badge status={runInfo.data.status} /> : null}
            {tab === "pg_wise" ? (
              <select aria-label="Plant" value={plant} onChange={(e) => setPlant(e.target.value)}>{plants.map((p) => <option key={p}>{p}</option>)}</select>
            ) : null}
            {tab === "pg_totals" ? (
              <select aria-label="Product group" value={pg} onChange={(e) => setPg(e.target.value)}>{pgs.map((p) => <option key={p}>{p}</option>)}</select>
            ) : null}
            {["plant_wise", "pg_wise", "all_pg", "pg_totals"].includes(tab) ? (
              <label className="row small"><input type="checkbox" checked={showPct} onChange={(e) => setShowPct(e.target.checked)} /> show % of sales</label>
            ) : null}
            <span className="right muted small">{UNIT_LABEL[unit]}</span>
          </div>
          {rep.isLoading ? <Loading /> : <ErrorBox error={rep.error} />}
          {rep.data && ["plant_wise", "pg_wise", "all_pg", "pg_totals"].includes(tab) ? (
            <PnlTable rep={rep.data as Report} showPct={showPct} numberFormat={nf} decimals={dec}
              onDrill={(row, ci) => {
                const c = (rep.data as Report).columns[ci];
                if (row.line) setDrill({ line: row.line, plant: c.plant, code: c.code });
              }} />
          ) : null}
          {rep.data && tab === "mc" ? (
            <div className="table-wrap" style={{ maxHeight: "calc(100vh - 260px)" }}>
              <table className="data">
                <thead><tr><th>Plant</th><th>Product group</th><th className="num">Sales</th><th className="num">MC per TB</th><th className="num">MC adjustment</th><th className="num">MC worked out</th><th className="num">MC % (TB)</th><th className="num">MC % (worked out)</th></tr></thead>
                <tbody>
                  {((rep.data as unknown as { rows: McRow[] }).rows).map((r, i) => (
                    <tr key={i} style={r.pg === "__TOTAL__" ? { fontWeight: 650, background: "var(--surface-2)" } : undefined}>
                      <td>{r.plant === "__ALL__" ? "Company" : r.plant}</td><td>{r.pg === "__TOTAL__" ? "Total" : r.pg}</td>
                      <td className="num"><Num v={r.sales} /></td><td className="num"><Num v={r.mc_tb} /></td>
                      <td className="num"><Num v={r.mc_adjustment} /></td><td className="num"><Num v={r.mc_worked_out} /></td>
                      <td className="num">{pct(r.mc_pct_tb)}</td><td className="num"><strong>{pct(r.mc_pct)}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {rep.data && tab === "mis" ? (
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Particulars</th><th className="num">MIS</th><th className="num">Product costing</th><th className="num">Difference</th><th>Status</th></tr></thead>
                <tbody>
                  {((rep.data as unknown as { rows: MisRow[] }).rows).map((r, i) => (
                    <tr key={i} style={!r.ok ? { background: "var(--bad-bg)" } : undefined}>
                      <td title={r.expr}>{r.label}</td><td className="num"><Num v={r.mis} d={3} /></td><td className="num"><Num v={r.pnl} d={3} /></td>
                      <td className="num"><strong><Num v={r.difference} d={3} /></strong></td>
                      <td>{r.error ? <Badge status="error">{r.error}</Badge> : r.ok ? <Badge status="ok">matches</Badge> : <Badge status="error">difference</Badge>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!((rep.data as unknown as { rows: MisRow[] }).rows).length ? <div className="empty">No MIS figures for this period – upload them on the Periods page.</div> : null}
            </div>
          ) : null}
          {rep.data && tab === "summary" ? <Summary data={rep.data as unknown as SummaryData} unit={unit} /> : null}
        </div>
      )}
      {drill && runId ? <DrillDown runId={runId} {...drill} unit={unit} onClose={() => setDrill(null)} /> : null}
    </>
  );
}

interface SummaryData { plants: ({ plant: string } & Record<string, number | null>)[]; product_groups: ({ pg: string } & Record<string, number | null>)[]; period: { label: string; months: number } }

function Summary({ data, unit }: { data: SummaryData; unit: string }) {
  const cols: [string, string, boolean][] = [["sales", "Sales", false], ["material_cost_pct", "MC %", true], ["gross_margin", "Gross margin", false],
    ["gross_margin_pct", "GM %", true], ["plant_profit", "Plant profit", false], ["pbt", "PBT", false], ["pbt_pct", "PBT %", true], ["bep_per_month", "BEP / month", false]];
  const table = (rows: (Record<string, number | null | string>)[], key: string, title: string) => (
    <div className="stack" style={{ padding: 12 }}>
      <h3>{title}</h3>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th>{key === "plant" ? "Plant" : "Product group"}</th>{cols.filter(([k]) => rows.some((r) => r[k] !== undefined)).map(([k, l]) => <th key={k} className="num">{l}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} style={r[key] === "__ALL__" ? { fontWeight: 650 } : undefined}>
                <td>{r[key] === "__ALL__" ? "Company" : String(r[key])}</td>
                {cols.filter(([k]) => rows.some((x) => x[k] !== undefined)).map(([k, , isPct]) => (
                  <td key={k} className="num">{isPct ? pct(r[k] as number | null) : <Num v={r[k] as number | null} />}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
  return (
    <div>
      <div className="muted small" style={{ padding: "0 12px" }}>Management / presentation format – {data.period.label} ({data.period.months} months) · {UNIT_LABEL[unit]}</div>
      {table(data.plants, "plant", "By plant")}
      {table(data.product_groups, "pg", "By product group (company)")}
    </div>
  );
}
