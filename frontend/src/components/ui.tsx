import type { ReactNode } from "react";

export function Seg<T extends string | number>({ items, value, onChange, label }: {
  items: [T, string][]; value: T; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {items.map(([v, text]) => (
        <button key={String(v)} className={v === value ? "active" : ""} onClick={() => onChange(v)}>
          {text}
        </button>
      ))}
    </div>
  );
}

export function Card({ title, children, className = "", action }: {
  title?: ReactNode; children: ReactNode; className?: string; action?: ReactNode;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <div className="card-head">
          {title && <h3>{title}</h3>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Tile({ label, value, unit, alert }: {
  label: string; value: ReactNode; unit?: string; alert?: boolean;
}) {
  return (
    <div className={`card tile${alert ? " alert" : ""}`}>
      <div className="label">{label}</div>
      <div className="value">{value}{unit && <small> {unit}</small>}</div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
