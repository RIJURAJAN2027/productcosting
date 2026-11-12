import { useState } from "react";
import { api } from "../api";
import { useAuth, useToast } from "../context";
import type { Company } from "../types";
import { Field } from "../components/ui";

// Shown when the signed-in user has no company yet (a new installation without the demo company,
// or a user who has not been given access to a company).
export default function Welcome() {
  const { me, can, refresh } = useAuth();
  const toast = useToast();
  const [f, setF] = useState({ code: "", name: "", currency: "INR" });
  const [busy, setBusy] = useState(false);
  const create = async () => {
    setBusy(true);
    try {
      const c = await api.post<Company>("/api/companies", f);
      toast(`Company ${c.name} created – next: Administration → Import legacy workbook, or Masters`);
      refresh();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const demo = async () => {
    setBusy(true);
    try {
      const c = await api.post<Company>("/api/demo-company");
      toast(`Created ${c.name}`);
      refresh();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  if (!can("admin")) {
    return (
      <div className="card empty">
        Welcome, {me?.name || me?.email}. You have no access to a company yet – ask an administrator to give you
        access.
      </div>
    );
  }
  return (
    <div className="stack" style={{ maxWidth: 720 }}>
      <div className="page-head">
        <div>
          <h1>Welcome</h1>
          <p>Create the first company. Then set it up from the legacy Excel workbook (<em>Administration → Import
            legacy workbook</em>) or enter its masters, and upload the trial balances.</p>
        </div>
      </div>
      <div className="card stack">
        <h2 style={{ margin: 0 }}>New company</h2>
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
          <Field label="Code" hint="Short, e.g. MAIN"><input value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
          <Field label="Name"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Currency"><input value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value })} /></Field>
        </div>
        <div><button className="primary" disabled={busy || !f.code.trim() || !f.name.trim()} onClick={create}>Create company</button></div>
      </div>
      <div className="card row">
        <span className="small">Just looking around? Create the fictional demo company with sample periods and reports.</span>
        <span className="right" />
        <button disabled={busy} onClick={demo}>{busy ? "Working…" : "Create demo company"}</button>
      </div>
    </div>
  );
}
