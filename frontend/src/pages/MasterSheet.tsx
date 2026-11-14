import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, qs } from "../api";
import { useApp, useAuth, useToast } from "../context";
import { fmt } from "../format";
import { ErrorBox, FileDrop, Loading, Modal } from "../components/ui";
import { Icon } from "../components/icons";
import type { Period } from "../types";

// ------------------------------------------------------------------ types
type Sec = "ba" | "gl" | "cc";
const SECS: Sec[] = ["ba", "gl", "cc"];
const TITLE: Record<Sec, string> = { ba: "Business areas", gl: "GL accounts", cc: "Cost centres" };
const NOUN: Record<Sec, string> = { ba: "business area", gl: "GL", cc: "cost centre" };

interface Row {
  id: number;
  code: string;
  missing: string[];
  effective_from: string | null;
  gl_type?: string;
  plant_code?: string | null;
  [k: string]: unknown;
}
interface SheetData {
  on: string | null;
  sections: Record<Sec, Row[]>;
  stats: Record<Sec, { rows: number; missing: number; by_type?: Record<string, number> }>;
  columns: Record<Sec, { field: string; header: string }[]>;
  options: { plants: string[]; pg_codes: string[]; families: string[]; categories: string[]; bareas: string[];
    functions: string[]; allocation: string[] };
}
interface Pending { code: string; description?: string; amount: number; source: string }
interface SectionResult { added: string[]; changed: { code: string; before: Record<string, unknown>; after: Record<string, unknown> }[];
  removed: string[]; unchanged: number; not_in_file?: number; file_rows?: number }
interface ImportResult { filename: string; sheet: string; header_row: number; warnings: string[]; unknown?: string[];
  setup?: { plants: string[]; product_groups: string[] }; missing_functions?: string[];
  sections: Partial<Record<Sec, SectionResult>>; dry_run: boolean }

// exact header text of the workbook's Masters sheet
const HEADERS: Record<Sec, { field: string; header: string; width: number }[]> = {
  ba: [{ field: "code", header: "BA", width: 110 }, { field: "plant_code", header: "Plant", width: 70 },
    { field: "pg_code", header: "COM/ COR/ PG", width: 90 }, { field: "family", header: "Product family", width: 110 }],
  gl: [{ field: "code", header: "GL", width: 100 }, { field: "description", header: "GL Descript", width: 220 },
    { field: "category", header: "Category1", width: 160 }],
  cc: [{ field: "code", header: "Cost centre", width: 100 }, { field: "description", header: "Cost centre descrip", width: 200 },
    { field: "business_area", header: "BArea", width: 100 }, { field: "function_name", header: "Function", width: 110 },
    { field: "function", header: "Allocation logic", width: 100 }],
};
// mapping fields a row needs before the calculation can use it
const REQUIRED: Record<Sec, string[]> = { ba: ["plant_code", "pg_code"], gl: ["category"], cc: ["business_area", "function"] };
const HEADER_OF: Record<string, string> = Object.fromEntries(
  (Object.keys(HEADERS) as Sec[]).flatMap((s) => HEADERS[s].map((h) => [h.field, h.header])));

const OPTION_OF: Record<string, keyof SheetData["options"]> = {
  plant_code: "plants", pg_code: "pg_codes", family: "families", category: "categories", business_area: "bareas",
  function_name: "functions", function: "allocation",
};

interface Draft {
  edits: Record<string, Record<string, string>>;   // code -> field -> value
  adds: { key: string; row: Record<string, string> }[];
  deletes: string[];
}
const emptyDraft = (): Record<Sec, Draft> => ({ ba: { edits: {}, adds: [], deletes: [] }, gl: { edits: {}, adds: [], deletes: [] },
  cc: { edits: {}, adds: [], deletes: [] } });

let addSeq = 0;

// ------------------------------------------------------------------ apply-from choices
function monthStart(iso: string) { return `${iso.slice(0, 7)}-01`; }
function nextMonthStart(iso: string) {
  const [y, m] = iso.slice(0, 7).split("-").map(Number);
  return m === 12 ? `${y + 1}-01-01` : `${y}-${String(m + 1).padStart(2, "0")}-01`;
}
function monthLabel(iso: string) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: "short", year: "numeric" });
}
function applyChoices(periods: Period[]) {
  const actual = periods.filter((p) => p.scenario === "actual").sort((a, b) => a.end_date.localeCompare(b.end_date));
  const seen = new Set<string>();
  const out: { value: string; label: string }[] = [];
  for (const p of actual) {
    const d = monthStart(p.end_date);
    if (seen.has(d)) continue;
    seen.add(d);
    out.push({ value: d, label: `${p.label || p.code} onwards (from ${monthLabel(d)})` });
  }
  if (actual.length) {
    const d = nextMonthStart(actual[actual.length - 1].end_date);
    if (!seen.has(d)) out.push({ value: d, label: `Upcoming periods (from ${monthLabel(d)})` });
  }
  out.push({ value: "", label: "All periods (change in place, no history)" });
  return out;
}

// ------------------------------------------------------------------ page
export default function MasterSheet() {
  const { companyId, periods, period, periodId } = useApp();
  const { can } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const editable = can("edit");
  const choices = useMemo(() => applyChoices(periods), [periods]);
  const [applyFrom, setApplyFrom] = useState<string | null>(null);
  useEffect(() => {
    if (applyFrom === null && choices.length) {
      setApplyFrom(period && period.scenario === "actual" ? monthStart(period.end_date) : choices[choices.length > 1 ? choices.length - 2 : 0].value);
    }
  }, [choices, period, applyFrom]);
  const on = applyFrom ?? "";

  const q = useQuery({
    queryKey: ["master-sheet", companyId, on],
    queryFn: () => api.get<SheetData>(`/api/companies/${companyId}/master-sheet${qs({ on })}`),
    enabled: companyId !== null && applyFrom !== null,
  });
  const pendingQ = useQuery({
    queryKey: ["master-pending", companyId, periodId, on],
    queryFn: () => api.get<Record<Sec, Pending[]>>(`/api/companies/${companyId}/master-sheet/pending${qs({ period_id: periodId, on })}`),
    enabled: companyId !== null && periodId !== null && applyFrom !== null,
  });

  const [draft, setDraft] = useState(emptyDraft);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [editing, setEditing] = useState<{ sec: Sec; key: string; field: string } | null>(null);
  const [limit, setLimit] = useState(200);
  const [showUpload, setShowUpload] = useState(false);
  const [saving, setSaving] = useState(false);

  const changeCount = SECS.reduce((n, s) => n + Object.keys(draft[s].edits).length + draft[s].adds.length + draft[s].deletes.length, 0);
  useEffect(() => {
    if (!changeCount) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [changeCount]);

  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data) return <Loading />;
  const d = q.data;
  const pending = pendingQ.data ?? { ba: [], gl: [], cc: [] };
  const pendingCount = SECS.reduce((n, s) => n + pending[s].length, 0);
  const applyLabel = choices.find((c) => c.value === on)?.label ?? "All periods";

  // ---------------------------------------------------------------- draft helpers
  const setCell = (sec: Sec, key: string, field: string, value: string, isNew: boolean) => {
    setDraft((dr) => {
      const n = { ...dr, [sec]: { ...dr[sec] } };
      if (isNew) {
        n[sec].adds = dr[sec].adds.map((a) => (a.key === key ? { ...a, row: { ...a.row, [field]: value } } : a));
      } else {
        const orig = d.sections[sec].find((r) => r.code === key);
        const origVal = orig?.[field] == null ? "" : String(orig[field]);
        const e = { ...(dr[sec].edits[key] ?? {}) };
        if (value === origVal) delete e[field]; else e[field] = value;
        const edits = { ...dr[sec].edits };
        if (Object.keys(e).length) edits[key] = e; else delete edits[key];
        n[sec].edits = edits;
      }
      return n;
    });
  };
  const addRow = (sec: Sec, row: Record<string, string> = {}) => {
    setDraft((dr) => ({ ...dr, [sec]: { ...dr[sec], adds: [{ key: `new-${++addSeq}`, row: { code: "", ...row } }, ...dr[sec].adds] } }));
  };
  const toggleDelete = (sec: Sec, code: string) => {
    setDraft((dr) => {
      const del = dr[sec].deletes.includes(code) ? dr[sec].deletes.filter((c) => c !== code) : [...dr[sec].deletes, code];
      return { ...dr, [sec]: { ...dr[sec], deletes: del } };
    });
  };
  const removeAdd = (sec: Sec, key: string) => {
    setDraft((dr) => ({ ...dr, [sec]: { ...dr[sec], adds: dr[sec].adds.filter((a) => a.key !== key) } }));
  };
  const addPending = (sec: Sec, items: Pending[]) => {
    const have = new Set(draft[sec].adds.map((a) => a.row.code));
    items.filter((p) => !have.has(p.code)).forEach((p) => addRow(sec, { code: p.code, ...(p.description ? { description: p.description } : {}) }));
    setOnlyMissing(false);
  };

  const save = async () => {
    const body: Record<string, unknown> = { effective_from: on || null };
    for (const s of SECS) {
      const codes = new Set(d.sections[s].map((r) => r.code));
      const adds = draft[s].adds;
      for (const a of adds) {
        if (!a.row.code?.trim()) { toast(`${TITLE[s]}: a new row has no code`, "error"); return; }
        if (codes.has(a.row.code.trim())) { toast(`${TITLE[s]}: ${a.row.code} already exists – edit that row instead`, "error"); return; }
      }
      body[s] = {
        upserts: [...adds.map((a) => ({ ...a.row, code: a.row.code.trim() })),
          ...Object.entries(draft[s].edits).map(([code, e]) => ({ code, ...e }))],
        deletes: draft[s].deletes,
      };
    }
    setSaving(true);
    try {
      const r = await api.put<{ sections: Partial<Record<Sec, SectionResult>> }>(`/api/companies/${companyId}/master-sheet`, body);
      const parts = SECS.flatMap((s) => {
        const x = r.sections[s];
        if (!x) return [];
        return [`${TITLE[s]}: ${x.added.length} added, ${x.changed.length} changed, ${x.removed.length} removed`];
      });
      toast(`Saved – ${parts.join(" · ")}`);
      setDraft(emptyDraft());
      qc.invalidateQueries({ queryKey: ["master-sheet", companyId] });
      qc.invalidateQueries({ queryKey: ["master-pending", companyId] });
      qc.invalidateQueries({ queryKey: ["master", companyId] });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };

  // ---------------------------------------------------------------- visible rows per section
  type View = { key: string; isNew: boolean; deleted: boolean; values: Record<string, string>; edited: Set<string>; missing: string[]; gl_type?: string };
  const views: Record<Sec, View[]> = { ba: [], gl: [], cc: [] };
  for (const s of SECS) {
    const fields = HEADERS[s].map((h) => h.field);
    const all: View[] = [
      ...draft[s].adds.map((a) => ({ key: a.key, isNew: true, deleted: false, values: a.row, edited: new Set(fields),
        missing: REQUIRED[s].filter((f) => !a.row[f]) })),
      ...d.sections[s].map((r) => {
        const e = draft[s].edits[r.code] ?? {};
        const values: Record<string, string> = {};
        fields.forEach((f) => { values[f] = f in e ? e[f] : r[f] == null ? "" : String(r[f]); });
        return { key: r.code, isNew: false, deleted: draft[s].deletes.includes(r.code), values, edited: new Set(Object.keys(e)),
          missing: r.missing, gl_type: r.gl_type };
      }),
    ];
    views[s] = all.filter((v) => {
      if (v.isNew) return true;
      if (onlyMissing && !v.missing.length) return false;
      return fields.every((f) => {
        const flt = (filters[`${s}.${f}`] ?? "").trim().toLowerCase();
        if (!flt) return true;
        const val = (v.values[f] ?? "").toLowerCase();
        return flt === "(blank)" ? !val : val.includes(flt);
      });
    });
  }
  const total = Math.max(...SECS.map((s) => views[s].length));
  const shown = Math.min(total, limit);

  const cell = (s: Sec, v: View | undefined, f: string, first: boolean) => {
    if (!v) return <td key={`${s}.${f}`} className={`mc g-${s} empty`} />;
    const isEditing = editing && editing.sec === s && editing.key === v.key && editing.field === f;
    const val = v.values[f] ?? "";
    const canEdit = editable && !v.deleted && (f !== "code" || v.isNew);
    const need = !val && v.missing.includes(f);
    const cls = ["mc", `g-${s}`, first ? "first" : "", v.isNew ? "new" : "", v.deleted ? "deleted" : "",
      v.edited.has(f) && !v.isNew ? "edited" : "", need ? "need" : "", canEdit ? "editable" : ""].filter(Boolean).join(" ");
    const opt = OPTION_OF[f];
    return (
      <td key={`${s}.${f}`} className={cls} onClick={() => canEdit && !isEditing && setEditing({ sec: s, key: v.key, field: f })}>
        {isEditing ? (
          <input autoFocus defaultValue={val} list={opt ? `opt-${opt}` : undefined}
            onBlur={(e) => { setCell(s, v.key, f, e.target.value.trim(), v.isNew); setEditing(null); }}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              if (e.key === "Escape") setEditing(null);
            }} />
        ) : (
          <span className="mc-text">
            {val || (need ? "needs mapping" : v.isNew && f === "code" ? "new code…" : "")}
            {f === "category" && v.gl_type === "PRE" && val ? <span className="type-tag">PRE</span> : null}
          </span>
        )}
        {first && editable ? (
          <button className="row-x" title={v.isNew ? "Discard new row" : v.deleted ? "Keep this row" : `Remove this ${NOUN[s]}`}
            onClick={(e) => { e.stopPropagation(); if (v.isNew) removeAdd(s, v.key); else toggleDelete(s, v.key); }}>
            <Icon name={v.deleted ? "plus" : "x"} size={12} />
          </button>
        ) : null}
      </td>
    );
  };

  const st = d.stats;
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Inputs</div>
          <h1>Master dashboard</h1>
          <p>
            Business areas, GL accounts and cost centres in the workbook's Masters layout. Edit here or upload the same
            Excel – new rows are added and changes take effect from the period you choose, so the next calculations
            pick them up automatically while earlier periods keep their mapping.
          </p>
        </div>
        <div className="row">
          <button onClick={() => api.download(`/api/companies/${companyId}/master-sheet/export${qs({ on })}`, "master.xlsx")}>
            <Icon name="download" size={15} />Download Excel
          </button>
          {editable ? <button className="primary" onClick={() => setShowUpload(true)}><Icon name="upload" size={15} />Upload Excel</button> : null}
        </div>
      </div>

      <div className="card apply-bar">
        <div className="row" style={{ gap: 12 }}>
          <span className="dot-icon sky" style={{ width: 34, height: 34 }}><Icon name="target" size={16} /></span>
          <div>
            <div style={{ fontWeight: 650 }}>Changes apply from</div>
            <div className="muted small">The sheet shows the master valid then; edits and uploads start on that date.</div>
          </div>
          <select value={on} onChange={(e) => { if (changeCount && !confirm("Discard unsaved changes?")) return; setDraft(emptyDraft()); setApplyFrom(e.target.value); }}>
            {choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </div>
      </div>

      <div className="stat-cards">
        <div className="stat-card navy"><div className="stat-text">
          <div className="title">Business areas</div><div className="value">{st.ba.rows}</div>
          <div className="sub">{st.ba.missing ? <span className="var-chip">{st.ba.missing} need mapping</span> : "All mapped to plant and PG"}</div>
        </div></div>
        <div className="stat-card violet"><div className="stat-text">
          <div className="title">GL accounts</div><div className="value">{st.gl.rows}</div>
          <div className="sub">Non-PRE {st.gl.by_type?.NONPRE ?? 0} · PRE {st.gl.by_type?.PRE ?? 0} · Excluded {st.gl.by_type?.EXCLUDED ?? 0}
            {st.gl.missing ? <> · <span className="var-chip">{st.gl.missing} need mapping</span></> : null}</div>
        </div></div>
        <div className="stat-card teal"><div className="stat-text">
          <div className="title">Cost centres</div><div className="value">{st.cc.rows}</div>
          <div className="sub">{st.cc.missing ? <span className="var-chip">{st.cc.missing} need mapping</span> : "All mapped to BArea and allocation"}</div>
        </div></div>
        <div className="stat-card amber"><div className="stat-text">
          <div className="title">New in trial balances</div><div className="value">{pendingCount}</div>
          <div className="sub">{period ? `${period.label || period.code} uploads` : "Select a period"} · BA {pending.ba.length} · GL {pending.gl.length} · CC {pending.cc.length}</div>
        </div></div>
      </div>

      {pendingCount ? (
        <div className="card pending-card">
          <div className="card-head">
            <div>
              <h2><Icon name="sparkle" size={16} /> New codes in the uploaded trial balances</h2>
              <div className="muted small">
                Found in the {period?.label || period?.code} BA / CC trial balances but not in the master. Add them, fill in the mapping and save.
              </div>
            </div>
            {editable ? <button className="primary" onClick={() => SECS.forEach((s) => addPending(s, pending[s]))}><Icon name="plus" size={15} />Add all to the sheet</button> : null}
          </div>
          <div className="pending-grid">
            {SECS.map((s) => (
              <div key={s}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <strong>{TITLE[s]} <span className="muted">({pending[s].length})</span></strong>
                  {editable && pending[s].length ? <button className="small" onClick={() => addPending(s, pending[s])}>Add {pending[s].length}</button> : null}
                </div>
                <ul className="pending-list">
                  {pending[s].slice(0, 8).map((p) => (
                    <li key={p.code}><code>{p.code}</code> <span className="muted">{p.description ?? ""}</span><span className="right muted small">{fmt(p.amount, 0)}</span></li>
                  ))}
                  {pending[s].length > 8 ? <li className="muted small">and {pending[s].length - 8} more</li> : null}
                  {!pending[s].length ? <li className="muted small">None</li> : null}
                </ul>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="card flush">
        <div className="card-head" style={{ padding: "14px 16px 10px", marginBottom: 0 }}>
          <div>
            <h2>Master sheet</h2>
            <div className="muted small">Valid {on ? `from ${monthLabel(on)}` : "– latest version"} · click a cell to edit · type in the filter row to filter a column, "(blank)" for empty cells</div>
          </div>
          <div className="row">
            <label className="row small"><input type="checkbox" checked={onlyMissing} onChange={(e) => setOnlyMissing(e.target.checked)} /> Only rows that need mapping</label>
            {Object.values(filters).some(Boolean) ? <button className="small" onClick={() => setFilters({})}><Icon name="x" size={12} />Clear filters</button> : null}
          </div>
        </div>
        <div className="msheet-wrap">
          <table className="msheet">
            <colgroup>
              {HEADERS.ba.map((h) => <col key={`ba${h.field}`} style={{ width: h.width }} />)}<col style={{ width: 14 }} />
              {HEADERS.gl.map((h) => <col key={`gl${h.field}`} style={{ width: h.width }} />)}<col style={{ width: 14 }} />
              {HEADERS.cc.map((h) => <col key={`cc${h.field}`} style={{ width: h.width }} />)}
            </colgroup>
            <thead>
              <tr className="band">
                {SECS.map((s, i) => [
                  <th key={s} colSpan={HEADERS[s].length} className={`g-${s}`}>
                    <div className="band-inner">
                      <span>{TITLE[s]} <span className="muted">{views[s].length}{views[s].length !== d.sections[s].length + draft[s].adds.length ? ` of ${d.sections[s].length + draft[s].adds.length}` : ""}</span></span>
                      {editable ? <button className="small" onClick={() => addRow(s)}><Icon name="plus" size={12} />Add</button> : null}
                    </div>
                  </th>,
                  i < 2 ? <th key={`sep${s}`} className="sep" /> : null,
                ])}
              </tr>
              <tr className="heads">
                {SECS.map((s, i) => [
                  ...HEADERS[s].map((h) => <th key={`${s}.${h.field}`} className={`g-${s}`}>{h.header}</th>),
                  i < 2 ? <th key={`sep${s}`} className="sep" /> : null,
                ])}
              </tr>
              <tr className="filters-row">
                {SECS.map((s, i) => [
                  ...HEADERS[s].map((h) => {
                    const k = `${s}.${h.field}`;
                    const opt = OPTION_OF[h.field];
                    return (
                      <th key={k} className={`g-${s}`}>
                        <div className="flt">
                          <Icon name="filter" size={11} />
                          <input aria-label={`Filter ${h.header}`} value={filters[k] ?? ""} list={opt ? `opt-${opt}` : undefined}
                            onChange={(e) => { setFilters({ ...filters, [k]: e.target.value }); setLimit(200); }} />
                        </div>
                      </th>
                    );
                  }),
                  i < 2 ? <th key={`sep${s}`} className="sep" /> : null,
                ])}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: shown }, (_, i) => (
                <tr key={i}>
                  {SECS.map((s, si) => [
                    ...HEADERS[s].map((h, hi) => cell(s, views[s][i], h.field, hi === 0)),
                    si < 2 ? <td key={`sep${s}`} className="sep" /> : null,
                  ])}
                </tr>
              ))}
              {!total ? <tr><td colSpan={14} className="empty">No rows match the filters.</td></tr> : null}
            </tbody>
          </table>
        </div>
        {total > shown ? (
          <div className="row" style={{ padding: 12, justifyContent: "center" }}>
            <span className="muted small">Showing {shown} of {total} rows</span>
            <button className="small" onClick={() => setLimit(limit + 500)}>Show more</button>
            <button className="small" onClick={() => setLimit(total)}>Show all</button>
          </div>
        ) : null}
      </div>

      {Object.entries(d.options).map(([k, vals]) => (
        <datalist key={k} id={`opt-${k}`}>{vals.map((v) => <option key={v} value={v} />)}</datalist>
      ))}

      {changeCount ? (
        <div className="save-bar" role="region" aria-label="Unsaved changes">
          <span><strong>{changeCount}</strong> unsaved change{changeCount === 1 ? "" : "s"} · apply from <strong>{applyLabel}</strong></span>
          <span className="grow" />
          <button onClick={() => setDraft(emptyDraft())} disabled={saving}>Discard</button>
          <button className="primary" onClick={save} disabled={saving}><Icon name="save" size={15} />{saving ? "Saving…" : "Save changes"}</button>
        </div>
      ) : null}

      {showUpload ? (
        <UploadDialog companyId={companyId!} on={on} applyLabel={applyLabel} onClose={() => setShowUpload(false)}
          onDone={() => {
            qc.invalidateQueries({ queryKey: ["master-sheet", companyId] });
            qc.invalidateQueries({ queryKey: ["master-pending", companyId] });
            qc.invalidateQueries({ queryKey: ["master", companyId] });
          }} />
      ) : null}
      <p className="muted small">
        Plants, product groups, categories and PRE functions are set up under <Link to="/masters">Masters</Link>. The plant of a cost
        centre comes from its BArea; a Category1 in the PRE list makes the GL a PRE GL and "Excluded" leaves it out of the P&amp;L.
      </p>
    </>
  );
}

// ------------------------------------------------------------------ upload
function UploadDialog({ companyId, on, applyLabel, onClose, onDone }: {
  companyId: number; on: string; applyLabel: string; onClose: () => void; onDone: () => void;
}) {
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [removeMissing, setRemoveMissing] = useState(false);
  const [createSetup, setCreateSetup] = useState(false);
  const [preview, setPreview] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (f: File, dry: boolean, remove = removeMissing, setup = createSetup) => {
    setBusy(true);
    try {
      const r = await api.upload<ImportResult>(`/api/companies/${companyId}/master-sheet/import`, {
        file: f, effective_from: on, remove_missing: remove ? "true" : "false", dry_run: dry ? "true" : "false",
        create_setup: setup ? "true" : "false" });
      if (dry) setPreview(r);
      else {
        const n = Object.values(r.sections).reduce((t, s) => t + (s?.added.length ?? 0) + (s?.changed.length ?? 0) + (s?.removed.length ?? 0), 0);
        toast(`Master updated from ${r.filename}: ${n} change(s)`);
        onDone();
        onClose();
      }
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const changes = preview ? Object.values(preview.sections).reduce((t, s) => t + (s?.added.length ?? 0) + (s?.changed.length ?? 0) + (s?.removed.length ?? 0), 0)
    + (preview.setup ? preview.setup.plants.length + preview.setup.product_groups.length : 0) : 0;
  return (
    <Modal title="Upload master sheet" onClose={onClose} wide footer={preview ? (<>
      <button onClick={() => { setPreview(null); setFile(null); }}>Choose another file</button>
      <button className="primary" disabled={busy || !changes} onClick={() => file && run(file, false)}>
        {busy ? "Applying…" : changes ? `Apply ${changes} change(s)` : "Nothing to apply"}
      </button>
    </>) : undefined}>
      <p className="muted" style={{ margin: 0 }}>
        Upload the master in the same format (BA · Plant · COM/COR/PG · Product family | GL · GL Descript · Category1 | Cost centre ·
        Cost centre descrip · BArea · Function · Allocation logic). A file with only the new rows works too. Blank cells never clear a value.
        Changes apply from <strong>{applyLabel}</strong>.
      </p>
      <label className="row small"><input type="checkbox" checked={removeMissing}
        onChange={(e) => { setRemoveMissing(e.target.checked); if (file) run(file, true, e.target.checked); }} />
        Also remove codes that are not in the file (use only with a complete master)</label>
      <label className="row small"><input type="checkbox" checked={createSetup}
        onChange={(e) => { setCreateSetup(e.target.checked); if (file) run(file, true, removeMissing, e.target.checked); }} />
        Also create the plants and product groups the business areas name (for a new company)</label>
      {!preview ? (
        <FileDrop accept=".xlsx,.xlsm" busy={busy} label="Drop the master Excel here or click to choose"
          onFile={(f) => { setFile(f); run(f, true); }} />
      ) : (
        <div className="stack">
          <div className="muted small">{preview.filename} · sheet “{preview.sheet}”, headers on row {preview.header_row} · preview – nothing is saved yet</div>
          {preview.warnings.map((w) => <div key={w} className="badge warning" style={{ whiteSpace: "normal" }}>{w}</div>)}
          {preview.setup && (preview.setup.plants.length || preview.setup.product_groups.length) ? (
            <div className="badge ok" style={{ whiteSpace: "normal" }}>
              Creates {preview.setup.plants.length} plant(s): {preview.setup.plants.join(", ") || "none"} · {preview.setup.product_groups.length} product
              group(s): {preview.setup.product_groups.join(", ") || "none"}
            </div>
          ) : null}
          {preview.missing_functions?.length ? (
            <div className="small muted">Allocation-logic values that are not a product group, COM or COR – set them up as PRE functions
              on the Masters page: <strong>{preview.missing_functions.join(", ")}</strong></div>
          ) : null}
          <table className="data">
            <thead><tr><th>List</th><th className="num">Rows in file</th><th className="num">New</th><th className="num">Changed</th>
              <th className="num">Unchanged</th><th className="num">{removeMissing ? "Removed" : "Not in file (kept)"}</th></tr></thead>
            <tbody>
              {SECS.filter((s) => preview.sections[s]).map((s) => {
                const r = preview.sections[s]!;
                return (
                  <tr key={s}><td>{TITLE[s]}</td><td className="num">{r.file_rows}</td>
                    <td className="num pos">{r.added.length || "–"}</td><td className="num">{r.changed.length || "–"}</td>
                    <td className="num">{r.unchanged}</td><td className={`num ${removeMissing && r.removed.length ? "neg" : ""}`}>{removeMissing ? r.removed.length : r.not_in_file}</td></tr>
                );
              })}
            </tbody>
          </table>
          <div className="issues stack">
            {SECS.filter((s) => preview.sections[s]).map((s) => {
              const r = preview.sections[s]!;
              if (!r.added.length && !r.changed.length && !r.removed.length) return null;
              return (
                <details key={s} className="control" open={s === "cc" || undefined}>
                  <summary><strong>{TITLE[s]}</strong> <span className="muted small">{r.added.length} new · {r.changed.length} changed · {r.removed.length} removed</span></summary>
                  {r.added.length ? <div className="small" style={{ marginTop: 6 }}><span className="pos">New:</span> {r.added.slice(0, 60).join(", ")}{r.added.length > 60 ? " …" : ""}</div> : null}
                  {r.removed.length ? <div className="small"><span className="neg">Removed:</span> {r.removed.slice(0, 60).join(", ")}</div> : null}
                  {r.changed.length ? (
                    <table className="data" style={{ marginTop: 6 }}>
                      <thead><tr><th>Code</th><th>Field</th><th>Now</th><th>New</th></tr></thead>
                      <tbody>
                        {r.changed.slice(0, 100).flatMap((c) => Object.keys(c.after).map((f) => (
                          <tr key={`${c.code}.${f}`}><td>{c.code}</td><td>{HEADER_OF[f] ?? f}</td><td className="muted">{String(c.before[f] ?? "—")}</td><td><strong>{String(c.after[f] ?? "—")}</strong></td></tr>
                        )))}
                      </tbody>
                    </table>
                  ) : null}
                </details>
              );
            })}
            {preview.unknown?.length ? (
              <details className="control"><summary><strong>Values not set up in Masters</strong> <span className="muted small">{preview.unknown.length}</span></summary>
                <ul className="small">{preview.unknown.slice(0, 50).map((u) => <li key={u}>{u}</li>)}</ul></details>
            ) : null}
          </div>
        </div>
      )}
    </Modal>
  );
}
