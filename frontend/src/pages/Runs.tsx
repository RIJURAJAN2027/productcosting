import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, qs } from "../api";
import { useApp, useAuth, useToast } from "../context";
import { dateTime, UNIT_LABEL } from "../format";
import type { Run } from "../types";
import { Badge, ControlList, ErrorBox, Loading, Modal } from "../components/ui";

const UNITS: Record<string, number> = { rupees: 1, thousand: 1e3, lakh: 1e5, million: 1e6, crore: 1e7 };

function Workflow({ run }: { run: Run }) {
  const { can, me } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [action, setAction] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const go = async () => {
    try {
      await api.post(`/api/runs/${run.id}/transition`, { action, comment: comment || null });
      toast(`Run ${action === "submit" ? "submitted" : action + "d"}`);
      qc.invalidateQueries({ queryKey: ["run", run.id] });
      qc.invalidateQueries({ queryKey: ["runs"] });
      setAction(null);
      setComment("");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const isMaker = me && run.submitted_by && (run.submitted_by === me.name || run.submitted_by === me.email);
  const buttons: { a: string; label: string; show: boolean; primary?: boolean }[] = [
    { a: "submit", label: "Submit for approval", show: can("edit") && ["draft", "rejected"].includes(run.status), primary: true },
    { a: "approve", label: "Approve", show: can("approve") && run.status === "submitted" && !isMaker, primary: true },
    { a: "reject", label: "Reject", show: can("approve") && ["submitted", "approved"].includes(run.status) && !isMaker },
    { a: "publish", label: "Publish", show: can("approve") && run.status === "approved" && !isMaker, primary: true },
  ];
  return (
    <div className="row">
      {buttons.filter((b) => b.show).map((b) => (
        <button key={b.a} className={b.primary ? "primary" : b.a === "reject" ? "danger" : ""} onClick={() => setAction(b.a)}>{b.label}</button>
      ))}
      {isMaker && run.status === "submitted" ? <span className="muted small">Waiting for another approver (maker-checker).</span> : null}
      {action ? (
        <Modal title={`${action[0].toUpperCase()}${action.slice(1)} run #${run.id}`} onClose={() => setAction(null)}
          footer={<><button onClick={() => setAction(null)}>Cancel</button><button className="primary" onClick={go}>Confirm</button></>}>
          {action === "submit" && run.control_summary.errors ? (
            <div style={{ color: "var(--bad)" }}>This run has {run.control_summary.errors} control error(s); explain them in the comment.</div>
          ) : null}
          <label className="field"><span>Comment</span><textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} /></label>
        </Modal>
      ) : null}
    </div>
  );
}

function RunDetail({ runId }: { runId: number }) {
  const { unit, company } = useApp();
  const q = useQuery({ queryKey: ["run", runId], queryFn: () => api.get<Run>(`/api/runs/${runId}`) });
  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data) return <Loading />;
  const run = q.data;
  const div = (run.calc_unit_divisor ?? company?.settings.calc_unit_divisor ?? 1e7) / UNITS[unit];
  const steps: [string, string | null, string | null][] = [
    ["Calculated", run.started_by, run.created_at],
    ["Submitted", run.submitted_by, run.submitted_at],
    [run.status === "rejected" ? "Rejected" : "Approved", run.approved_by, run.approved_at],
  ];
  return (
    <div className="stack">
      <div className="card stack">
        <div className="row">
          <h2 style={{ margin: 0 }}>Run #{run.id} · {run.period_label}</h2>
          <Badge status={run.status} />
          <span className="right" />
          <Link className="btn" to={`/reports${qs({ run: run.id })}`}>Reports</Link>
          <Workflow run={run} />
        </div>
        <div className="row small muted">
          {steps.map(([what, who, when]) => (who || when) ? <span key={what}>{what}: <strong>{who ?? "—"}</strong> {dateTime(when)} · </span> : null)}
          <span>calculation {run.timings?.total ? `${(run.timings.total * 1000).toFixed(0)} ms` : ""} · stored {run.timings?.stored_values ?? 0} values</span>
        </div>
        {run.note ? <div className="small">Note: {run.note}</div> : null}
        {run.review_comment ? <div className="small">Review comment: <em>{run.review_comment}</em></div> : null}
        {run.error ? <div style={{ color: "var(--bad)" }}>{run.error}</div> : null}
      </div>
      <div className="card">
        <div className="card-head">
          <h2>Controls</h2>
          <span className="muted small">Total in = total out, tie-out to the TB, unmapped amounts, PBT coverage · amounts in {UNIT_LABEL[unit]}</span>
        </div>
        <ControlList controls={run.controls ?? []} unitDiv={div} unitLabel={UNIT_LABEL[unit]} />
      </div>
      {run.inputs ? (
        <div className="card small">
          <h3>Inputs used</h3>
          <div className="muted">BA TB upload #{String((run.inputs as Record<string, unknown>).ba_upload_id)} · CC TB upload #{String((run.inputs as Record<string, unknown>).cc_upload_id ?? "—")} ·{" "}
            {((run.inputs as Record<string, unknown[]>).adjustments ?? []).length} adjustment rule(s) ·{" "}
            {Object.keys((run.inputs as Record<string, object>).mc_adjustments ?? {}).length} MC adjustment(s)</div>
        </div>
      ) : null}
    </div>
  );
}

export default function Runs() {
  const { runId } = useParams();
  const nav = useNavigate();
  const { companyId, period } = useApp();
  const { can } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const runs = useQuery({
    queryKey: ["runs", companyId],
    queryFn: () => api.get<Run[]>(`/api/companies/${companyId}/runs`),
    enabled: companyId !== null,
  });
  const calculate = async () => {
    if (!period) return;
    setBusy(true);
    try {
      const r = await api.post<Run>(`/api/companies/${companyId}/periods/${period.id}/runs`, { note: note || null });
      toast(`Calculated in ${r.duration_s}s – ${r.control_summary.errors ?? 0} error(s), ${r.control_summary.warnings ?? 0} warning(s)`);
      qc.invalidateQueries({ queryKey: ["runs", companyId] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      setNote("");
      nav(`/runs/${r.id}`);
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
          <h1>Calculations</h1>
          <p>Each calculation stores a snapshot of the masters and inputs it used, so results are reproducible and auditable. Publish requires a second person (maker-checker).</p>
        </div>
      </div>
      {can("edit") && period ? (
        <div className="card row">
          <strong>Calculate {period.label || period.code}</strong>
          {period.status === "locked" ? <Badge status="locked">locked</Badge> : null}
          <input style={{ flex: 1, minWidth: 200 }} placeholder="Optional note (e.g. after MC adjustments)" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="primary" disabled={busy || period.status === "locked" || !period.uploads.BA_TB} onClick={calculate}>
            {busy ? "Calculating…" : "▶ Run calculation"}
          </button>
          {!period.uploads.BA_TB ? <span className="muted small">Upload the business-area TB first.</span> : null}
        </div>
      ) : null}
      <div className="grid" style={{ gridTemplateColumns: "minmax(300px, 420px) 1fr", alignItems: "start" }}>
        <div className="card flush">
          {runs.isLoading ? <Loading /> : <ErrorBox error={runs.error} />}
          <table className="data">
            <thead><tr><th>Run</th><th>Period</th><th>Status</th><th>Controls</th></tr></thead>
            <tbody>
              {(runs.data ?? []).map((r) => (
                <tr key={r.id} className="clickable" onClick={() => nav(`/runs/${r.id}`)} style={String(r.id) === runId ? { background: "var(--accent-bg)" } : undefined}>
                  <td>#{r.id}<div className="muted small">{dateTime(r.created_at)}</div></td>
                  <td>{r.period_label}<div className="muted small">{r.scenario}</div></td>
                  <td><Badge status={r.status} /></td>
                  <td className="small">
                    {r.control_summary.errors ? <Badge status="error">{r.control_summary.errors}</Badge> : null}{" "}
                    {r.control_summary.warnings ? <Badge status="warning">{r.control_summary.warnings}</Badge> : null}{" "}
                    {!r.control_summary.errors && !r.control_summary.warnings && r.status !== "failed" ? <Badge status="ok">ok</Badge> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {runId ? <RunDetail runId={Number(runId)} /> : <div className="card empty">Select a run to see its controls and approval status.</div>}
      </div>
    </>
  );
}
