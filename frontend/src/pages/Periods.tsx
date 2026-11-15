import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useApp, useAuth, useToast } from "../context";
import { dateTime, fmt } from "../format";
import type { Period, Run } from "../types";
import { Badge, Field, Modal } from "../components/ui";
import { Icon } from "../components/icons";

interface Issue { level: string; type: string; message: string; code?: string; amount?: number; description?: string }
interface UploadInfo {
  id: number; kind: string; filename: string; status: string; is_active: boolean; created_at: string | null;
  summary: Record<string, unknown>; issues: Issue[] | null; issue_count?: number;
}
type Kind = "BA_TB" | "CC_TB";

const money = (v: number) => `${v < 0 ? "−" : ""}₹${fmt(Math.abs(v), 2)}`;

const TYPE_LABEL: Record<string, string> = {
  new_gl: "GL not in the master", new_ba: "Business area not in the master", new_cc: "Cost centre not in the master",
  row_total_mismatch: "Row total ≠ sum of columns", duplicate_gl: "GL appears twice", non_numeric: "Text in an amount cell",
  duplicate_column: "Column appears twice",
};

/** Only real problems: errors and warnings that carry an amount or a message worth acting on. */
function realProblems(issues: Issue[] | null | undefined): Issue[] {
  return (issues ?? []).filter((i) => {
    if (i.level !== "error" && i.level !== "warning") return false;
    if (i.type.startsWith("new_") && Math.abs(i.amount ?? 0) < 0.005) return false;
    return true;
  });
}

function Problems({ issues }: { issues: Issue[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!issues.length) {
    return <div className="tb-ok"><Icon name="check" size={16} />No problems found</div>;
  }
  const groups = Object.entries(issues.reduce<Record<string, Issue[]>>((g, i) => { (g[i.type] = g[i.type] || []).push(i); return g; }, {}));
  return (
    <div className="tb-problems">
      {groups.map(([type, list]) => {
        const err = list.some((i) => i.level === "error");
        const total = list.reduce((t, i) => t + (i.amount ?? 0), 0);
        const isOpen = open === type;
        return (
          <div key={type} className={`tb-problem ${err ? "error" : "warning"}`}>
            <button className="tb-problem-head" onClick={() => setOpen(isOpen ? null : type)} aria-expanded={isOpen}>
              <Icon name="alert" size={15} />
              <strong>{list.length} × {TYPE_LABEL[type] ?? type}</strong>
              {list.some((i) => i.amount !== undefined) ? <span className="num-font">{money(total)}</span> : null}
              <Icon name="chevron" size={14} className={isOpen ? "rot" : ""} />
            </button>
            {isOpen ? (
              <ul>
                {list.slice(0, 200).map((i, n) => (
                  <li key={n}>
                    <span>{i.code ? <code>{i.code}</code> : null} {i.description ?? (i.code ? "" : i.message)}</span>
                    {i.amount !== undefined ? <span className="num-font">{money(i.amount)}</span> : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function UploadCard({ period, kind, title }: { period: Period; kind: Kind; title: string }) {
  const { companyId } = useApp();
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const current = period.uploads[kind];
  const detail = useQuery({
    queryKey: ["upload-detail", current?.id],
    queryFn: () => api.get<UploadInfo>(`/api/companies/${companyId}/uploads/${current!.id}`),
    enabled: !!current,
  });
  const editable = can("edit") && period.status !== "locked";
  const upload = async (f: File) => {
    setBusy(true);
    try {
      const r = await api.upload<UploadInfo>(`/api/companies/${companyId}/periods/${period.id}/uploads`, { kind, file: f });
      const n = realProblems(r.issues).length;
      toast(n ? `${title}: uploaded with ${n} problem${n === 1 ? "" : "s"}` : `${title}: uploaded, no problems`, n ? "error" : "info");
      qc.invalidateQueries({ queryKey: ["periods", companyId] });
      qc.invalidateQueries({ queryKey: ["uploads", period.id] });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const problems = realProblems(detail.data?.issues);
  const state = !current ? "empty" : detail.isLoading ? "loading" : problems.some((i) => i.level === "error") ? "error" : problems.length ? "warning" : "ok";
  return (
    <section className={`card tb-card ${state}`}>
      <div className="tb-head">
        <span className="tb-icon"><Icon name="sheet" size={20} /></span>
        <h2>{title}</h2>
        <span className={`tb-state ${state}`}>
          {state === "empty" ? "Not uploaded" : state === "ok" ? "Ready" : state === "loading" ? "Checking…" : `${problems.length} problem${problems.length === 1 ? "" : "s"}`}
        </span>
      </div>

      {current ? (
        <div className="tb-file">
          <Icon name="reports" size={18} />
          <div className="grow">
            <strong>{current.filename}</strong>
            <small>{dateTime(current.uploaded_at)}</small>
          </div>
          <button className="small ghost" onClick={() => api.download(`/api/companies/${companyId}/uploads/${current.id}/download`)} title="Download this file">
            <Icon name="download" size={15} />
          </button>
        </div>
      ) : null}

      {editable ? (
        <div className={`tb-drop ${drag ? "drag" : ""} ${busy ? "busy" : ""}`}
          onClick={() => !busy && input.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files?.[0]; if (f && !busy) upload(f); }}
          role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && input.current?.click()}>
          <input ref={input} type="file" hidden accept=".xlsx,.xlsm,.csv" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
          <span className="tb-drop-icon"><Icon name="upload" size={22} /></span>
          {busy ? <strong>Uploading and checking…</strong> : (
            <>
              <strong>{current ? "Replace file" : "Upload file"}</strong>
              <span>Drag the Excel here or <u>browse</u></span>
              <small>.xlsx · .xlsm · .csv</small>
            </>
          )}
        </div>
      ) : null}

      {current && !detail.isLoading ? <Problems issues={problems} /> : null}
    </section>
  );
}

function NewPeriod({ onClose }: { onClose: () => void }) {
  const { companyId, setPeriodId } = useApp();
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ code: "", label: "", scenario: "actual", basis: "YTD", start_date: "", end_date: "", fiscal_year: "" });
  const save = async () => {
    try {
      const p = await api.post<Period>(`/api/companies/${companyId}/periods`, { ...f, start_date: f.start_date || null, fiscal_year: f.fiscal_year || null });
      qc.invalidateQueries({ queryKey: ["periods", companyId] });
      setPeriodId(p.id);
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
        <Field label="Code"><input value={f.code} onChange={(e) => set("code", e.target.value)} placeholder="2026-10" /></Field>
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
  const nav = useNavigate();
  const [creating, setCreating] = useState(false);
  const [calculating, setCalculating] = useState(false);
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
      toast(action === "lock" ? "Period locked" : "Period unlocked");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const calculate = async () => {
    if (!period) return;
    setCalculating(true);
    try {
      const r = await api.post<Run>(`/api/companies/${companyId}/periods/${period.id}/runs`, {});
      const e = r.control_summary.errors ?? 0;
      const w = r.control_summary.warnings ?? 0;
      toast(e || w ? `Calculated – ${e} error(s), ${w} warning(s) in the controls` : "Calculated – all controls passed", e ? "error" : "info");
      qc.invalidateQueries({ queryKey: ["runs", companyId] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      nav(`/runs/${r.id}`);
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setCalculating(false);
    }
  };

  const ba = period?.uploads.BA_TB;
  const cc = period?.uploads.CC_TB;
  const locked = period?.status === "locked";
  return (
    <>
      <div className="page-head">
        <h1>Trial balances</h1>
        {can("edit") ? <button onClick={() => setCreating(true)}><Icon name="plus" size={15} />New period</button> : null}
      </div>

      {periods.length ? (
        <div className="period-pills" role="tablist" aria-label="Period">
          {periods.map((p) => (
            <button key={p.id} role="tab" aria-selected={p.id === period?.id} className={`period-pill ${p.id === period?.id ? "on" : ""}`} onClick={() => setPeriodId(p.id)}>
              <strong>{p.label || p.code}</strong>
              <small>{p.scenario !== "actual" ? `${p.scenario} · ` : ""}{p.uploads.BA_TB && p.uploads.CC_TB ? "2 of 2 files" : `${(p.uploads.BA_TB ? 1 : 0) + (p.uploads.CC_TB ? 1 : 0)} of 2 files`}{p.status === "locked" ? " · locked" : ""}</small>
            </button>
          ))}
        </div>
      ) : <div className="card empty">Create a period to start.</div>}

      {period ? (
        <>
          {locked ? <div className="alert warning"><Icon name="alert" size={18} /><div className="grow"><strong>This period is locked.</strong></div>{can("admin") ? <button className="small" onClick={() => lock("unlock")}>Unlock</button> : null}</div> : null}

          <div className="tb-grid">
            <UploadCard period={period} kind="BA_TB" title="Business-area TB" />
            <UploadCard period={period} kind="CC_TB" title="Cost-centre TB" />
          </div>

          <section className="calc-bar card">
            <div className="calc-steps">
              <span className={ba ? "done" : ""}><Icon name={ba ? "check" : "upload"} size={15} />Business-area TB</span>
              <span className={cc ? "done" : ""}><Icon name={cc ? "check" : "upload"} size={15} />Cost-centre TB</span>
            </div>
            <span className="grow" />
            {period.basis === "MONTH" && can("edit") && !locked ? <button className="ghost small" onClick={() => setDeriving(true)}>Derive from YTD</button> : null}
            {period.status === "open" && can("approve") ? <button className="ghost small" onClick={() => lock("lock")}>Lock period</button> : null}
            {can("edit") ? (
              <button className="primary calc-btn" disabled={calculating || locked || !ba} onClick={calculate}>
                <Icon name="play" size={16} />{calculating ? "Calculating…" : `Calculate ${period.label || period.code}`}
              </button>
            ) : null}
          </section>

          {(uploads.data ?? []).length ? (
            <details className="card history">
              <summary><strong>Upload history</strong> <span className="muted small">{uploads.data!.length}</span></summary>
              <table className="data">
                <thead><tr><th>When</th><th>File</th><th>Type</th><th>Status</th><th /></tr></thead>
                <tbody>
                  {uploads.data!.map((u) => (
                    <tr key={u.id}>
                      <td>{dateTime(u.created_at)}</td><td>{u.filename}</td><td>{u.kind === "BA_TB" ? "Business-area TB" : u.kind === "CC_TB" ? "Cost-centre TB" : u.kind}</td>
                      <td><Badge status={u.status} /></td><td>{u.is_active ? <span className="badge ok">in use</span> : null}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          ) : null}
        </>
      ) : null}
      {creating ? <NewPeriod onClose={() => setCreating(false)} /> : null}
      {deriving && period ? <DeriveMonthly period={period} onClose={() => setDeriving(false)} /> : null}
    </>
  );
}
