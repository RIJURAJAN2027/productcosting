import { useRef, useState, type ReactNode } from "react";
import { fmt } from "../format";

export function Badge({ status, children }: { status: string; children?: ReactNode }) {
  const icon: Record<string, string> = { ok: "✓", published: "✓", approved: "✓", validated: "✓", warning: "!",
    warnings: "!", submitted: "…", error: "✕", errors: "✕", failed: "✕", rejected: "✕", locked: "🔒" };
  return (
    <span className={`badge ${status}`}>
      {icon[status] ? <span aria-hidden>{icon[status]}</span> : null}
      {children ?? status}
    </span>
  );
}

export function Modal({ title, onClose, children, wide, footer }: {
  title: string; onClose: () => void; children: ReactNode; wide?: boolean; footer?: ReactNode;
}) {
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button className="ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="stack">{children}</div>
        {footer ? <div className="row" style={{ justifyContent: "flex-end", marginTop: 16 }}>{footer}</div> : null}
      </div>
    </div>
  );
}

export function Drawer({ title, subtitle, onClose, children }: {
  title: string; subtitle?: ReactNode; onClose: () => void; children: ReactNode;
}) {
  return (
    <>
      <div className="overlay" style={{ background: "rgba(10,16,28,.25)" }} onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={title}>
        <div className="drawer-head">
          <div>
            <h2 style={{ margin: 0 }}>{title}</h2>
            {subtitle ? <div className="muted small">{subtitle}</div> : null}
          </div>
          <button className="ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint ? <small className="muted">{hint}</small> : null}
    </label>
  );
}

export function FileDrop({ onFile, accept, label, busy }: {
  onFile: (f: File) => void; accept?: string; label: string; busy?: boolean;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  return (
    <div
      className={`dropzone ${drag ? "drag" : ""}`}
      onClick={() => !busy && ref.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        const f = e.dataTransfer.files?.[0];
        if (f && !busy) onFile(f);
      }}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && ref.current?.click()}
    >
      <input ref={ref} type="file" hidden accept={accept} onChange={(e) => {
        const f = e.target.files?.[0];
        if (f) onFile(f);
        e.target.value = "";
      }} />
      {busy ? "Uploading and validating…" : label}
    </div>
  );
}

export function Num({ v, d = 2, style = "indian" }: { v: number | null | undefined; d?: number; style?: "indian" | "international" }) {
  return <span className={v !== null && v !== undefined && v < 0 ? "neg" : ""}>{fmt(v, d, style)}</span>;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="empty">{what}…</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="card" style={{ borderColor: "var(--bad)", color: "var(--bad)" }}>{String((error as Error).message ?? error)}</div>;
}

export function Tabs<T extends string>({ value, onChange, items }: {
  value: T; onChange: (v: T) => void; items: { key: T; label: string }[];
}) {
  return (
    <div className="tabs" role="tablist">
      {items.map((it) => (
        <button key={it.key} role="tab" aria-selected={value === it.key} className={value === it.key ? "active" : ""}
          onClick={() => onChange(it.key)}>{it.label}</button>
      ))}
    </div>
  );
}

export function Seg<T extends string>({ value, onChange, items }: {
  value: T; onChange: (v: T) => void; items: { key: T; label: string }[];
}) {
  return (
    <div className="seg">
      {items.map((it) => (
        <button key={it.key} className={value === it.key ? "active" : ""} onClick={() => onChange(it.key)}>{it.label}</button>
      ))}
    </div>
  );
}

export function ControlList({ controls, unitDiv, unitLabel }: {
  controls: { code: string; level: string; title: string; amount: number; details: Record<string, unknown>[] }[];
  unitDiv: number; unitLabel: string;
}) {
  const order: Record<string, number> = { error: 0, warning: 1, ok: 2 };
  const sorted = [...controls].sort((a, b) => order[a.level] - order[b.level]);
  return (
    <div className="stack">
      {sorted.map((c) => (
        <details key={c.code} className="control" open={c.level === "error"}>
          <summary>
            <Badge status={c.level}>{c.level.toUpperCase()}</Badge>
            <span style={{ fontWeight: 600 }}>{c.title}</span>
            <span className="right mono" style={{ whiteSpace: "nowrap" }}>{fmt(c.amount * unitDiv, 4)} {unitLabel}</span>
          </summary>
          {c.details?.length ? (
            <div className="table-wrap" style={{ marginTop: 8, maxHeight: 280 }}>
              <table className="data">
                <thead>
                  <tr>{Object.keys(c.details[0]).map((k) => <th key={k}>{k}</th>)}</tr>
                </thead>
                <tbody>
                  {c.details.slice(0, 200).map((d, i) => (
                    <tr key={i}>
                      {Object.entries(d).map(([k, v]) => (
                        <td key={k} className={typeof v === "number" ? "num" : ""}>
                          {typeof v === "number" ? fmt(["amount", "pool", "allocated", "effect"].includes(k) ? v * unitDiv : v, 4) : String(v)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <div className="muted small" style={{ marginTop: 6 }}>No details.</div>}
        </details>
      ))}
    </div>
  );
}
