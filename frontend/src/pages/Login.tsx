import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { useAuth } from "../context";

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sso, setSso] = useState(false);
  useEffect(() => {
    api.get<{ enabled: boolean }>("/api/auth/sso/config").then((c) => setSso(c.enabled)).catch(() => {});
  }, []);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email, password);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div>
          <h1>Product Costing & Profitability</h1>
          <p className="muted" style={{ margin: "6px 0 0" }}>Sign in to continue</p>
        </div>
        <label className="field">
          <span>E-mail</span>
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
        </label>
        <label className="field">
          <span>Password</span>
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error ? <div style={{ color: "var(--bad)" }} role="alert">{error}</div> : null}
        <button className="primary" type="submit" disabled={busy} style={{ justifyContent: "center", padding: "9px 12px" }}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {sso ? (
          <a className="btn" style={{ justifyContent: "center" }} href="/api/auth/sso/login">Sign in with your organisation (SSO)</a>
        ) : null}
      </form>
    </div>
  );
}
