import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, qs } from "../api";
import { useApp } from "../context";
import { fmt, pct, UNIT_LABEL } from "../format";
import type { Kpis, Run } from "../types";
import { Badge, ErrorBox, Loading, Num, Seg, Tabs } from "../components/ui";
import { Icon } from "../components/icons";

interface CmpRow { line: string; label: string; style: { bold?: boolean; fill?: string | null }; a: number; b: number; variance: number; variance_pct: number | null }
interface CmpSide { run_id: number; period: string; scenario: string; label: string }
interface Cmp { unit: string; level: string; plant: string; pg: string; a: CmpSide; b: CmpSide; rows: CmpRow[] }
interface McCmp {
  unit: string;
  periods: { id: number; code: string; label: string; run_id: number; run_status: string }[];
  rows: { plant: string; pg: string; by_period: Record<string, { sales: number; mc: number; mc_pct: number | null }> }[];
}
interface Summary { plants: ({ plant: string } & Kpis)[]; product_groups: ({ pg: string } & Kpis)[]; period: { label: string; months: number } }

type Level = "company" | "plant" | "pg";
const UNIT_SHORT: Record<string, string> = { rupees: "", thousand: "'000", lakh: "lakh", million: "mn", crore: "cr" };
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const runName = (r: Run) => `${r.period_label || r.period_code}${r.scenario !== "actual" ? ` · ${r.scenario}` : ""}`;
/** lines where an increase is good news (income and profit); everything else is a cost */
const HIGHER_IS_BETTER = /sales|margin|profit|pbt|income|credit|contribution|recover|benefit|revenue/i;

function Delta({ a, b, higherIsBetter = true, unit, kind = "amount" }: { a?: number; b?: number; higherIsBetter?: boolean; unit: string; kind?: "amount" | "pp" }) {
  if (a === undefined || b === undefined) return <span className="delta muted">–</span>;
  const d = a - b;
  if (kind === "pp") {
    const pp = d * 100;
    if (Math.abs(pp) < 0.05) return <span className="delta flat">no change</span>;
    const good = higherIsBetter ? pp > 0 : pp < 0;
    return <span className={`delta ${good ? "good" : "bad"}`}>{pp > 0 ? "▲" : "▼"} {fmt(Math.abs(pp), 1)} pp</span>;
  }
  if (Math.abs(d) < 0.005) return <span className="delta flat">no change</span>;
  const good = higherIsBetter ? d > 0 : d < 0;
  const rel = b ? d / Math.abs(b) : null;
  return (
    <span className={`delta ${good ? "good" : "bad"}`}>
      {d > 0 ? "▲" : "▼"} ₹{fmt(Math.abs(d))}{UNIT_SHORT[unit] ? ` ${UNIT_SHORT[unit]}` : ""}{rel !== null && Math.abs(rel) < 100 ? <small> {d > 0 ? "+" : "−"}{fmt(Math.abs(rel * 100), 1)}%</small> : null}
    </span>
  );
}

const TILES: { key: string; label: string; higherIsBetter: boolean; pctKey?: string }[] = [
  { key: "sales", label: "Sales", higherIsBetter: true },
  { key: "material_cost", label: "Material cost", higherIsBetter: false, pctKey: "material_cost_pct" },
  { key: "gross_margin", label: "Gross margin", higherIsBetter: true, pctKey: "gross_margin_pct" },
  { key: "plant_profit", label: "Plant profit", higherIsBetter: true, pctKey: "plant_profit_pct" },
  { key: "pbt", label: "Profit before tax", higherIsBetter: true, pctKey: "pbt_pct" },
];

const WALK: { key: string; label: string; sign: 1 | -1 }[] = [
  { key: "sales", label: "Sales", sign: 1 },
  { key: "material_cost", label: "Material cost", sign: -1 },
  { key: "variable_cost", label: "Variable cost", sign: -1 },
  { key: "factory_overhead", label: "Factory overhead", sign: -1 },
  { key: "pre_factory", label: "People cost – factory", sign: -1 },
  { key: "corporate_overheads", label: "Corporate overhead", sign: -1 },
  { key: "pre_corporate", label: "People cost – corporate", sign: -1 },
  { key: "other_credits", label: "Other income / (expense)", sign: 1 },
];

/** What moved profit before tax from B to A: each driver's effect on PBT. */
function PbtWalk({ a, b, unit, nameA, nameB }: { a: Kpis; b: Kpis; unit: string; nameA: string; nameB: string }) {
  const pa = num(a.pbt), pb = num(b.pbt);
  if (pa === undefined || pb === undefined) return null;
  const steps = WALK.filter((w) => num(a[w.key]) !== undefined && num(b[w.key]) !== undefined)
    .map((w) => ({ label: w.label, v: ((num(a[w.key]) as number) - (num(b[w.key]) as number)) * w.sign }));
  const rest = pa - pb - steps.reduce((t, s) => t + s.v, 0);
  if (Math.abs(rest) > 0.005) steps.push({ label: "Other items", v: rest });
  const maxAbs = Math.max(...steps.map((s) => Math.abs(s.v)), Math.abs(pa - pb), 1e-9);
  const sorted = [...steps].sort((x, y) => Math.abs(y.v) - Math.abs(x.v));
  return (
    <div className="walk">
      <div className="walk-ends">
        <div><span>PBT · {nameB}</span><strong className="num-font">₹{fmt(pb)}</strong></div>
        <Icon name="trend" size={20} />
        <div><span>PBT · {nameA}</span><strong className="num-font">₹{fmt(pa)}</strong></div>
        <div className={`walk-total ${pa - pb >= 0 ? "good" : "bad"}`}><span>Change</span><strong className="num-font">{pa - pb >= 0 ? "+" : "−"}₹{fmt(Math.abs(pa - pb))}</strong></div>
      </div>
      <div className="walk-rows">
        {sorted.map((s) => (
          <div key={s.label} className="walk-row">
            <span className="w-label">{s.label}</span>
            <span className="w-track" aria-hidden>
              <i className="mid" />
              <i className={`bar ${s.v >= 0 ? "good" : "bad"}`} style={s.v >= 0 ? { left: "50%", width: `${(Math.abs(s.v) / maxAbs) * 50}%` } : { right: "50%", width: `${(Math.abs(s.v) / maxAbs) * 50}%` }} />
            </span>
            <span className={`w-value num-font ${Math.abs(s.v) < 0.005 ? "" : s.v > 0 ? "pos" : "neg"}`}>{Math.abs(s.v) < 0.005 ? "–" : `${s.v > 0 ? "+" : "−"}${fmt(Math.abs(s.v))}`}</span>
          </div>
        ))}
      </div>
      <div className="muted small">Effect on profit before tax · {UNIT_LABEL[unit]} · biggest first · green raised profit, red lowered it</div>
    </div>
  );
}

type MoverSort = "pbt" | "sales" | "margin";

function Movers({ title, rows, unit, nameKey }: {
  title: string; unit: string; nameKey: string;
  rows: { name: string; a?: Kpis; b?: Kpis }[];
}) {
  const [sort, setSort] = useState<MoverSort>("pbt");
  const d = (r: { a?: Kpis; b?: Kpis }, k: string) => (num(r.a?.[k]) ?? 0) - (num(r.b?.[k]) ?? 0);
  const sorted = [...rows].sort((x, y) => sort === "margin"
    ? Math.abs(d(y, "pbt_pct")) - Math.abs(d(x, "pbt_pct"))
    : Math.abs(d(y, sort)) - Math.abs(d(x, sort)));
  const maxAbs = Math.max(...rows.map((r) => Math.abs(d(r, "pbt"))), 1e-9);
  return (
    <div className="card flush">
      <div className="card-head" style={{ padding: "14px 18px 6px" }}>
        <h2>{title}</h2>
        <Seg value={sort} onChange={setSort} items={[{ key: "pbt", label: "Δ PBT" }, { key: "sales", label: "Δ Sales" }, { key: "margin", label: "Δ PBT %" }]} />
      </div>
      <div className="table-wrap">
        <table className="data cmp-table">
          <thead>
            <tr>
              <th>{nameKey}</th>
              <th className="num">Sales A</th><th className="num">Sales B</th><th className="num">Δ Sales</th>
              <th className="num">PBT A</th><th className="num">PBT B</th><th className="num">Δ PBT</th>
              <th className="num">PBT % A</th><th className="num">PBT % B</th><th className="num">Δ</th>
              <th className="bar-col" aria-label="Δ PBT" />
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => {
              const dp = d(r, "pbt");
              const w = (Math.abs(dp) / maxAbs) * 50;
              const meaningful = Math.abs(num(r.a?.sales) ?? 0) > 0.005 && Math.abs(num(r.b?.sales) ?? 0) > 0.005;
              return (
                <tr key={r.name}>
                  <td><strong>{r.name}</strong>{!r.b ? <span className="flag info">new</span> : !r.a ? <span className="flag info">gone</span> : null}</td>
                  <td className="num">{fmt(num(r.a?.sales))}</td><td className="num">{fmt(num(r.b?.sales))}</td>
                  <td className="num"><Delta a={num(r.a?.sales)} b={num(r.b?.sales)} unit="" /></td>
                  <td className="num">{fmt(num(r.a?.pbt))}</td><td className="num">{fmt(num(r.b?.pbt))}</td>
                  <td className="num"><Delta a={num(r.a?.pbt) ?? 0} b={num(r.b?.pbt) ?? 0} unit="" /></td>
                  <td className="num">{meaningful ? pct(num(r.a?.pbt_pct)) : "n/m"}</td><td className="num">{meaningful ? pct(num(r.b?.pbt_pct)) : "n/m"}</td>
                  <td className="num">{meaningful ? <Delta a={num(r.a?.pbt_pct)} b={num(r.b?.pbt_pct)} unit="" kind="pp" /> : "–"}</td>
                  <td className="bar-col" aria-hidden><span className="pbt-bar"><i className={dp < 0 ? "neg" : ""} style={dp < 0 ? { right: "50%", width: `${w}%` } : { left: "50%", width: `${w}%` }} /></span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="muted small" style={{ padding: "8px 18px 14px" }}>{UNIT_LABEL[unit]} · A minus B · n/m = no sales</div>
    </div>
  );
}

function Overview({ a, b, unit, nameA, nameB, scope }: { a: Summary; b: Summary; unit: string; nameA: string; nameB: string; scope: string }) {
  const ka = a.plants.find((p) => p.plant === scope);
  const kb = b.plants.find((p) => p.plant === scope);
  if (!ka || !kb) return <div className="empty">No figures for {scope} in one of the runs.</div>;
  const plantNames = [...new Set([...a.plants, ...b.plants].map((p) => p.plant))].filter((p) => p !== "__ALL__");
  const plants = plantNames.map((n) => ({ name: n, a: a.plants.find((p) => p.plant === n), b: b.plants.find((p) => p.plant === n) }))
    .filter((r) => Math.abs(num(r.a?.sales) ?? 0) + Math.abs(num(r.b?.sales) ?? 0) + Math.abs(num(r.a?.pbt) ?? 0) + Math.abs(num(r.b?.pbt) ?? 0) > 0.005);
  const pgNames = [...new Set([...a.product_groups, ...b.product_groups].map((p) => p.pg))];
  const pgs = pgNames.map((n) => ({ name: n, a: a.product_groups.find((p) => p.pg === n), b: b.product_groups.find((p) => p.pg === n) }))
    .filter((r) => Math.abs(num(r.a?.sales) ?? 0) + Math.abs(num(r.b?.sales) ?? 0) + Math.abs(num(r.a?.pbt) ?? 0) + Math.abs(num(r.b?.pbt) ?? 0) > 0.005);
  return (
    <>
      <section className="cmp-tiles">
        {TILES.map((t) => (
          <div key={t.key} className="cmp-tile">
            <span className="label">{t.label}</span>
            <span className="value num-font">₹{fmt(num(ka[t.key]))}<small>{UNIT_SHORT[unit]}</small></span>
            <span className="vs">vs ₹{fmt(num(kb[t.key]))} in {nameB}</span>
            <Delta a={num(ka[t.key])} b={num(kb[t.key])} higherIsBetter={t.higherIsBetter} unit={unit} />
            {t.pctKey ? (
              <span className="pp">{pct(num(ka[t.pctKey]))} of sales · <Delta a={num(ka[t.pctKey])} b={num(kb[t.pctKey])} higherIsBetter={t.higherIsBetter} unit={unit} kind="pp" /></span>
            ) : <span className="pp">&nbsp;</span>}
          </div>
        ))}
      </section>
      <div className="card">
        <div className="card-head"><h2>What moved profit before tax</h2><span className="muted small">{scope === "__ALL__" ? "Company" : scope}</span></div>
        <PbtWalk a={ka} b={kb} unit={unit} nameA={nameA} nameB={nameB} />
      </div>
      {scope === "__ALL__" ? <Movers title="Plants" nameKey="Plant" rows={plants} unit={unit} /> : null}
      <Movers title={scope === "__ALL__" ? "Product groups" : "Product groups (all plants)"} nameKey="Product group" rows={pgs} unit={unit} />
    </>
  );
}

function LineByLine({ a, b }: { a: number; b: number }) {
  const { unit, company } = useApp();
  const info = useQuery({ queryKey: ["run", a], queryFn: () => api.get<Run>(`/api/runs/${a}`), enabled: !!a });
  const [level, setLevel] = useState<Level>("company");
  const [plant, setPlant] = useState("");
  const [pg, setPg] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(true);
  const [search, setSearch] = useState("");
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
  const rows = (q.data?.rows ?? []).filter((r) => (!onlyChanged || Math.abs(r.variance) >= 0.005 || r.style?.bold)
    && (!search || r.label.toLowerCase().includes(search.toLowerCase())));
  const csv = () => {
    if (!q.data) return;
    const head = ["Particulars", q.data.a.label || q.data.a.period, q.data.b.label || q.data.b.period, "Variance", "Variance %"];
    const body = q.data.rows.map((r) => [r.label, r.a, r.b, r.variance, r.variance_pct ?? ""].map((x) => `"${String(x).replace(/"/g, '""')}"`).join(","));
    const blob = new Blob([[head.join(","), ...body].join("\n")], { type: "text/csv" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `comparison_${q.data.a.period}_vs_${q.data.b.period}.csv`;
    link.click();
  };
  return (
    <div className="stack" style={{ padding: 16 }}>
      <div className="row">
        <Seg value={level} onChange={setLevel} items={[{ key: "company", label: "Company" }, { key: "plant", label: "Plant" }, { key: "pg", label: "Plant × PG" }]} />
        {level !== "company" ? <select aria-label="Plant" value={plant} onChange={(e) => setPlant(e.target.value)}>{plants.map((p) => <option key={p}>{p}</option>)}</select> : null}
        {level === "pg" ? <select aria-label="Product group" value={pg} onChange={(e) => setPg(e.target.value)}>{pgs.map((p) => <option key={p}>{p}</option>)}</select> : null}
        <input placeholder="Search a line" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 200 }} />
        <label className="row small"><input type="checkbox" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)} /> Only lines that changed</label>
        <span className="grow" />
        <button className="small" onClick={csv} disabled={!q.data}><Icon name="download" size={14} />CSV</button>
      </div>
      {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
      {q.data ? (
        <div className="table-wrap" style={{ maxHeight: "calc(100vh - 330px)" }}>
          <table className="data cmp-lines">
            <thead>
              <tr>
                <th>Particulars</th>
                <th className="num">A · {q.data.a.label || q.data.a.period}</th>
                <th className="num">B · {q.data.b.label || q.data.b.period}</th>
                <th className="num">Variance</th>
                <th className="num">Variance %</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const better = HIGHER_IS_BETTER.test(r.label) ? r.variance > 0 : r.variance < 0;
                const tone = Math.abs(r.variance) < 0.005 ? "" : better ? "good" : "bad";
                return (
                  <tr key={i} className={r.style?.bold ? "bold" : ""}>
                    <td>{r.label}</td>
                    <td className="num"><Num v={r.a} style={nf} /></td>
                    <td className="num"><Num v={r.b} style={nf} /></td>
                    <td className={`num var ${tone}`}>{Math.abs(r.variance) < 0.005 ? "–" : <>{r.variance > 0 ? "▲" : "▼"} {fmt(Math.abs(r.variance))}</>}</td>
                    <td className={`num var ${tone}`}>{r.variance_pct === null || Math.abs(r.variance_pct) > 99 ? "–" : pct(r.variance_pct)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!rows.length ? <div className="empty">No lines changed.</div> : null}
        </div>
      ) : null}
      <div className="muted small">{UNIT_LABEL[unit]} · green = better for profit, red = worse</div>
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
    <div className="stack" style={{ padding: 16 }}>
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
  const { companyId, period, unit } = useApp();
  const [tab, setTab] = useState<"overview" | "lines" | "mc">("overview");
  const runs = useQuery({
    queryKey: ["runs", companyId],
    queryFn: () => api.get<Run[]>(`/api/companies/${companyId}/runs`),
    enabled: companyId !== null,
  });
  // latest successful run per period, newest period first
  const latest = useMemo(() => {
    const seen = new Set<number>();
    return (runs.data ?? []).filter((r) => r.status !== "failed").filter((r) => (seen.has(r.period_id) ? false : (seen.add(r.period_id), true)));
  }, [runs.data]);
  const [a, setA] = useState<number | null>(null);
  const [b, setB] = useState<number | null>(null);
  const [scope, setScope] = useState("__ALL__");

  const runA = latest.find((r) => r.id === a);
  const presets = useMemo(() => {
    if (!runA) return [] as { key: string; label: string; run: Run }[];
    const others = latest.filter((r) => r.id !== runA.id);
    const out: { key: string; label: string; run: Run }[] = [];
    const budget = others.find((r) => r.period_code === runA.period_code && r.scenario !== "actual");
    if (budget) out.push({ key: "budget", label: `vs ${budget.scenario}`, run: budget });
    const prev = others.filter((r) => r.scenario === "actual" && r.period_code < runA.period_code).sort((x, y) => y.period_code.localeCompare(x.period_code))[0];
    if (prev) out.push({ key: "prev", label: "vs previous period", run: prev });
    const ly = String(Number(runA.period_code.slice(0, 4)) - 1) + runA.period_code.slice(4);
    const lastYear = others.find((r) => r.scenario === "actual" && r.period_code === ly);
    if (lastYear) out.push({ key: "ly", label: "vs same period last year", run: lastYear });
    return out;
  }, [latest, runA]);

  useEffect(() => {
    if (!latest.length) return;
    const ra = latest.find((r) => r.period_id === period?.id && r.scenario === "actual") ?? latest.find((r) => r.scenario === "actual") ?? latest[0];
    setA(ra.id);
  }, [latest, period?.id]);
  useEffect(() => {
    if (!runA) return;
    if (b && b !== runA.id && latest.some((r) => r.id === b)) return;
    setB((presets[0]?.run ?? latest.find((r) => r.id !== runA.id))?.id ?? null);
  }, [runA, presets]); // eslint-disable-line react-hooks/exhaustive-deps

  const sa = useQuery({ queryKey: ["report", a, "summary", unit], queryFn: () => api.get<Summary>(`/api/runs/${a}/reports/summary${qs({ unit })}`), enabled: !!a });
  const sb = useQuery({ queryKey: ["report", b, "summary", unit], queryFn: () => api.get<Summary>(`/api/runs/${b}/reports/summary${qs({ unit })}`), enabled: !!b });
  const runB = latest.find((r) => r.id === b);
  const plantOptions = (sa.data?.plants ?? []).map((p) => p.plant);

  if (runs.isLoading) return <Loading />;
  if (latest.length < 2) {
    return (
      <>
        <div className="page-head"><h1>Compare</h1></div>
        <div className="card empty">
          <Icon name="compare" size={28} />
          <p><strong>Nothing to compare yet.</strong></p>
          <p className="muted">Calculate a second period, or a budget, and it will appear here.</p>
        </div>
      </>
    );
  }
  return (
    <>
      <div className="page-head"><h1>Compare</h1></div>
      <section className="card cmp-pick">
        <div className="pick">
          <span className="tag a">A</span>
          <select value={a ?? ""} onChange={(e) => setA(Number(e.target.value))} aria-label="Run A">
            {latest.map((r) => <option key={r.id} value={r.id}>{runName(r)}</option>)}
          </select>
        </div>
        <button className="icon-btn swap" onClick={() => { setA(b); setB(a); }} title="Swap A and B" aria-label="Swap A and B"><Icon name="compare" size={18} /></button>
        <div className="pick">
          <span className="tag b">B</span>
          <select value={b ?? ""} onChange={(e) => setB(Number(e.target.value))} aria-label="Run B">
            {latest.filter((r) => r.id !== a).map((r) => <option key={r.id} value={r.id}>{runName(r)}</option>)}
          </select>
        </div>
        <div className="presets">
          {presets.map((p) => (
            <button key={p.key} className={`chip ${b === p.run.id ? "on" : ""}`} onClick={() => setB(p.run.id)}>{p.label}</button>
          ))}
        </div>
        <span className="grow" />
        <label className="filter">
          <span>Scope</span>
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            {plantOptions.map((p) => <option key={p} value={p}>{p === "__ALL__" ? "Company" : p}</option>)}
          </select>
        </label>
      </section>

      <div className="card flush">
        <div style={{ padding: "8px 12px 0" }}>
          <Tabs value={tab} onChange={setTab} items={[{ key: "overview", label: "Overview" }, { key: "lines", label: "Line by line" }, { key: "mc", label: "MC % trend" }]} />
        </div>
        {tab === "overview" ? (
          <div className="stack" style={{ padding: 16, gap: 16 }}>
            {sa.error || sb.error ? <ErrorBox error={sa.error ?? sb.error} /> : null}
            {sa.data && sb.data && runA && runB ? (
              <Overview a={sa.data} b={sb.data} unit={unit} nameA={runName(runA)} nameB={runName(runB)} scope={scope} />
            ) : <Loading />}
          </div>
        ) : null}
        {tab === "lines" && a && b ? <LineByLine a={a} b={b} /> : null}
        {tab === "mc" ? <McCompare /> : null}
      </div>
      {runA && runB ? <div className="muted small">A = {runName(runA)} (run #{runA.id} <Badge status={runA.status} />) · B = {runName(runB)} (run #{runB.id} <Badge status={runB.status} />)</div> : null}
    </>
  );
}
