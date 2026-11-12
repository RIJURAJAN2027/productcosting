import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useApp, useAuth, useToast } from "../context";
import { dateTime, fmt, pct, UNIT_LABEL } from "../format";
import { Badge, ErrorBox, Field, Loading, Modal, Num, Tabs } from "../components/ui";

const UNITS: Record<string, number> = { rupees: 1, thousand: 1e3, lakh: 1e5, million: 1e6, crore: 1e7 };

type AdjType = "regroup_gl" | "move_dim" | "exclude_gl" | "amount" | "reclass";
const ADJ_LABEL: Record<AdjType, string> = {
  regroup_gl: "Regroup GL (move one GL's amounts to another GL)",
  move_dim: "Move GL to another business area / cost centre",
  exclude_gl: "Exclude GL",
  amount: "Fixed correction (add an amount)",
  reclass: "Reclassify an amount between BA/CC (and GL)",
};

interface Adjustment {
  id: number; period_id: number | null; type: AdjType; definition: Record<string, unknown>; reason: string;
  author_name: string | null; enabled: boolean; sort_order: number; created_at: string | null;
}

const list = (s: string) => s.split(/[,\s;]+/).map((x) => x.trim()).filter(Boolean);
const joined = (v: unknown) => (Array.isArray(v) ? v.join(", ") : v === undefined || v === null ? "" : String(v));

function describe(a: Adjustment, unitDiv: number, unit: string): string {
  const d = a.definition as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  const tb = d.tb && d.tb !== "BA" ? ` [${d.tb} TB]` : "";
  switch (a.type) {
    case "regroup_gl": return `GL ${joined(d.from_gls)} → GL ${d.to_gl}${tb}`;
    case "move_dim": return `GL ${d.gl}${d.from ? ` from ${joined(d.from)}` : ""} → ${d.to}${tb}`;
    case "exclude_gl": return `exclude GL ${joined(d.gls)}${d.dims ? ` in ${joined(d.dims)}` : ""}${tb}`;
    case "amount": return `GL ${d.gl} × ${d.dim}: ${fmt(Number(d.amount) / unitDiv, 4)} ${UNIT_LABEL[unit]}${tb}`;
    case "reclass": return `${fmt(Number(d.amount) / unitDiv, 4)} ${UNIT_LABEL[unit]} from GL ${d.from?.gl} × ${d.from?.dim} to GL ${d.to?.gl ?? d.from?.gl} × ${d.to?.dim}${tb}`;
    default: return JSON.stringify(d);
  }
}

function AdjustmentEditor({ adj, onClose }: { adj: Adjustment | null; onClose: () => void }) {
  const { companyId, period, unit } = useApp();
  const toast = useToast();
  const qc = useQueryClient();
  const d = (adj?.definition ?? {}) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  const div = UNITS[unit];
  const [type, setType] = useState<AdjType>(adj?.type ?? "move_dim");
  const [f, setF] = useState<Record<string, string>>({
    from_gls: joined(d.from_gls), to_gl: joined(d.to_gl), gl: joined(d.gl ?? d.from?.gl), from: joined(d.from && !d.from.gl ? d.from : ""),
    to: joined(typeof d.to === "string" ? d.to : ""), gls: joined(d.gls), dims: joined(d.dims), dim: joined(d.dim ?? d.from?.dim),
    to_dim: joined(d.to?.dim), to_gl2: joined(d.to?.gl), amount: d.amount !== undefined ? String(Number(d.amount) / div) : "", tb: d.tb ?? "BA",
  });
  const [reason, setReason] = useState(adj?.reason ?? "");
  const [standing, setStanding] = useState(adj ? adj.period_id === null : false);
  const [enabled, setEnabled] = useState(adj?.enabled ?? true);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const amount = () => Number(f.amount) * div;
  const definition = (): Record<string, unknown> => {
    const base = { tb: f.tb };
    switch (type) {
      case "regroup_gl": return { ...base, from_gls: list(f.from_gls), to_gl: f.to_gl.trim() };
      case "move_dim": return { ...base, gl: f.gl.trim(), to: f.to.trim(), ...(f.from.trim() ? { from: list(f.from) } : {}) };
      case "exclude_gl": return { ...base, gls: list(f.gls), ...(f.dims.trim() ? { dims: list(f.dims) } : {}) };
      case "amount": return { ...base, gl: f.gl.trim(), dim: f.dim.trim(), amount: amount() };
      case "reclass": return { ...base, amount: amount(), from: { gl: f.gl.trim(), dim: f.dim.trim() }, to: { ...(f.to_gl2.trim() ? { gl: f.to_gl2.trim() } : {}), dim: f.to_dim.trim() } };
    }
  };
  const save = async () => {
    const body = { type, definition: definition(), reason, period_id: standing ? null : period?.id, enabled, sort_order: adj?.sort_order ?? 0 };
    try {
      if (adj) await api.patch(`/api/companies/${companyId}/adjustments/${adj.id}`, body);
      else await api.post(`/api/companies/${companyId}/adjustments`, body);
      toast("Adjustment saved – recalculate to apply it");
      qc.invalidateQueries({ queryKey: ["adjustments", companyId] });
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const tbSelect = (
    <Field label="Trial balance">
      <select value={f.tb} onChange={set("tb")}><option value="BA">Business-area TB (Non-PRE)</option><option value="CC">Cost-centre TB (PRE)</option><option value="BOTH">Both</option></select>
    </Field>
  );
  return (
    <Modal title={adj ? `Edit adjustment #${adj.id}` : "New adjustment"} onClose={onClose}
      footer={<><button onClick={onClose}>Cancel</button><button className="primary" disabled={!reason.trim()} onClick={save}>Save</button></>}>
      <Field label="Type">
        <select value={type} onChange={(e) => setType(e.target.value as AdjType)}>
          {(Object.keys(ADJ_LABEL) as AdjType[]).map((t) => <option key={t} value={t}>{ADJ_LABEL[t]}</option>)}
        </select>
      </Field>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
        {type === "regroup_gl" ? <>
          <Field label="From GL(s)" hint="Comma-separated"><input value={f.from_gls} onChange={set("from_gls")} /></Field>
          <Field label="To GL"><input value={f.to_gl} onChange={set("to_gl")} /></Field>
        </> : null}
        {type === "move_dim" ? <>
          <Field label="GL"><input value={f.gl} onChange={set("gl")} /></Field>
          <Field label="Only from BA/CC" hint="Optional, comma-separated; blank = all"><input value={f.from} onChange={set("from")} /></Field>
          <Field label="To BA/CC"><input value={f.to} onChange={set("to")} /></Field>
        </> : null}
        {type === "exclude_gl" ? <>
          <Field label="GL(s)" hint="Comma-separated"><input value={f.gls} onChange={set("gls")} /></Field>
          <Field label="Only in BA/CC" hint="Optional; blank = everywhere"><input value={f.dims} onChange={set("dims")} /></Field>
        </> : null}
        {type === "amount" ? <>
          <Field label="GL"><input value={f.gl} onChange={set("gl")} /></Field>
          <Field label="BA/CC"><input value={f.dim} onChange={set("dim")} /></Field>
          <Field label={`Amount (${UNIT_LABEL[unit]})`} hint="TB sign: debit +, credit −"><input type="number" value={f.amount} onChange={set("amount")} /></Field>
        </> : null}
        {type === "reclass" ? <>
          <Field label="From GL"><input value={f.gl} onChange={set("gl")} /></Field>
          <Field label="From BA/CC"><input value={f.dim} onChange={set("dim")} /></Field>
          <Field label="To GL" hint="Blank = same GL"><input value={f.to_gl2} onChange={set("to_gl2")} /></Field>
          <Field label="To BA/CC"><input value={f.to_dim} onChange={set("to_dim")} /></Field>
          <Field label={`Amount (${UNIT_LABEL[unit]})`}><input type="number" value={f.amount} onChange={set("amount")} /></Field>
        </> : null}
        {tbSelect}
      </div>
      <Field label="Reason (required, kept in the audit trail)"><textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} style={{ fontFamily: "inherit" }} /></Field>
      <div className="row">
        <label className="row small"><input type="checkbox" checked={standing} onChange={(e) => setStanding(e.target.checked)} /> Standing rule (applies to every period)</label>
        <label className="row small"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled</label>
      </div>
      {!standing ? <div className="muted small">Applies to {period?.label || period?.code} only.</div> : null}
      {err ? <div style={{ color: "var(--bad)" }}>{err}</div> : null}
    </Modal>
  );
}

function AdjustmentRules({ locked }: { locked: boolean }) {
  const { companyId, period, unit } = useApp();
  const { can } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const [edit, setEdit] = useState<Adjustment | null | undefined>(undefined);
  const q = useQuery({
    queryKey: ["adjustments", companyId, period?.id],
    queryFn: () => api.get<Adjustment[]>(`/api/companies/${companyId}/adjustments?period_id=${period?.id}`),
    enabled: companyId !== null && !!period,
  });
  const editable = (a: Adjustment) => can("edit") && !(locked && a.period_id !== null);
  const toggle = async (a: Adjustment) => {
    try {
      await api.patch(`/api/companies/${companyId}/adjustments/${a.id}`, { type: a.type, definition: a.definition, reason: a.reason, period_id: a.period_id, enabled: !a.enabled, sort_order: a.sort_order });
      qc.invalidateQueries({ queryKey: ["adjustments", companyId] });
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const del = async (a: Adjustment) => {
    if (!confirm("Delete this adjustment?")) return;
    try {
      await api.del(`/api/companies/${companyId}/adjustments/${a.id}`);
      qc.invalidateQueries({ queryKey: ["adjustments", companyId] });
    } catch (e) { toast((e as Error).message, "error"); }
  };
  return (
    <div className="stack" style={{ padding: 12 }}>
      <div className="row">
        <div className="muted small">Adjustments are applied to the trial balances before tagging, in this order. Each needs a reason and is kept with its author in the audit trail; the original upload is never changed.</div>
        <span className="right" />
        {can("edit") && !locked ? <button className="primary" onClick={() => setEdit(null)}>+ New adjustment</button> : null}
      </div>
      {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th>#</th><th>Scope</th><th>Type</th><th>What</th><th>Reason</th><th>Author</th><th>Enabled</th><th /></tr></thead>
          <tbody>
            {(q.data ?? []).map((a) => (
              <tr key={a.id} style={!a.enabled ? { opacity: 0.55 } : undefined}>
                <td>{a.id}</td>
                <td>{a.period_id === null ? <Badge status="open">standing</Badge> : <Badge status="draft">this period</Badge>}</td>
                <td className="small">{a.type}</td>
                <td className="mono small">{describe(a, UNITS[unit], unit)}</td>
                <td className="small">{a.reason}</td>
                <td className="small">{a.author_name}<div className="muted">{dateTime(a.created_at)}</div></td>
                <td><input type="checkbox" aria-label="Enabled" checked={a.enabled} disabled={!editable(a)} onChange={() => toggle(a)} /></td>
                <td style={{ whiteSpace: "nowrap" }}>
                  {editable(a) ? <><button className="small" onClick={() => setEdit(a)}>Edit</button> <button className="small ghost" aria-label="Delete" onClick={() => del(a)}>✕</button></> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!q.isLoading && !(q.data ?? []).length ? <div className="empty">No adjustments for this period.</div> : null}
      </div>
      {edit !== undefined ? <AdjustmentEditor adj={edit} onClose={() => setEdit(undefined)} /> : null}
    </div>
  );
}

interface Mc { id: number; plant_code: string; pg_code: string; kind: "amount" | "mc_pct"; value: number; reason: string }

function useCodes() {
  const { companyId, period } = useApp();
  const on = period?.end_date;
  const plants = useQuery({ queryKey: ["master", companyId, "plants", on], queryFn: () => api.get<{ code: string }[]>(`/api/companies/${companyId}/masters/plants${on ? `?on=${on}` : ""}`), enabled: companyId !== null });
  const pgs = useQuery({ queryKey: ["master", companyId, "product-groups", on], queryFn: () => api.get<{ code: string }[]>(`/api/companies/${companyId}/masters/product-groups${on ? `?on=${on}` : ""}`), enabled: companyId !== null });
  return { plants: (plants.data ?? []).map((p) => p.code), pgs: (pgs.data ?? []).map((p) => p.code) };
}

function McAdjustments({ locked }: { locked: boolean }) {
  const { companyId, period, unit } = useApp();
  const { can } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const { plants, pgs } = useCodes();
  const [form, setForm] = useState({ plant_code: "", pg_code: "", kind: "amount", value: "", reason: "" });
  useEffect(() => { if (!form.plant_code && plants.length) setForm((f) => ({ ...f, plant_code: plants[0] })); }, [plants]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!form.pg_code && pgs.length) setForm((f) => ({ ...f, pg_code: pgs[0] })); }, [pgs]); // eslint-disable-line react-hooks/exhaustive-deps
  const q = useQuery({
    queryKey: ["mc", period?.id],
    queryFn: () => api.get<Mc[]>(`/api/companies/${companyId}/periods/${period?.id}/mc-adjustments`),
    enabled: !!period,
  });
  const add = async () => {
    const value = form.kind === "amount" ? Number(form.value) * UNITS[unit] : Number(form.value) / 100;
    try {
      await api.post(`/api/companies/${companyId}/periods/${period?.id}/mc-adjustments`, { ...form, value });
      setForm({ ...form, value: "", reason: "" });
      qc.invalidateQueries({ queryKey: ["mc", period?.id] });
      toast("MC adjustment added – recalculate to apply it");
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const del = async (m: Mc) => {
    try {
      await api.del(`/api/companies/${companyId}/periods/${period?.id}/mc-adjustments/${m.id}`);
      qc.invalidateQueries({ queryKey: ["mc", period?.id] });
    } catch (e) { toast((e as Error).message, "error"); }
  };
  return (
    <div className="stack" style={{ padding: 12 }}>
      <div className="muted small">Material cost per the TB can be corrected to the worked-out figure (e.g. from an SAP material-cost report or production data) per plant × product group, either as an amount added to material cost or as a target MC % of sales. The MC % report shows both the TB and the worked-out figures.</div>
      {can("edit") && !locked ? (
        <div className="row" style={{ alignItems: "flex-end" }}>
          <Field label="Plant"><select value={form.plant_code} onChange={(e) => setForm({ ...form, plant_code: e.target.value })}>{plants.map((p) => <option key={p}>{p}</option>)}</select></Field>
          <Field label="Product group"><select value={form.pg_code} onChange={(e) => setForm({ ...form, pg_code: e.target.value })}>{pgs.map((p) => <option key={p}>{p}</option>)}</select></Field>
          <Field label="Kind"><select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}><option value="amount">Amount added to MC</option><option value="mc_pct">Target MC % of sales</option></select></Field>
          <Field label={form.kind === "amount" ? `Amount (${UNIT_LABEL[unit]})` : "MC %"}><input type="number" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} style={{ width: 130 }} /></Field>
          <Field label="Reason"><input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} style={{ minWidth: 220 }} /></Field>
          <button className="primary" disabled={!form.value || !form.reason.trim()} onClick={add}>Add</button>
        </div>
      ) : null}
      {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
      <table className="data">
        <thead><tr><th>Plant</th><th>Product group</th><th>Kind</th><th className="num">Value</th><th>Reason</th><th /></tr></thead>
        <tbody>
          {(q.data ?? []).map((m) => (
            <tr key={m.id}>
              <td>{m.plant_code}</td><td>{m.pg_code}</td><td>{m.kind === "amount" ? "amount" : "target MC %"}</td>
              <td className="num">{m.kind === "amount" ? <><Num v={m.value / UNITS[unit]} d={4} /> <span className="muted small">{UNIT_LABEL[unit]}</span></> : pct(m.value, 2)}</td>
              <td className="small">{m.reason}</td>
              <td>{can("edit") && !locked ? <button className="small ghost" aria-label="Delete" onClick={() => del(m)}>✕</button> : null}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!q.isLoading && !(q.data ?? []).length ? <div className="empty">No material-cost adjustments for this period.</div> : null}
    </div>
  );
}

type DriverMap = Record<string, { standing: boolean; values: Record<string, number> }>;

function Drivers({ locked }: { locked: boolean }) {
  const { companyId, period } = useApp();
  const { can } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const { plants, pgs } = useCodes();
  const q = useQuery({
    queryKey: ["drivers", period?.id],
    queryFn: () => api.get<DriverMap>(`/api/companies/${companyId}/periods/${period?.id}/drivers`),
    enabled: !!period,
  });
  const names = Object.keys(q.data ?? {});
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [standing, setStanding] = useState(false);
  useEffect(() => { if (!name && names.length) setName(names[0]); }, [names.join()]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const d = q.data?.[name];
    setValues(Object.fromEntries(Object.entries(d?.values ?? {}).map(([k, v]) => [k, String(v)])));
    setStanding(d?.standing ?? false);
  }, [name, q.data]);
  const codes = useMemo(() => [...pgs, "COM"], [pgs]);
  const save = async () => {
    const vals: Record<string, number> = {};
    Object.entries(values).forEach(([k, v]) => { if (v !== "" && !Number.isNaN(Number(v))) vals[k] = Number(v); });
    try {
      await api.put(`/api/companies/${companyId}/periods/${period?.id}/drivers`, { driver: name.trim(), values: vals, standing });
      toast(`Driver ${name} saved`);
      qc.invalidateQueries({ queryKey: ["drivers", period?.id] });
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const editable = can("edit") && !locked;
  return (
    <div className="stack" style={{ padding: 12 }}>
      <div className="muted small">Drivers are quantities (machine hours, power units, headcount, production tonnes …) that allocation rules can use as a basis with <code>{'{"kind": "driver", "driver": "name"}'}</code>. Enter them per plant × product group.</div>
      <div className="row">
        <select value={names.includes(name) ? name : ""} onChange={(e) => setName(e.target.value)}>
          <option value="" disabled>{names.length ? "Choose driver" : "No drivers yet"}</option>
          {names.map((n) => <option key={n}>{n}</option>)}
        </select>
        {editable ? <input placeholder="or new driver name" value={names.includes(name) ? "" : name} onChange={(e) => setName(e.target.value)} /> : null}
        <label className="row small"><input type="checkbox" checked={standing} disabled={!editable} onChange={(e) => setStanding(e.target.checked)} /> standing (all periods)</label>
        <span className="right" />
        {editable ? <button className="primary" disabled={!name.trim()} onClick={save}>Save driver</button> : null}
      </div>
      {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
      {name ? (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Plant</th>{codes.map((c) => <th key={c} className="num">{c}</th>)}</tr></thead>
            <tbody>
              {plants.map((p) => (
                <tr key={p}>
                  <td>{p}</td>
                  {codes.map((c) => (
                    <td key={c} className="num" style={{ padding: 2 }}>
                      <input type="number" aria-label={`${p} ${c}`} value={values[`${p}|${c}`] ?? ""} readOnly={!editable}
                        onChange={(e) => setValues({ ...values, [`${p}|${c}`]: e.target.value })} style={{ width: 90, textAlign: "right" }} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

interface Mis { id: number; label: string; expr: string; mis_value: number; compare: string; sort_order: number }

function MisLines({ locked }: { locked: boolean }) {
  const { companyId, period, unit } = useApp();
  const { can } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const [form, setForm] = useState({ label: "", expr: "", mis_value: "", compare: "sum" });
  const q = useQuery({
    queryKey: ["mis", period?.id],
    queryFn: () => api.get<Mis[]>(`/api/companies/${companyId}/periods/${period?.id}/mis`),
    enabled: !!period,
  });
  const add = async () => {
    try {
      await api.post(`/api/companies/${companyId}/periods/${period?.id}/mis`, { ...form, mis_value: Number(form.mis_value) * UNITS[unit], sort_order: (q.data?.length ?? 0) + 1 });
      setForm({ label: "", expr: "", mis_value: "", compare: "sum" });
      qc.invalidateQueries({ queryKey: ["mis", period?.id] });
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const del = async (m: Mis) => {
    try {
      await api.del(`/api/companies/${companyId}/periods/${period?.id}/mis/${m.id}`);
      qc.invalidateQueries({ queryKey: ["mis", period?.id] });
    } catch (e) { toast((e as Error).message, "error"); }
  };
  return (
    <div className="stack" style={{ padding: 12 }}>
      <div className="muted small">MIS figures are reconciled with the product-costing P&L in the <em>MIS check</em> report. The formula picks the P&L lines to compare, e.g. <code>[L:sales_total]</code> or <code>[L:pbt]</code>. “sum” expects MIS + P&L = 0 (MIS kept in TB sign); “difference” expects MIS = P&L. You can also upload a two-column MIS sheet on the Periods page.</div>
      {can("edit") && !locked ? (
        <div className="row" style={{ alignItems: "flex-end" }}>
          <Field label="Particulars"><input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} /></Field>
          <Field label="P&L formula"><input className="mono" value={form.expr} onChange={(e) => setForm({ ...form, expr: e.target.value })} style={{ minWidth: 220 }} /></Field>
          <Field label={`MIS value (${UNIT_LABEL[unit]})`}><input type="number" value={form.mis_value} onChange={(e) => setForm({ ...form, mis_value: e.target.value })} style={{ width: 130 }} /></Field>
          <Field label="Compare"><select value={form.compare} onChange={(e) => setForm({ ...form, compare: e.target.value })}><option value="sum">sum (MIS + P&L = 0)</option><option value="difference">difference (MIS = P&L)</option></select></Field>
          <button className="primary" disabled={!form.label || !form.expr || form.mis_value === ""} onClick={add}>Add</button>
        </div>
      ) : null}
      {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
      <table className="data">
        <thead><tr><th>Particulars</th><th>P&L formula</th><th className="num">MIS value</th><th>Compare</th><th /></tr></thead>
        <tbody>
          {(q.data ?? []).map((m) => (
            <tr key={m.id}>
              <td>{m.label}</td><td className="mono small">{m.expr}</td>
              <td className="num"><Num v={m.mis_value / UNITS[unit]} d={4} /></td><td className="small">{m.compare}</td>
              <td>{can("edit") && !locked ? <button className="small ghost" aria-label="Delete" onClick={() => del(m)}>✕</button> : null}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!q.isLoading && !(q.data ?? []).length ? <div className="empty">No MIS figures for this period.</div> : null}
    </div>
  );
}

export default function Adjustments() {
  const { period } = useApp();
  const [tab, setTab] = useState<"rules" | "mc" | "drivers" | "mis">("rules");
  const locked = period?.status === "locked";
  if (!period) return <div className="card empty">Create a period first (Periods & uploads).</div>;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Adjustments & inputs</h1>
          <p>Manual inputs for {period.label || period.code}: TB adjustment rules, material-cost corrections, allocation drivers and MIS figures. {locked ? "This period is locked – inputs are read-only." : "Recalculate after changing them."}</p>
        </div>
        {locked ? <Badge status="locked">period locked</Badge> : null}
      </div>
      <div className="card flush">
        <div style={{ padding: "8px 12px 0" }}>
          <Tabs value={tab} onChange={setTab} items={[{ key: "rules", label: "TB adjustment rules" }, { key: "mc", label: "Material-cost adjustments" },
            { key: "drivers", label: "Drivers" }, { key: "mis", label: "MIS figures" }]} />
        </div>
        {tab === "rules" ? <AdjustmentRules locked={locked} /> : null}
        {tab === "mc" ? <McAdjustments locked={locked} /> : null}
        {tab === "drivers" ? <Drivers locked={locked} /> : null}
        {tab === "mis" ? <MisLines locked={locked} /> : null}
      </div>
    </>
  );
}
