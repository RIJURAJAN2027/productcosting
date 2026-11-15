import { useState, type FormEvent } from "react";
import { useAuth } from "../context";

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
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
        <img src="/brand/logo-long.png" alt="L&T Valves" style={{ height: 40, alignSelf: "flex-start" }} />
        <div>
          <h1 style={{ color: "var(--brand)" }}>Product Costing & Profitability</h1>
          <p className="muted" style={{ margin: "6px 0 0" }}>Sign in to continue</p>
        </div>
        <label className="field">
          <span>Company e-mail</span>
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@lntvalves.com" required autoFocus />
        </label>
        <label className="field">
          <span>Password</span>
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error ? <div style={{ color: "var(--bad)" }} role="alert">{error}</div> : null}
        <button className="primary" type="submit" disabled={busy} style={{ justifyContent: "center", padding: "9px 12px" }}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="muted small" style={{ margin: 0, textAlign: "center" }}>Forgot your password? Contact your administrator.</p>
      </form>
    </div>
  );
}
