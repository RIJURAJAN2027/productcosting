import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, qs } from "../api";
import { useApp, useAuth, useToast } from "../context";
import { Badge, ErrorBox, Field, FileDrop, Loading, Modal, Tabs } from "../components/ui";

type Kind = "plants" | "product-groups" | "business-areas" | "gl-accounts" | "cost-centres" | "categories"
  | "pre-functions" | "allocation-rules" | "pnl-lines";

type FieldType = "text" | "number" | "select" | "checkbox" | "date" | "json" | "formula" | "textarea";
interface FieldSpec { key: string; label: string; type?: FieldType; options?: string[]; hint?: string; table?: boolean; width?: number }

const DATES: FieldSpec[] = [
  { key: "effective_from", label: "Effective from", type: "date", table: true },
  { key: "effective_to", label: "Effective to", type: "date", table: true, hint: "Leave blank while the row is current." },
];

const SPECS: Record<Kind, { label: string; help: string; fields: FieldSpec[] }> = {
  plants: {
    label: "Plants", help: "Plants (reporting units) in display order. Corporate / head-office units are plants too.",
    fields: [{ key: "code", label: "Code", table: true }, { key: "name", label: "Name", table: true },
      { key: "sort_order", label: "Order", type: "number", table: true },
      { key: "is_reporting", label: "Shown in reports", type: "checkbox", table: true }, ...DATES],
  },
  "product-groups": {
    label: "Product groups", help: "Product groups and the product family each one rolls up to.",
    fields: [{ key: "code", label: "Code", table: true }, { key: "name", label: "Name", table: true },
      { key: "family", label: "Family", table: true }, { key: "sort_order", label: "Order", type: "number", table: true }, ...DATES],
  },
  "business-areas": {
    label: "Business areas", help: "Each business area (column of the BA trial balance) maps to a plant and a product group, COM (plant common) or COR (corporate).",
    fields: [{ key: "code", label: "BA code", table: true }, { key: "name", label: "Name", table: true },
      { key: "plant_code", label: "Plant", table: true }, { key: "pg_code", label: "Product group / COM / COR", table: true },
      { key: "family", label: "Family", table: true }, ...DATES],
  },
  "gl-accounts": {
    label: "GL accounts", help: "Each GL maps to a P&L category and is Non-PRE (taken from the BA TB), PRE (people cost from the cost-centre TB) or excluded.",
    fields: [{ key: "code", label: "GL code", table: true }, { key: "description", label: "Description", table: true },
      { key: "fs_description", label: "FS description" }, { key: "category", label: "Category", table: true },
      { key: "gl_type", label: "Type", type: "select", options: ["NONPRE", "PRE", "EXCLUDED"], table: true },
      { key: "note", label: "Note", type: "textarea" }, ...DATES],
  },
  "cost-centres": {
    label: "Cost centres", help: "Cost centres of the CC trial balance: business area, plant and function (a product group code or a service function such as QC, stores, maintenance).",
    fields: [{ key: "code", label: "CC code", table: true }, { key: "description", label: "Description", table: true },
      { key: "business_area", label: "Business area", table: true }, { key: "plant_code", label: "Plant", table: true },
      { key: "function", label: "Function", table: true }, ...DATES],
  },
  categories: {
    label: "Categories", help: "Cost / revenue categories that GLs roll up to. Sign −1 flips the TB sign (e.g. revenue credits shown positive).",
    fields: [{ key: "name", label: "Name", table: true }, { key: "sign", label: "Sign", type: "select", options: ["1", "-1"], table: true },
      { key: "group_name", label: "Group", table: true }, { key: "sort_order", label: "Order", type: "number", table: true }, ...DATES],
  },
  "pre-functions": {
    label: "PRE functions", help: "Functions people cost (PRE) is booked to. Factory functions feed PRE-factory, corporate functions feed PRE-corporate. Mode: plant = spread within the plant, pooled = one company-wide pool, custom = explicit legs in an allocation rule.",
    fields: [{ key: "code", label: "Code", table: true }, { key: "name", label: "Name", table: true },
      { key: "group", label: "Group", type: "select", options: ["factory", "corporate"], table: true },
      { key: "mode", label: "Mode", type: "select", options: ["plant", "pooled", "custom"], table: true }, ...DATES],
  },
  "allocation-rules": {
    label: "Allocation rules", help: "How each Common (COM), Corporate (COR) or PRE-function pool is spread to product groups. Share = pool × basis of the product group ÷ basis total of the plant (or of the pool's denominator).",
    fields: [{ key: "dataset", label: "Dataset", type: "select", options: ["NONPRE", "PRE"], table: true },
      { key: "category", label: "Category", table: true }, { key: "pool", label: "Pool", table: true, hint: "COM, COR or a PRE function code" },
      { key: "mode", label: "Mode", type: "select", options: ["plant", "pooled", "custom"], table: true },
      { key: "label", label: "Label", table: true }, { key: "sort_order", label: "Order", type: "number" },
      { key: "definition", label: "Definition (JSON)", type: "json",
        hint: 'e.g. {"basis": {"kind": "line", "line": "sales_total"}} · basis kinds: component, line, pre, const, sum, driver, none · optional basis_cells {"PLANT|PG": basis}, legs, denominator, plugs, fixed_cells' },
      { key: "note", label: "Note", type: "textarea" }, ...DATES],
  },
  "pnl-lines": {
    label: "P&L lines", help: "Every line of the P&L with its formula. Lines can override the formula for one plant or cell, and set totals or ratio behaviour in the attributes.",
    fields: [{ key: "code", label: "Code", table: true }, { key: "label", label: "Label", table: true },
      { key: "expr", label: "Formula", type: "formula", table: true,
        hint: "[L:line] another line · [C:Category|direct|common|corporate|total] category part · [P:direct|Wages], [P:fn|QC], [P:group|factory] PRE · [D:driver] · [MCADJ] · @PLANT|PG pins a cell · SUM, NEG, ABS, MIN, MAX · + − × ÷ ( )" },
      { key: "sort_order", label: "Order", type: "number", table: true },
      { key: "attrs", label: "Attributes (JSON)", type: "json",
        hint: 'e.g. {"hidden": true} · {"additive": false} for ratios · {"pct_base": "sales_total"} · {"plant_exprs": {"PLANT": "expr"}} · {"cell_exprs": {"PLANT|PG": "expr"}} · {"total_exprs": {...}}' },
      ...DATES],
  },
};

const KINDS = Object.keys(SPECS) as Kind[];
type Row = Record<string, unknown> & { id: number };

function display(v: unknown, f: FieldSpec): string {
  if (v === null || v === undefined || v === "") return "";
  if (f.type === "checkbox") return v ? "Yes" : "No";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function Editor({ kind, row, onClose }: { kind: Kind; row: Partial<Row> | null; onClose: () => void }) {
  const { companyId } = useApp();
  const toast = useToast();
  const qc = useQueryClient();
  const spec = SPECS[kind];
  const init = useMemo(() => {
    const d: Record<string, string | boolean> = {};
    spec.fields.forEach((f) => {
      const v = row?.[f.key];
      if (f.type === "checkbox") d[f.key] = v === undefined ? true : Boolean(v);
      else if (f.type === "json") d[f.key] = JSON.stringify(v ?? {}, null, 2);
      else d[f.key] = v === null || v === undefined ? "" : String(v);
    });
    return d;
  }, [row, spec]);
  const [form, setForm] = useState(init);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: string | boolean) => setForm({ ...form, [k]: v });
  const save = async () => {
    const body: Record<string, unknown> = {};
    try {
      spec.fields.forEach((f) => {
        const v = form[f.key];
        if (f.type === "json") body[f.key] = (v as string).trim() ? JSON.parse(v as string) : {};
        else if (f.type === "number") body[f.key] = v === "" ? 0 : Number(v);
        else if (f.type === "checkbox") body[f.key] = v;
        else if (f.key === "sign") body[f.key] = Number(v || 1);
        else if (f.type === "date") body[f.key] = v || (f.key === "effective_from" ? "2000-01-01" : null);
        else body[f.key] = v === "" ? null : v;
      });
    } catch (e) {
      setErr(`Invalid JSON: ${(e as Error).message}`);
      return;
    }
    setBusy(true);
    try {
      if (row?.id) await api.patch(`/api/companies/${companyId}/masters/${kind}/${row.id}`, body);
      else await api.post(`/api/companies/${companyId}/masters/${kind}`, body);
      toast(row?.id ? "Saved" : "Created");
      qc.invalidateQueries({ queryKey: ["master", companyId, kind] });
      qc.invalidateQueries({ queryKey: ["masters-validate", companyId] });
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`${row?.id ? "Edit" : "New"} – ${spec.label}`} onClose={onClose} wide={spec.fields.some((f) => f.type === "json" || f.type === "formula")}
      footer={<><button onClick={onClose}>Cancel</button><button className="primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button></>}>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
        {spec.fields.filter((f) => !["json", "formula", "textarea"].includes(f.type ?? "")).map((f) => (
          <Field key={f.key} label={f.label} hint={f.hint}>
            {f.type === "select" ? (
              <select value={String(form[f.key])} onChange={(e) => set(f.key, e.target.value)}>
                <option value="" />
                {f.options!.map((o) => <option key={o}>{o}</option>)}
              </select>
            ) : f.type === "checkbox" ? (
              <input type="checkbox" checked={Boolean(form[f.key])} onChange={(e) => set(f.key, e.target.checked)} style={{ alignSelf: "flex-start" }} />
            ) : (
              <input type={f.type === "number" ? "number" : f.type === "date" ? "date" : "text"} value={String(form[f.key])}
                onChange={(e) => set(f.key, e.target.value)} />
            )}
          </Field>
        ))}
      </div>
      {spec.fields.filter((f) => ["json", "formula", "textarea"].includes(f.type ?? "")).map((f) => (
        <Field key={f.key} label={f.label} hint={f.hint}>
          <textarea rows={f.type === "json" ? 10 : f.type === "formula" ? 4 : 2} spellCheck={false} value={String(form[f.key])}
            onChange={(e) => set(f.key, e.target.value)} />
        </Field>
      ))}
      {err ? <div style={{ color: "var(--bad)" }}>{err}</div> : null}
    </Modal>
  );
}

function MasterTable({ kind }: { kind: Kind }) {
  const { companyId } = useApp();
  const { can } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [on, setOn] = useState("");
  const [limit, setLimit] = useState(200);
  const [edit, setEdit] = useState<Partial<Row> | null | undefined>(undefined);
  const [versionDate, setVersionDate] = useState<string | null>(null);
  const spec = SPECS[kind];
  const q = useQuery({
    queryKey: ["master", companyId, kind, on],
    queryFn: () => api.get<Row[]>(`/api/companies/${companyId}/masters/${kind}${qs({ on })}`),
    enabled: companyId !== null,
  });
  const rows = useMemo(() => {
    const s = search.trim().toLowerCase();
    const all = q.data ?? [];
    return s ? all.filter((r) => Object.values(r).some((v) => v !== null && JSON.stringify(v).toLowerCase().includes(s))) : all;
  }, [q.data, search]);
  const cols = spec.fields.filter((f) => f.table);
  const del = async (r: Row) => {
    if (!confirm("Delete this row? (Prefer closing it with an end date to keep history.)")) return;
    try {
      await api.del(`/api/companies/${companyId}/masters/${kind}/${r.id}`);
      toast("Deleted");
      qc.invalidateQueries({ queryKey: ["master", companyId, kind] });
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const newVersion = async () => {
    if (!versionDate) return;
    try {
      const r = await api.post<{ copied: number }>(`/api/companies/${companyId}/masters/${kind}/new-version${qs({ effective_from: versionDate })}`);
      toast(`New version from ${versionDate}: ${r.copied} row(s) copied`);
      setVersionDate(null);
      qc.invalidateQueries({ queryKey: ["master", companyId, kind] });
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <div className="stack" style={{ padding: 12 }}>
      <div className="muted small">{spec.help}</div>
      <div className="row">
        <input type="search" placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} style={{ minWidth: 220 }} />
        <label className="row small">Valid on <input type="date" value={on} onChange={(e) => setOn(e.target.value)} /></label>
        {on ? <button className="small ghost" onClick={() => setOn("")}>all versions</button> : null}
        <span className="muted small">{rows.length} row(s)</span>
        <span className="right" />
        {can("edit") ? (
          <>
            <button onClick={() => setVersionDate(new Date().toISOString().slice(0, 10))}>New version…</button>
            <button className="primary" onClick={() => setEdit(null)}>+ Add</button>
          </>
        ) : null}
      </div>
      {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
      <div className="table-wrap" style={{ maxHeight: "calc(100vh - 330px)" }}>
        <table className="data">
          <thead><tr>{cols.map((c) => <th key={c.key}>{c.label}</th>)}{can("edit") ? <th /> : null}</tr></thead>
          <tbody>
            {rows.slice(0, limit).map((r) => (
              <tr key={r.id} className={can("edit") ? "clickable" : ""} onClick={() => can("edit") && setEdit(r)}>
                {cols.map((c) => (
                  <td key={c.key} className={c.type === "formula" ? "mono" : c.type === "number" ? "num" : ""}
                    style={c.type === "formula" ? { maxWidth: 520, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } : undefined}
                    title={c.type === "formula" ? String(r[c.key] ?? "") : undefined}>
                    {c.key === "gl_type" ? <Badge status={r[c.key] === "EXCLUDED" ? "draft" : "open"}>{String(r[c.key])}</Badge> : display(r[c.key], c)}
                  </td>
                ))}
                {can("edit") ? (
                  <td style={{ width: 1 }} onClick={(e) => e.stopPropagation()}>
                    <button className="small ghost" aria-label="Delete" title="Delete" onClick={() => del(r)}>✕</button>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length > limit ? (
          <div className="empty"><button onClick={() => setLimit(limit + 500)}>Show more ({rows.length - limit} hidden)</button></div>
        ) : null}
        {!q.isLoading && !rows.length ? <div className="empty">No rows.</div> : null}
      </div>
      {edit !== undefined ? <Editor kind={kind} row={edit} onClose={() => setEdit(undefined)} /> : null}
      {versionDate !== null ? (
        <Modal title={`New version of ${spec.label}`} onClose={() => setVersionDate(null)}
          footer={<><button onClick={() => setVersionDate(null)}>Cancel</button><button className="primary" onClick={newVersion}>Create version</button></>}>
          <div className="small">Every row valid on this date is closed the day before and copied as a new row starting on it. Edit the copies to change the mapping from that date onwards; periods ending earlier keep the old mapping.</div>
          <Field label="Effective from"><input type="date" value={versionDate} onChange={(e) => setVersionDate(e.target.value)} /></Field>
        </Modal>
      ) : null}
    </div>
  );
}

function Templates() {
  const { companyId } = useApp();
  const { can } = useAuth();
  const toast = useToast();
  const [name, setName] = useState("pg_wise");
  const [text, setText] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["template", companyId, name],
    queryFn: () => api.get<unknown[]>(`/api/companies/${companyId}/masters-templates/${name}`),
    enabled: companyId !== null,
  });
  const value = text ?? (q.data ? JSON.stringify(q.data, null, 1) : "");
  const save = async () => {
    try {
      const rows = JSON.parse(value);
      await api.put(`/api/companies/${companyId}/masters-templates/${name}`, rows);
      toast("Layout saved");
      setText(null);
      q.refetch();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <div className="stack" style={{ padding: 12 }}>
      <div className="muted small">Report layouts list the rows of each report in order: <code>{'{"label": "...", "line": "code", "style": {"bold": true}}'}</code>, <code>{'{"kind": "header", "label": "..."}'}</code> or <code>{'{"kind": "blank"}'}</code>. Rows can point to any P&L line, including hidden ones.</div>
      <div className="row">
        <select value={name} onChange={(e) => { setName(e.target.value); setText(null); }}>
          {["pg_wise", "plant_wise", "pg_totals", "all_pg"].map((n) => <option key={n}>{n}</option>)}
        </select>
        <span className="right" />
        {can("edit") ? <button className="primary" disabled={text === null} onClick={save}>Save layout</button> : null}
      </div>
      {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
      <textarea rows={24} spellCheck={false} value={value} onChange={(e) => setText(e.target.value)} readOnly={!can("edit")} />
    </div>
  );
}

interface Validation { problems: { level: "error" | "warning"; message: string }[]; counts: Record<string, number> }

export default function Masters() {
  const { companyId, company } = useApp();
  const { can } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const [kind, setKind] = useState<Kind | "templates">("gl-accounts");
  const [importing, setImporting] = useState(false);
  const [mode, setMode] = useState<"merge" | "replace">("merge");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Record<string, { created: number; updated: number; errors: string[] }> | null>(null);
  const v = useQuery({
    queryKey: ["masters-validate", companyId],
    queryFn: () => api.get<Validation>(`/api/companies/${companyId}/masters-validate`),
    enabled: companyId !== null,
  });
  const doImport = async (f: File) => {
    setBusy(true);
    try {
      const r = await api.upload<Record<string, { created: number; updated: number; errors: string[] }>>(
        `/api/companies/${companyId}/masters-import`, { file: f, mode });
      setResult(r);
      qc.invalidateQueries({ queryKey: ["master", companyId] });
      qc.invalidateQueries({ queryKey: ["masters-validate", companyId] });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Masters</h1>
          <p>Mappings and rules that drive the calculation for {company?.name}. Every row has an effective-date range, so you can change a mapping from a date without rewriting history; every change is recorded in the audit trail.</p>
        </div>
        <div className="row">
          <button onClick={() => api.download(`/api/companies/${companyId}/masters-export`)}>⬇ Export to Excel</button>
          {can("edit") ? <button onClick={() => { setResult(null); setImporting(true); }}>⇪ Import from Excel</button> : null}
        </div>
      </div>
      {v.data ? (
        <div className="card row small">
          {v.data.problems.some((p) => p.level === "error") ? <Badge status="error">{v.data.problems.filter((p) => p.level === "error").length} configuration error(s)</Badge> : null}
          {v.data.problems.some((p) => p.level === "warning") ? <Badge status="warning">{v.data.problems.filter((p) => p.level === "warning").length} warning(s)</Badge> : null}
          {!v.data.problems.length ? <Badge status="ok">configuration valid</Badge> : null}
          {Object.entries(v.data.counts).map(([k, n]) => <span key={k} className="muted">{k.replace("_", " ")}: <strong>{n}</strong></span>)}
          {v.data.problems.length ? (
            <details style={{ width: "100%" }}>
              <summary className="clickable">Show problems</summary>
              <ul>{v.data.problems.slice(0, 100).map((p, i) => <li key={i}><Badge status={p.level}>{p.level}</Badge> {p.message}</li>)}</ul>
            </details>
          ) : null}
        </div>
      ) : null}
      <div className="card flush">
        <div style={{ padding: "8px 12px 0" }}>
          <Tabs value={kind} onChange={setKind} items={[...KINDS.map((k) => ({ key: k as Kind | "templates", label: SPECS[k].label })), { key: "templates", label: "Report layouts" }]} />
        </div>
        {kind === "templates" ? <Templates /> : <MasterTable key={kind} kind={kind} />}
      </div>
      {importing ? (
        <Modal title="Import masters from Excel" onClose={() => setImporting(false)} footer={<button onClick={() => setImporting(false)}>Close</button>}>
          <div className="small">Use the workbook from <em>Export to Excel</em> as the template: one sheet per master. <strong>Merge</strong> updates rows with the same key and adds new ones; <strong>Replace</strong> deletes each imported sheet's rows first.</div>
          <div className="row">
            <label className="row small"><input type="radio" checked={mode === "merge"} onChange={() => setMode("merge")} /> Merge</label>
            <label className="row small"><input type="radio" checked={mode === "replace"} onChange={() => setMode("replace")} /> Replace</label>
          </div>
          <FileDrop accept=".xlsx" label="Drop the masters workbook here or click to choose" busy={busy} onFile={doImport} />
          {result ? (
            <table className="data">
              <thead><tr><th>Sheet</th><th className="num">Created</th><th className="num">Updated</th><th>Errors</th></tr></thead>
              <tbody>
                {Object.entries(result).map(([s, r]) => (
                  <tr key={s}><td>{s}</td><td className="num">{r.created}</td><td className="num">{r.updated}</td>
                    <td className="small">{r.errors.length ? <span style={{ color: "var(--bad)" }}>{r.errors.slice(0, 5).join("; ")}</span> : "—"}</td></tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </Modal>
      ) : null}
    </>
  );
}
