import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useApp, useAuth, useToast } from "../context";
import { dateTime, fmt, UNIT_LABEL } from "../format";
import type { Company } from "../types";
import { Badge, ErrorBox, Field, FileDrop, Loading, Modal, Tabs } from "../components/ui";

interface UserRow { id: number; email: string; name: string; role: string; is_active: boolean; company_ids: number[] | null; auth_provider: string; last_login: string | null; locked: boolean }
interface Tenant { id: number; name: string; slug: string; license_plan: string; license_max_companies: number; license_max_users: number; license_expires: string | null; usage: { users: number; companies: number }; settings: Record<string, unknown> }
const ROLES = [
  ["admin", "Admin – everything incl. users, settings, backup"],
  ["analyst", "Costing analyst – uploads, masters, adjustments, calculations"],
  ["approver", "Approver – review, approve, publish, lock periods"],
  ["viewer", "Viewer – read-only reports"],
];

function UserEditor({ user, companies, onClose }: { user: UserRow | null; companies: Company[]; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [f, setF] = useState({ email: user?.email ?? "", name: user?.name ?? "", role: user?.role ?? "viewer", password: "",
    is_active: user?.is_active ?? true, auth_provider: user?.auth_provider ?? "local" });
  const [all, setAll] = useState(user ? user.company_ids === null : true);
  const [ids, setIds] = useState<number[]>(user?.company_ids ?? []);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    try {
      const company_ids = all ? null : ids;
      if (user) {
        await api.patch(`/api/users/${user.id}`, { name: f.name, role: f.role, is_active: f.is_active, company_ids, ...(f.password ? { password: f.password } : {}) });
      } else {
        await api.post("/api/users", { ...f, password: f.password || null, company_ids });
      }
      toast(user ? "User updated" : "User created");
      qc.invalidateQueries({ queryKey: ["users"] });
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <Modal title={user ? `Edit ${user.email}` : "New user"} onClose={onClose}
      footer={<><button onClick={onClose}>Cancel</button><button className="primary" onClick={save}>Save</button></>}>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
        <Field label="Email"><input type="email" value={f.email} disabled={!!user} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        <Field label="Name"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Role">
          <select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{ROLES.map(([r, l]) => <option key={r} value={r}>{l}</option>)}</select>
        </Field>
        {!user ? (
          <Field label="Sign-in">
            <select value={f.auth_provider} onChange={(e) => setF({ ...f, auth_provider: e.target.value })}>
              <option value="local">Email & password</option><option value="oidc">Single sign-on (OIDC) only</option>
            </select>
          </Field>
        ) : null}
        {f.auth_provider === "local" ? (
          <Field label={user ? "Reset password (optional)" : "Initial password"} hint="At least 10 characters with upper- and lower-case letters and a digit.">
            <input type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
          </Field>
        ) : null}
      </div>
      <label className="row small"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> Active</label>
      <div className="stack">
        <label className="row small"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Access to all companies</label>
        {!all ? (
          <div className="row">
            {companies.map((c) => (
              <label key={c.id} className="row small">
                <input type="checkbox" checked={ids.includes(c.id)} onChange={(e) => setIds(e.target.checked ? [...ids, c.id] : ids.filter((x) => x !== c.id))} /> {c.name}
              </label>
            ))}
          </div>
        ) : null}
      </div>
      {err ? <div style={{ color: "var(--bad)" }}>{err}</div> : null}
    </Modal>
  );
}

function Users() {
  const { me } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["users"], queryFn: () => api.get<UserRow[]>("/api/users") });
  const companies = useQuery({ queryKey: ["companies"], queryFn: () => api.get<Company[]>("/api/companies") });
  const [edit, setEdit] = useState<UserRow | null | undefined>(undefined);
  const unlock = async (u: UserRow) => {
    try {
      await api.patch(`/api/users/${u.id}`, { unlock: true });
      qc.invalidateQueries({ queryKey: ["users"] });
      toast("Unlocked");
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const name = (id: number) => companies.data?.find((c) => c.id === id)?.code ?? id;
  return (
    <div className="stack" style={{ padding: 12 }}>
      <div className="row">
        <div className="muted small">Roles: admin, costing analyst, approver and viewer. A calculation must be approved and published by someone other than the person who submitted it.</div>
        <span className="right" />
        <button className="primary" onClick={() => setEdit(null)}>+ New user</button>
      </div>
      {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
      <table className="data">
        <thead><tr><th>User</th><th>Role</th><th>Companies</th><th>Sign-in</th><th>Last login</th><th>Status</th><th /></tr></thead>
        <tbody>
          {(q.data ?? []).map((u) => (
            <tr key={u.id} className="clickable" onClick={() => setEdit(u)}>
              <td><strong>{u.name || u.email}</strong><div className="muted small">{u.email}</div></td>
              <td>{u.role}</td>
              <td className="small">{u.company_ids === null ? "all" : u.company_ids.map(name).join(", ") || "none"}</td>
              <td className="small">{u.auth_provider === "oidc" ? "SSO" : "password"}</td>
              <td className="small">{dateTime(u.last_login)}</td>
              <td>
                {!u.is_active ? <Badge status="draft">inactive</Badge> : u.locked ? <Badge status="locked">locked out</Badge> : <Badge status="ok">active</Badge>}
                {u.id === me?.id ? <span className="muted small"> (you)</span> : null}
              </td>
              <td onClick={(e) => e.stopPropagation()}>{u.locked ? <button className="small" onClick={() => unlock(u)}>Unlock</button> : null}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {edit !== undefined ? <UserEditor user={edit} companies={companies.data ?? []} onClose={() => setEdit(undefined)} /> : null}
    </div>
  );
}

function CompanySettings() {
  const { company, companyId } = useApp();
  const { refresh } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const [f, setF] = useState<Record<string, string>>({});
  const [meta, setMeta] = useState("");
  const [creating, setCreating] = useState(false);
  const [nc, setNc] = useState({ code: "", name: "", currency: "INR" });
  useEffect(() => {
    if (!company) return;
    const s = company.settings;
    setF({ name: company.name, currency: company.currency, display_unit: s.display_unit, number_format: s.number_format,
      decimals: String(s.decimals), mis_tolerance: String(s.mis_tolerance), fiscal_year_start_month: String(s.fiscal_year_start_month),
      calc_unit_divisor: String(s.calc_unit_divisor), ignore_columns: (s.ignore_columns ?? []).join(", ") });
    setMeta(JSON.stringify(s.meta ?? {}, null, 2));
  }, [company]);
  const save = async () => {
    try {
      await api.patch(`/api/companies/${companyId}`, {
        name: f.name, currency: f.currency,
        settings: { display_unit: f.display_unit, number_format: f.number_format, decimals: Number(f.decimals), mis_tolerance: Number(f.mis_tolerance),
          fiscal_year_start_month: Number(f.fiscal_year_start_month), calc_unit_divisor: Number(f.calc_unit_divisor),
          ignore_columns: f.ignore_columns.split(",").map((x) => x.trim()).filter(Boolean), meta: JSON.parse(meta || "{}") },
      });
      toast("Settings saved");
      qc.invalidateQueries({ queryKey: ["company", companyId] });
      qc.invalidateQueries({ queryKey: ["companies"] });
      refresh();
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const create = async () => {
    try {
      await api.post("/api/companies", nc);
      toast(`Company ${nc.code} created – set up its masters (import a workbook or the masters Excel)`);
      setCreating(false);
      refresh();
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const remove = async () => {
    const code = prompt(`Type the company code ${company?.code} to delete it with all its periods, uploads and runs. This cannot be undone – take a backup first.`);
    if (code !== company?.code) return;
    try {
      await api.del(`/api/companies/${companyId}`);
      toast("Company deleted");
      refresh();
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  if (!company) return <Loading />;
  return (
    <div className="stack" style={{ padding: 12 }}>
      <div className="row">
        <strong>{company.name}</strong><span className="muted">({company.code})</span>{company.is_demo ? <Badge status="draft">demo</Badge> : null}
        <span className="right" />
        <button onClick={() => setCreating(true)}>+ New company</button>
        <button className="danger" onClick={remove}>Delete company…</button>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
        <Field label="Name"><input value={f.name ?? ""} onChange={set("name")} /></Field>
        <Field label="Currency"><input value={f.currency ?? ""} onChange={set("currency")} /></Field>
        <Field label="Default display unit">
          <select value={f.display_unit} onChange={set("display_unit")}>{Object.entries(UNIT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        </Field>
        <Field label="Number format">
          <select value={f.number_format} onChange={set("number_format")}><option value="indian">Indian (1,00,000.00)</option><option value="international">International (100,000.00)</option></select>
        </Field>
        <Field label="Decimals"><input type="number" min={0} max={6} value={f.decimals ?? ""} onChange={set("decimals")} /></Field>
        <Field label="MIS tolerance (₹)" hint="Differences up to this amount count as matching."><input type="number" value={f.mis_tolerance ?? ""} onChange={set("mis_tolerance")} /></Field>
        <Field label="Fiscal year starts in month"><input type="number" min={1} max={12} value={f.fiscal_year_start_month ?? ""} onChange={set("fiscal_year_start_month")} /></Field>
        <Field label="Calculation unit divisor" hint="Unit used inside the engine and for stored formula constants (1,00,00,000 = crore). Change only with care."><input type="number" value={f.calc_unit_divisor ?? ""} onChange={set("calc_unit_divisor")} /></Field>
      </div>
      <Field label="TB columns to ignore on import" hint="Comma-separated header names of helper / total columns in the trial balances."><input value={f.ignore_columns ?? ""} onChange={set("ignore_columns")} /></Field>
      <Field label="Key lines (meta, JSON)" hint='Which P&L lines are sales, material cost, PBT … e.g. {"sales_line": "sales_total", "material_cost_line": "material_cost", "mc_tb_line": "material_cost_tb", "pbt_line": "pbt"}'>
        <textarea rows={6} value={meta} onChange={(e) => setMeta(e.target.value)} spellCheck={false} />
      </Field>
      <div><button className="primary" onClick={save}>Save settings</button></div>
      {creating ? (
        <Modal title="New company" onClose={() => setCreating(false)} footer={<><button onClick={() => setCreating(false)}>Cancel</button><button className="primary" disabled={!nc.code || !nc.name} onClick={create}>Create</button></>}>
          <Field label="Code"><input value={nc.code} onChange={(e) => setNc({ ...nc, code: e.target.value })} /></Field>
          <Field label="Name"><input value={nc.name} onChange={(e) => setNc({ ...nc, name: e.target.value })} /></Field>
          <Field label="Currency"><input value={nc.currency} onChange={(e) => setNc({ ...nc, currency: e.target.value })} /></Field>
        </Modal>
      ) : null}
    </div>
  );
}

function Organisation() {
  const { me } = useAuth();
  const toast = useToast();
  const q = useQuery({ queryKey: ["tenant"], queryFn: () => api.get<Tenant>("/api/tenant") });
  const sso = useQuery({ queryKey: ["sso"], queryFn: () => api.get<{ enabled: boolean; issuer: string | null }>("/api/auth/sso/config") });
  const [name, setName] = useState("");
  const [lic, setLic] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!q.data) return;
    setName(q.data.name);
    setLic({ license_plan: q.data.license_plan, license_max_companies: String(q.data.license_max_companies), license_max_users: String(q.data.license_max_users), license_expires: q.data.license_expires ?? "" });
  }, [q.data]);
  const patch = async (body: Record<string, unknown>) => {
    try {
      await api.patch("/api/tenant", body);
      toast("Saved");
      q.refetch();
    } catch (e) { toast((e as Error).message, "error"); }
  };
  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data) return <Loading />;
  const t = q.data;
  const selfApproval = Boolean(t.settings.allow_self_approval);
  return (
    <div className="stack" style={{ padding: 12, maxWidth: 900 }}>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <Field label="Organisation name"><input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <button onClick={() => patch({ name })}>Save</button>
      </div>
      <div className="card stack">
        <h3 style={{ margin: 0 }}>Licence</h3>
        <div className="row small">
          <span>Plan <strong>{t.license_plan}</strong></span> ·
          <span>Users <strong>{t.usage.users}</strong> of {t.license_max_users}</span> ·
          <span>Companies <strong>{t.usage.companies}</strong> of {t.license_max_companies} (demo companies are not counted)</span> ·
          <span>Expires <strong>{t.license_expires ?? "never"}</strong></span>
        </div>
        {me?.is_superadmin ? (
          <div className="row" style={{ alignItems: "flex-end" }}>
            <Field label="Plan"><input value={lic.license_plan ?? ""} onChange={(e) => setLic({ ...lic, license_plan: e.target.value })} /></Field>
            <Field label="Max users"><input type="number" value={lic.license_max_users ?? ""} onChange={(e) => setLic({ ...lic, license_max_users: e.target.value })} style={{ width: 90 }} /></Field>
            <Field label="Max companies"><input type="number" value={lic.license_max_companies ?? ""} onChange={(e) => setLic({ ...lic, license_max_companies: e.target.value })} style={{ width: 90 }} /></Field>
            <Field label="Expires"><input type="date" value={lic.license_expires ?? ""} onChange={(e) => setLic({ ...lic, license_expires: e.target.value })} /></Field>
            <button onClick={() => patch({ license_plan: lic.license_plan, license_max_users: Number(lic.license_max_users), license_max_companies: Number(lic.license_max_companies), license_expires: lic.license_expires || null })}>Update licence</button>
          </div>
        ) : <div className="muted small">Licence terms are set by the vendor (super-admin).</div>}
      </div>
      <div className="card stack">
        <h3 style={{ margin: 0 }}>Approval policy</h3>
        <label className="row small">
          <input type="checkbox" checked={selfApproval} onChange={(e) => patch({ settings: { ...t.settings, allow_self_approval: e.target.checked } })} />
          Allow the person who submitted a calculation to approve and publish it (turns maker-checker off – not recommended)
        </label>
      </div>
      <div className="card stack">
        <h3 style={{ margin: 0 }}>Single sign-on</h3>
        {sso.data?.enabled ? <div className="small"><Badge status="ok">enabled</Badge> OpenID Connect issuer <code>{sso.data.issuer}</code>. Users must exist here (created as “SSO”) before they can sign in.</div>
          : <div className="small muted">Not configured. Set <code>PC_OIDC_ISSUER</code>, <code>PC_OIDC_CLIENT_ID</code>, <code>PC_OIDC_CLIENT_SECRET</code> and <code>PC_OIDC_REDIRECT_URI</code> on the server (Azure AD / Entra ID, Okta, Google Workspace, Keycloak …) – see the admin guide.</div>}
      </div>
    </div>
  );
}

function Backup() {
  const { company, companyId } = useApp();
  const { refresh } = useAuth();
  const toast = useToast();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const restore = async (file: File) => {
    setBusy(true);
    try {
      const c = await api.upload<Company>("/api/restore", { file, new_code: code || null });
      toast(`Restored as ${c.name} (${c.code})`);
      refresh();
    } catch (e) { toast((e as Error).message, "error"); }
    finally { setBusy(false); }
  };
  return (
    <div className="grid cols-2" style={{ padding: 12 }}>
      <div className="card stack">
        <h3 style={{ margin: 0 }}>Back up {company?.name}</h3>
        <div className="small muted">Downloads one compressed file with the company's settings, masters, report layouts, periods, uploads (TB lines), adjustments, MIS figures and calculation runs with their stored results. For whole-server backups use the database backup described in the admin guide.</div>
        <div><button className="primary" onClick={() => api.download(`/api/companies/${companyId}/backup`)}>⬇ Download backup</button></div>
      </div>
      <div className="card stack">
        <h3 style={{ margin: 0 }}>Restore</h3>
        <div className="small muted">Restores a backup file as a new company (existing companies are never overwritten). Give a new code if a company with the backed-up code already exists.</div>
        <Field label="New company code (optional)"><input value={code} onChange={(e) => setCode(e.target.value)} /></Field>
        <FileDrop accept=".gz" label="Drop a .pcbak.gz backup here or click to choose" busy={busy} onFile={restore} />
      </div>
    </div>
  );
}

interface Correction { kind: string; where: string; source?: string | null; amount: number; detail: string }
interface LegacyResult {
  counts: Record<string, number>;
  notes: { level: string; message: string; sheet?: string; cell?: string }[];
  note_counts: Record<string, number>;
  period_id: number | null;
  regression: null | { tolerance_rupees: number; cells_compared: number; mismatches: number; by_section: { section: string; cells: number; mismatches: number; max_abs_diff: number }[]; worst: Record<string, unknown>[] };
  corrections: null | {
    changes: Correction[];
    effect: null | {
      unit: string; values_changed: number; lines_changed: string[];
      pbt: { plant: string; before: number; after: number; change: number }[];
      controls: { code: string; title: string; before: number; after: number; after_level: string }[];
      changed_values: { label: string; plant: string; pg: string; before: number; after: number; change: number }[];
    };
  };
}

const CORRECTION_LABEL: Record<string, string> = {
  allocation_plug_removed: "Plug removed from allocation",
  report_plug_removed: "Plug removed from report",
  gl_remapped: "GL brought into the P&L",
  gl_not_mapped: "GL still outside the P&L",
};

function LegacyImport() {
  const { company, setCompanyId, companyId } = useApp();
  const { refresh } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const [f, setF] = useState({ period_code: "", period_label: "", start_date: "", end_date: "" });
  const [fixPlugs, setFixPlugs] = useState(true);
  const [fixGls, setFixGls] = useState(true);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<LegacyResult | null>(null);
  const run = async (file: File) => {
    setBusy(true);
    setRes(null);
    try {
      const r = await api.upload<LegacyResult>(`/api/companies/${companyId}/legacy-import`,
        { file, ...f, remove_plugs: String(fixPlugs), map_unused_categories: String(fixGls) });
      setRes(r);
      toast("Workbook imported");
      qc.invalidateQueries();
      refresh();
      if (companyId) setCompanyId(companyId);
    } catch (e) { toast((e as Error).message, "error"); }
    finally { setBusy(false); }
  };
  return (
    <div className="stack" style={{ padding: 12 }}>
      <div className="small">
        Builds <strong>{company?.name}</strong>'s masters (plants, product groups, business areas, GLs, cost centres, categories, PRE functions), allocation rules, P&L lines and report layouts
        from the existing <em>Product costing – plant wise</em> Excel workbook by reading its formulas. With a period code, the workbook's trial balances, manual edits (as adjustment rules), worked-out material cost and MIS figures are loaded too, and the result is compared cell by cell with the workbook (tolerance ₹1).
      </div>
      <div className="small" style={{ color: "var(--warn)" }}>This replaces the company's current masters and rules. Use a new, empty company or take a backup first.</div>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
        <Field label="Period code (optional)" hint="e.g. 2026-09"><input value={f.period_code} onChange={(e) => setF({ ...f, period_code: e.target.value })} /></Field>
        <Field label="Period label"><input value={f.period_label} onChange={(e) => setF({ ...f, period_label: e.target.value })} placeholder="YTD Apr–Sep 2026" /></Field>
        <Field label="From"><input type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value })} /></Field>
        <Field label="To"><input type="date" value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></Field>
      </div>
      <div className="stack small">
        <strong>Correct known workbook errors</strong>
        <label className="row"><input type="checkbox" checked={fixPlugs} onChange={(e) => setFixPlugs(e.target.checked)} />
          Remove fixed amounts typed into allocation formulas and report figures (plugs), so every pool is shared by its basis and reports show calculated values</label>
        <label className="row"><input type="checkbox" checked={fixGls} onChange={(e) => setFixGls(e.target.checked)} />
          Bring GLs whose category never reaches the P&L into it, using the category that other GLs with the same FS description use</label>
        <span className="muted">The import is always checked against the workbook first, without corrections; every correction is then listed below with its effect.</span>
      </div>
      <FileDrop accept=".xlsx,.xlsm" label={busy ? "Importing… (about half a minute)" : "Drop the legacy workbook (.xlsx) here or click to choose"} busy={busy} onFile={run} />
      {res ? (
        <div className="stack">
          <div className="row small">{Object.entries(res.counts).map(([k, v]) => <span key={k}>{k.replace(/_/g, " ")}: <strong>{v}</strong> ·</span>)}</div>
          {res.regression ? (
            <div className="card stack">
              <div className="row">
                <h3 style={{ margin: 0 }}>Import check against the workbook (before corrections)</h3>
                {res.regression.mismatches === 0 ? <Badge status="ok">all {res.regression.cells_compared.toLocaleString()} cells match within ₹{res.regression.tolerance_rupees}</Badge>
                  : <Badge status="error">{res.regression.mismatches} of {res.regression.cells_compared} cells differ</Badge>}
              </div>
              <table className="data">
                <thead><tr><th>Section</th><th className="num">Cells</th><th className="num">Mismatches</th><th className="num">Max difference</th></tr></thead>
                <tbody>{res.regression.by_section.map((s) => (
                  <tr key={s.section}><td>{s.section}</td><td className="num">{s.cells}</td><td className="num">{s.mismatches}</td><td className="num">{fmt(s.max_abs_diff, 8)}</td></tr>
                ))}</tbody>
              </table>
            </div>
          ) : null}
          {res.corrections && res.corrections.changes.length ? (
            <div className="card stack">
              <h3 style={{ margin: 0 }}>Corrections ({res.corrections.changes.length})</h3>
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>Correction</th><th>Where</th><th>Workbook cell</th><th className="num">Amount ({res.corrections.effect?.unit ?? "calc. unit"})</th></tr></thead>
                  <tbody>{res.corrections.changes.map((c, i) => (
                    <tr key={i} title={c.detail}>
                      <td><Badge status={c.kind === "gl_not_mapped" ? "warning" : "ok"}>{CORRECTION_LABEL[c.kind] ?? c.kind}</Badge></td>
                      <td className="small">{c.where}<div className="muted">{c.detail}</div></td>
                      <td className="mono small">{c.source ?? ""}</td>
                      <td className="num">{fmt(c.amount, 6)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              {res.corrections.effect ? (
                <>
                  <h3 style={{ margin: "8px 0 0" }}>Effect on profit before tax ({res.corrections.effect.unit})</h3>
                  <table className="data">
                    <thead><tr><th>Plant</th><th className="num">Workbook</th><th className="num">Corrected</th><th className="num">Change</th></tr></thead>
                    <tbody>{res.corrections.effect.pbt.map((r) => (
                      <tr key={r.plant} style={r.plant === "__ALL__" ? { fontWeight: 650 } : undefined}>
                        <td>{r.plant === "__ALL__" ? "Company" : r.plant}</td>
                        <td className="num">{fmt(r.before, 6)}</td><td className="num">{fmt(r.after, 6)}</td><td className="num">{fmt(r.change, 6)}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                  <div className="row small">
                    {res.corrections.effect.controls.map((c) => (
                      <span key={c.code}><Badge status={c.after_level}>{c.after_level}</Badge> {c.title}: {fmt(c.before, 6)} → {fmt(c.after, 6)}</span>
                    ))}
                  </div>
                  <details>
                    <summary className="clickable">{res.corrections.effect.values_changed} report values changed in {res.corrections.effect.lines_changed.length} lines</summary>
                    <div className="table-wrap" style={{ maxHeight: 360 }}>
                      <table className="data">
                        <thead><tr><th>Line</th><th>Plant</th><th>Product group</th><th className="num">Workbook</th><th className="num">Corrected</th><th className="num">Change</th></tr></thead>
                        <tbody>{res.corrections.effect.changed_values.slice(0, 1000).map((r, i) => (
                          <tr key={i}><td>{r.label}</td><td>{r.plant}</td><td>{r.pg === "__TOTAL__" ? "Plant total" : r.pg}</td>
                            <td className="num">{fmt(r.before, 6)}</td><td className="num">{fmt(r.after, 6)}</td><td className="num">{fmt(r.change, 6)}</td></tr>
                        ))}</tbody>
                      </table>
                    </div>
                  </details>
                </>
              ) : null}
            </div>
          ) : null}
          <details>
            <summary className="clickable">Importer notes ({Object.entries(res.note_counts).map(([k, v]) => `${v} ${k}`).join(", ") || "none"})</summary>
            <div className="table-wrap" style={{ maxHeight: 360 }}>
              <table className="data">
                <thead><tr><th>Level</th><th>Where</th><th>Note</th></tr></thead>
                <tbody>{res.notes.slice(0, 500).map((n, i) => (
                  <tr key={i}><td><Badge status={n.level === "info" ? "draft" : n.level}>{n.level}</Badge></td><td className="mono small">{[n.sheet, n.cell].filter(Boolean).join("!")}</td><td className="small">{n.message}</td></tr>
                ))}</tbody>
              </table>
            </div>
          </details>
        </div>
      ) : null}
    </div>
  );
}

function Demo() {
  const { refresh } = useAuth();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const create = async () => {
    setBusy(true);
    try {
      const c = await api.post<Company>("/api/demo-company");
      toast(`Created ${c.name}`);
      refresh();
    } catch (e) { toast((e as Error).message, "error"); }
    finally { setBusy(false); }
  };
  return (
    <div className="stack" style={{ padding: 12, maxWidth: 760 }}>
      <div className="small">The demo company is entirely fictional (four plants, eight product groups, three actual periods and a budget) and is meant for training and trying out rules safely. It does not count towards the licence.</div>
      <div><button className="primary" disabled={busy} onClick={create}>{busy ? "Creating…" : "Create demo company"}</button></div>
    </div>
  );
}

export default function Admin() {
  const [tab, setTab] = useState<"users" | "company" | "org" | "backup" | "legacy" | "demo">("users");
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Administration</h1>
          <p>Users and roles, company settings and number formats, licence and approval policy, backup / restore, and set-up from the legacy Excel workbook.</p>
        </div>
      </div>
      <div className="card flush">
        <div style={{ padding: "8px 12px 0" }}>
          <Tabs value={tab} onChange={setTab} items={[{ key: "users", label: "Users" }, { key: "company", label: "Company & settings" },
            { key: "org", label: "Organisation & licence" }, { key: "backup", label: "Backup & restore" },
            { key: "legacy", label: "Import legacy workbook" }, { key: "demo", label: "Demo company" }]} />
        </div>
        {tab === "users" ? <Users /> : null}
        {tab === "company" ? <CompanySettings /> : null}
        {tab === "org" ? <Organisation /> : null}
        {tab === "backup" ? <Backup /> : null}
        {tab === "legacy" ? <LegacyImport /> : null}
        {tab === "demo" ? <Demo /> : null}
      </div>
    </>
  );
}
