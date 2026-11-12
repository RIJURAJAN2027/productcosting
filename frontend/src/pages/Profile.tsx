import { useState } from "react";
import { api } from "../api";
import { useAuth, useToast } from "../context";
import { Field } from "../components/ui";

export default function Profile() {
  const { me } = useAuth();
  const toast = useToast();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const save = async () => {
    try {
      await api.post("/api/auth/change-password", { current_password: cur, new_password: next });
      toast("Password changed");
      setCur("");
      setNext("");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <>
      <div className="page-head"><h1>Profile</h1></div>
      <div className="card stack" style={{ maxWidth: 520 }}>
        <div><strong>{me?.name}</strong> · {me?.email}</div>
        <div className="muted">Role: {me?.role} · Organisation: {me?.tenant.name}</div>
        <h2 style={{ marginTop: 12 }}>Change password</h2>
        <Field label="Current password"><input type="password" value={cur} onChange={(e) => setCur(e.target.value)} /></Field>
        <Field label="New password" hint="At least 10 characters with upper- and lower-case letters and a digit.">
          <input type="password" value={next} onChange={(e) => setNext(e.target.value)} />
        </Field>
        <div><button className="primary" disabled={!cur || !next} onClick={save}>Change password</button></div>
      </div>
    </>
  );
}
