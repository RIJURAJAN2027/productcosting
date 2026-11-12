import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApp, useAuth, useToast } from "../context";
import { dateTime, fmt } from "../format";
import type { Period } from "../types";
import { Badge, ErrorBox, Field, FileDrop, Loading, Modal } from "../components/ui";

interface Issue { level: string; type: string; message: string; code?: string; amount?: number; description?: string }
interface UploadInfo {
  id: number; kind: string; filename: string; status: string; is_active: boolean; created_at: string | null;
  summary: Record<string, unknown>; issues: Issue[] | null; issue_count?: number;
}

function IssueTable({ issues }: { issues: Issue[] }) {
  const [filter, setFilter] = useState("all");
  const types = Array.from(new Set(issues.map((i) => i.type)));
  const shown = issues.filter((i) => filter === "all" || i.type === filter);
  if (!issues.length) return <div className="muted">No issues – the file matches the masters.</div>;
  const label: Record<string, string> = { new_gl: "New GL accounts", new_ba: "New business areas", new_cc: "New cost centres",
    row_total_mismatch: "Row total ≠ sum of columns", duplicate_gl: "Duplicate GL rows", non_numeric: "Non-numeric cells",
    ignored_columns: "Ignored columns", duplicate_column: "Duplicate columns" };
  return (
    <div className="stack">
      <div className="row">
        <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter issues">
          <option value="all">All issues ({issues.length})</option>
          {types.map((t) => <option key={t} value={t}>{label[t] ?? t} ({issues.filter((i) => i.type === t).length})</option>)}
        </select>
        <span className="muted small">New GLs / business areas / cost centres must be added to the masters (or the amounts stay flagged as unmapped in the controls).</span>
      </div>
      <div className="issues table-wrap">
        <table className="data">
          <thead><tr><th>Level</th><th>Type</th><th>Message</th><th className="num">Amount (₹)</th></tr></thead>
          <tbody>
            {shown.slice(0, 500).map((i, n) => (
              <tr key={n}>
                <td><Badge status={i.level === "info" ? "draft" : i.level}>{i.level}</Badge></td>
                <td>{label[i.type] ?? i.type}</td>
                <td>{i.message}</td>
                <td className="num">{i.amount !== undefined ? fmt(i.amount, 2) : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function UploadPanel({ period, kind, title, hint }: { period: Period; kind: "BA_TB" | "CC_TB" | "MIS"; title: string; hint: string }) {
  const { companyId } = useApp();
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<UploadInfo | null>(null);
  const current = period.uploads[kind];
  const upload = async (f: File) => {
    setBusy(true);
    try {
      const r = await api.upload<UploadInfo & { rows?: number }>(`/api/companies/${companyId}/periods/${period.id}/uploads`, { kind, file: f });
      if (kind === "MIS") toast(`MIS figures loaded: ${(r as { rows?: number }).rows ?? 0} rows`);
      else {
        setResult(r);
        toast(`${title} uploaded – ${r.status}`);
      }
      qc.invalidateQueries({ queryKey: ["periods", companyId] });
      qc.invalidateQueries({ queryKey: ["uploads", period.id] });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const openDetail = async () => {
    if (!current) return;
    setResult(await api.get<UploadInfo>(`/api/companies/${companyId}/uploads/${current.id}`));
  };
  return (
    <div className="card stack">
      <div className="card-head" style={{ margin: 0 }}>
        <h3 style={{ margin: 0 }}>{title}</h3>
        {current ? <Badge status={current.status} /> : <span className="badge">not uploaded</span>}
      </div>
      <div className="muted small">{hint}</div>
      {current ? (
        <div className="small">
          <strong>{current.filename}</strong> · {dateTime(current.uploaded_at)}{" "}
          {kind !== "MIS" ? (
            <>
              <button className="small" onClick={openDetail}>Validation</button>{" "}
              <button className="small" onClick={() => api.download(`/api/companies/${companyId}/uploads/${current.id}/download`)}>Download</button>
            </>
          ) : null}
        </div>
      ) : null}
      {can("edit") && period.status !== "locked" ? (
        <FileDrop busy={busy} accept=".xlsx,.xlsm,.csv" label={`Drop the ${title} file here or click to choose (.xlsx / .csv)`} onFile={upload} />
      ) : null}
      {result ? (
        <Modal title={`Validation – ${result.filename}`} onClose={() => setResult(null)} wide>
          <div className="row">
            <Badge status={result.status} />
            <span className="small">Header row {String(result.summary.header_row ?? "")} · {String(result.summary.gl_rows ?? "")} GL rows ·{" "}
              {String(result.summary.dimension_columns ?? "")} columns · total ₹ {fmt(Number(result.summary.total ?? 0))}</span>
          </div>
          {result.summary.total_by_gl_type ? (
            <div className="small muted">By GL type: {Object.entries(result.summary.total_by_gl_type as Record<string, number>)
              .map(([k, v]) => `${k} ₹ ${fmt(v)}`).join(" · ")}</div>
          ) : null}
          <IssueTable issues={result.issues ?? []} />
        </Modal>
      ) : null}
    </div>
  );
}

function NewPeriod({ onClose }: { onClose: () => void }) {
  const { companyId } = useApp();
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ code: "", label: "", scenario: "actual", basis: "YTD", start_date: "", end_date: "", fiscal_year: "" });
  const save = async () => {
    try {
      await api.post(`/api/companies/${companyId}/periods`, { ...f, start_date: f.start_date || null, fiscal_year: f.fiscal_year || null });
      qc.invalidateQueries({ queryKey: ["periods", companyId] });
      toast("Period created");
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const set = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }));
  return (
    <Modal title="New period" onClose={onClose} footer={<><button onClick={onClose}>Cancel</button><button className="primary" onClick={save} disabled={!f.code || !f.end_date}>Create</button></>}>
      <div className="form-grid">
        <Field label="Code" hint="e.g. 2026-10"><input value={f.code} onChange={(e) => set("code", e.target.value)} /></Field>
        <Field label="Label"><input value={f.label} onChange={(e) => set("label", e.target.value)} placeholder="YTD Apr-Oct 2026" /></Field>
        <Field label="Scenario">
          <select value={f.scenario} onChange={(e) => set("scenario", e.target.value)}>
            <option value="actual">Actual</option><option value="budget">Budget</option><option value="forecast">Forecast</option>
          </select>
        </Field>
        <Field label="Basis">
          <select value={f.basis} onChange={(e) => set("basis", e.target.value)}><option value="YTD">Year to date</option><option value="MONTH">Month</option></select>
        </Field>
        <Field label="Start date"><input type="date" value={f.start_date} onChange={(e) => set("start_date", e.target.value)} /></Field>
        <Field label="End date"><input type="date" value={f.end_date} onChange={(e) => set("end_date", e.target.value)} /></Field>
        <Field label="Fiscal year"><input value={f.fiscal_year} onChange={(e) => set("fiscal_year", e.target.value)} placeholder="FY2026-27" /></Field>
      </div>
    </Modal>
  );
}

function DeriveMonthly({ period, onClose }: { period: Period; onClose: () => void }) {
  const { companyId, periods } = useApp();
  const qc = useQueryClient();
  const toast = useToast();
  const ytd = periods.filter((p) => p.basis === "YTD" && p.id !== period.id && p.scenario === period.scenario);
  const [cur, setCur] = useState<number | "">("");
  const [prev, setPrev] = useState<number | "">("");
  const go = async () => {
    try {
      await api.post(`/api/companies/${companyId}/periods/${period.id}/derive-monthly?ytd_current_id=${cur}${prev ? `&ytd_previous_id=${prev}` : ""}`);
      toast("Monthly TBs derived (current YTD − previous YTD)");
      qc.invalidateQueries({ queryKey: ["periods", companyId] });
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <Modal title={`Derive ${period.label} from YTD`} onClose={onClose} footer={<><button onClick={onClose}>Cancel</button><button className="primary" disabled={!cur} onClick={go}>Derive</button></>}>
      <p className="muted">Monthly TB = YTD of this month − YTD of the previous month, for both the business-area and cost-centre TBs.</p>
      <div className="form-grid">
        <Field label="YTD up to this month"><select value={cur} onChange={(e) => setCur(Number(e.target.value))}><option value="">—</option>{ytd.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></Field>
        <Field label="YTD up to previous month (empty for the first month)"><select value={prev} onChange={(e) => setPrev(e.target.value ? Number(e.target.value) : "")}><option value="">—</option>{ytd.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></Field>
      </div>
    </Modal>
  );
}

export default function Periods() {
  const { periods, period, setPeriodId, companyId } = useApp();
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const [deriving, setDeriving] = useState(false);
  const uploads = useQuery({
    queryKey: ["uploads", period?.id],
    queryFn: () => api.get<UploadInfo[]>(`/api/companies/${companyId}/periods/${period!.id}/uploads`),
    enabled: !!period,
  });
  const lock = async (action: "lock" | "unlock") => {
    try {
      await api.post(`/api/companies/${companyId}/periods/${period!.id}/${action}`);
      qc.invalidateQueries({ queryKey: ["periods", companyId] });
      toast(`Period ${action}ed`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Periods & uploads</h1>
          <p>Upload the business-area TB (GL × business areas), the cost-centre TB (GL × cost centres, for PRE) and optionally the MIS figures. Each upload is validated against the masters; columns are matched by header name.</p>
        </div>
        {can("edit") ? <button className="primary" onClick={() => setCreating(true)}>+ New period</button> : null}
      </div>
      <div className="grid" style={{ gridTemplateColumns: "minmax(260px, 340px) 1fr" }}>
        <div className="card flush">
          <table className="data">
            <thead><tr><th>Period</th><th>Scenario</th><th>Status</th></tr></thead>
            <tbody>
              {periods.map((p) => (
                <tr key={p.id} className="clickable" onClick={() => setPeriodId(p.id)} style={p.id === period?.id ? { background: "var(--accent-bg)" } : undefined}>
                  <td><strong>{p.label || p.code}</strong><div className="muted small">{p.code} · {p.basis} · ends {p.end_date}</div></td>
                  <td>{p.scenario}</td>
                  <td><Badge status={p.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!periods.length ? <div className="empty">No periods yet.</div> : null}
        </div>
        {period ? (
          <div className="stack">
            <div className="card row">
              <div>
                <h2 style={{ margin: 0 }}>{period.label || period.code}</h2>
                <div className="muted small">{period.scenario} · {period.basis} · {period.start_date ?? "?"} → {period.end_date} · {period.months} month(s)</div>
              </div>
              <span className="right" />
              {period.basis === "MONTH" && can("edit") ? <button onClick={() => setDeriving(true)}>Derive from YTD…</button> : null}
              {period.status === "open" && can("approve") ? <button onClick={() => lock("lock")}>🔒 Lock period</button> : null}
              {period.status === "locked" && can("admin") ? <button onClick={() => lock("unlock")}>Unlock</button> : null}
              <Link className="btn primary" to="/runs">Calculate →</Link>
            </div>
            {period.status === "locked" ? <div className="card" style={{ background: "var(--bad-bg)" }}>This period is locked: uploads, adjustments and calculations are blocked.</div> : null}
            <div className="grid cols-3">
              <UploadPanel period={period} kind="BA_TB" title="Business-area TB" hint="GL rows × business-area columns. Used for all Non-PRE amounts." />
              <UploadPanel period={period} kind="CC_TB" title="Cost-centre TB" hint="GL rows × cost-centre columns. Used for PRE (wages, salaries, welfare)." />
              <UploadPanel period={period} kind="MIS" title="MIS figures" hint="Columns: Particulars, MIS, optional Formula / Compare. Used by the MIS check report." />
            </div>
            <div className="card flush">
              <div className="card-head" style={{ padding: "16px 16px 0" }}><h2>Upload history</h2></div>
              {uploads.isLoading ? <Loading /> : <ErrorBox error={uploads.error} />}
              <table className="data">
                <thead><tr><th>When</th><th>Kind</th><th>File</th><th>Status</th><th className="num">Issues</th><th>Active</th></tr></thead>
                <tbody>
                  {(uploads.data ?? []).map((u) => (
                    <tr key={u.id}>
                      <td>{dateTime(u.created_at)}</td><td>{u.kind}</td><td>{u.filename}</td>
                      <td><Badge status={u.status} /></td><td className="num">{u.issue_count ?? 0}</td><td>{u.is_active ? "✓" : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : <div className="card empty">Create a period to start.</div>}
      </div>
      {creating ? <NewPeriod onClose={() => setCreating(false)} /> : null}
      {deriving && period ? <DeriveMonthly period={period} onClose={() => setDeriving(false)} /> : null}
    </>
  );
}
