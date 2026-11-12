import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, qs } from "../api";
import { useApp } from "../context";
import { dateTime } from "../format";
import type { AuditEntry } from "../types";
import { ErrorBox, Loading } from "../components/ui";

const ENTITIES = ["", "run", "adjustment", "mc_adjustment", "driver", "mis", "mis_line", "upload", "period", "company", "user", "tenant",
  "plants", "product-groups", "business-areas", "gl-accounts", "cost-centres", "categories", "pre-functions",
  "allocation-rules", "pnl-lines", "masters", "report_template"];

function summary(d: Record<string, unknown>): string {
  if (!d) return "";
  if (d.changes && typeof d.changes === "object") {
    return Object.entries(d.changes as Record<string, { from: unknown; to: unknown }>)
      .map(([k, v]) => `${k}: ${JSON.stringify(v?.from)} → ${JSON.stringify(v?.to)}`).join(" · ");
  }
  const keys = Object.keys(d).filter((k) => k !== "values");
  return keys.map((k) => `${k}: ${typeof d[k] === "object" ? JSON.stringify(d[k]) : String(d[k])}`).join(" · ");
}

export default function Audit() {
  const { companyId } = useApp();
  const [scope, setScope] = useState<"company" | "all">("company");
  const [entity, setEntity] = useState("");
  const [action, setAction] = useState("");
  const [user, setUser] = useState("");
  const [since, setSince] = useState("");
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const size = 100;
  const q = useQuery({
    queryKey: ["audit", scope, companyId, entity, action, user, since, page],
    queryFn: () => api.get<AuditEntry[]>(`/api/audit${qs({ company_id: scope === "company" ? companyId : undefined, entity, action, user_email: user, since, limit: size, offset: page * size })}`),
  });
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Audit trail</h1>
          <p>Every change to masters, inputs, adjustments, calculations, approvals, users and settings – who, when and what changed. Entries cannot be edited or deleted from the application.</p>
        </div>
      </div>
      <div className="card row">
        <select aria-label="Scope" value={scope} onChange={(e) => { setScope(e.target.value as "company" | "all"); setPage(0); }}>
          <option value="company">This company</option>
          <option value="all">All companies & organisation</option>
        </select>
        <select aria-label="Entity" value={entity} onChange={(e) => { setEntity(e.target.value); setPage(0); }}>
          {ENTITIES.map((x) => <option key={x} value={x}>{x || "All entities"}</option>)}
        </select>
        <input placeholder="Action (e.g. update, publish)" value={action} onChange={(e) => { setAction(e.target.value.trim()); setPage(0); }} />
        <input placeholder="User email contains…" value={user} onChange={(e) => { setUser(e.target.value); setPage(0); }} />
        <label className="row small">Since <input type="date" value={since} onChange={(e) => { setSince(e.target.value); setPage(0); }} /></label>
      </div>
      <div className="card flush">
        {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
        <div className="table-wrap" style={{ maxHeight: "calc(100vh - 290px)" }}>
          <table className="data">
            <thead><tr><th>When</th><th>User</th><th>Action</th><th>Entity</th><th>ID</th><th>Details</th></tr></thead>
            <tbody>
              {(q.data ?? []).map((r) => (
                <tr key={r.id} className="clickable" onClick={() => setOpen(open === r.id ? null : r.id)}>
                  <td style={{ whiteSpace: "nowrap" }}>{dateTime(r.created_at)}</td>
                  <td className="small">{r.user_email ?? "system"}</td>
                  <td><strong>{r.action}</strong></td>
                  <td>{r.entity}</td>
                  <td className="mono">{r.entity_id}</td>
                  <td className="small" style={{ maxWidth: 640 }}>
                    {open === r.id ? <pre className="mono" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{JSON.stringify(r.details, null, 2)}</pre>
                      : <span style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{summary(r.details)}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!q.isLoading && !(q.data ?? []).length ? <div className="empty">No entries.</div> : null}
        </div>
        <div className="row" style={{ padding: 10 }}>
          <button disabled={page === 0} onClick={() => setPage(page - 1)}>← Newer</button>
          <span className="muted small">Page {page + 1}</span>
          <button disabled={(q.data ?? []).length < size} onClick={() => setPage(page + 1)}>Older →</button>
        </div>
      </div>
    </>
  );
}
