import { useState } from "react";
import type { Forecast, HistoryRow, Watering } from "../api";
import { NOTES, fmtTime } from "../format";
import { useWidth } from "../hooks";

const L = 36, R = 12, T = 22, B = 30;

/** Влажность почвы: факт, прогноз ИИ с коридором, целевой диапазон, порог, поливы. */
export function SoilChart({ rows, marks, forecast, hours, low, high }: {
  rows: HistoryRow[]; marks: Watering[]; forecast: Forecast | null; hours: number;
  low: number; high: number;
}) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const H = W < 600 ? 240 : 320;          // высота в пикселях — подписи не масштабируются
  const [hover, setHover] = useState<HistoryRow | null>(null);
  const traj = forecast?.status === "ok" ? forecast.trajectory ?? [] : [];
  const now = Date.now() / 1000;
  const first = rows.length ? rows[0].ts : now - hours * 3600;
  const t0 = hours > 24 * 31 ? Math.max(first, now - hours * 3600) : now - hours * 3600;
  const t1 = traj.length ? now + Math.min(24 * 3600, (now - t0) / 3) : now;
  const x = (t: number) => L + ((t - t0) / (t1 - t0)) * (W - L - R);
  const y = (v: number) => H - B - (v / 100) * (H - T - B);
  const visible = rows.filter(r => r.ts >= t0);
  const vmarks = marks.filter(m => m.ts >= t0);
  const tick = (t: number) => new Date(t * 1000).toLocaleString("ru-RU",
    hours <= 24 ? { hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "short" });

  const onMove = (e: React.PointerEvent) => {
    if (visible.length < 2) return;
    const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const t = t0 + ((e.clientX - box.left - L) / (W - L - R)) * (t1 - t0);
    let best = visible[0];
    for (const r of visible) if (Math.abs(r.ts - t) < Math.abs(best.ts - t)) best = r;
    setHover(best);
  };
  const near = hover && vmarks.find(m => Math.abs(m.ts - hover.ts) < (t1 - t0) / 60);
  const tt = (h: number) => (forecast?.now ?? now) + h * 3600;
  const fvis = traj.filter(p => tt(p.h) <= t1 + 1);

  return (
    <div className="chart-box" ref={ref} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} style={{ height: H }} role="img" aria-label="Влажность почвы">
        <rect x={L} width={W - L - R} y={y(high)} height={y(low) - y(high)} fill="#76e23814" />
        {[0, 25, 50, 75, 100].map(v => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="#262626" />
            <text x={L - 8} y={y(v) + 4} fontSize={12} fill="#666" textAnchor="end">{v}</text>
          </g>
        ))}
        <line x1={L} x2={W - R} y1={y(low)} y2={y(low)} stroke="#ff5a52" strokeDasharray="5 5" />
        <text x={L} y={H - 8} fontSize={12} fill="#666">{tick(t0)}</text>
        {visible.length > 1 ? (
          <polyline points={visible.map(r => `${x(r.ts).toFixed(1)},${y(r.soil_pct).toFixed(1)}`).join(" ")}
                    fill="none" stroke="#f2f2f2" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        ) : (
          <text x={W / 2} y={H / 2} textAnchor="middle" fill="#666" fontSize={14}>Нет данных</text>
        )}
        {vmarks.map(m => (
          <g key={m.ts}>
            <line x1={x(m.ts)} x2={x(m.ts)} y1={T - 6} y2={H - B} stroke="#76e23866" />
            <rect x={x(m.ts) - 4} y={T - 14} width={8} height={8} rx={2} fill="#76e238" />
          </g>
        ))}
        {fvis.length > 0 && (
          <>
            <polygon fill="#76e2381f" points={[
              ...fvis.map(p => `${x(tt(p.h)).toFixed(1)},${y(p.hi).toFixed(1)}`),
              ...[...fvis].reverse().map(p => `${x(tt(p.h)).toFixed(1)},${y(p.lo).toFixed(1)}`),
            ].join(" ")} />
            <polyline points={fvis.map(p => `${x(tt(p.h)).toFixed(1)},${y(p.soil).toFixed(1)}`).join(" ")}
                      fill="none" stroke="#76e238" strokeWidth={2} strokeDasharray="6 5" />
            <line x1={x(now)} x2={x(now)} y1={T} y2={H - B} stroke="#444" />
            <text x={x(now)} y={H - 8} fontSize={12} fill="#9b9b9b" textAnchor="middle">сейчас</text>
          </>
        )}
        {hover && (
          <>
            <line x1={x(hover.ts)} x2={x(hover.ts)} y1={T} y2={H - B} stroke="#9b9b9b" />
            <circle cx={x(hover.ts)} cy={y(hover.soil_pct)} r={5} fill="#f2f2f2" stroke="#161616" strokeWidth={2} />
          </>
        )}
      </svg>
      {hover && (
        <div className="tip" style={{
          left: Math.min(Math.max(x(hover.ts), 80), W - 80), top: y(hover.soil_pct),
        }}>
          <b>{Math.round(hover.soil_pct)} %</b> · {fmtTime(hover.ts)}
          {near && <><br />Полив{near.value ? ` ${near.value} с` : ""}: {NOTES[near.note ?? ""] ?? near.note ?? "—"}</>}
        </div>
      )}
    </div>
  );
}

/** Столбцы «ошибка, часы»: первый — модель (акцент), остальные — простые способы. */
export function Bars({ items }: { items: { label: string; v: number | null }[] }) {
  const [ref, W] = useWidth<HTMLDivElement>(420);
  const h = 200, b = 34, t = 26;
  const max = Math.max(...items.map(i => i.v ?? 0), 1);
  const slot = W / items.length, bw = Math.min(56, slot * 0.5);
  return (
    <div ref={ref}>
      <svg className="chart small" viewBox={`0 0 ${W} ${h}`}>
        <line x1={0} x2={W} y1={h - b} y2={h - b} stroke="#2a2a2a" />
        {items.map((it, i) => {
          const bh = Math.max(2, ((it.v ?? 0) / max) * (h - b - t));
          const x0 = slot * i + (slot - bw) / 2, top = h - b - bh, r = Math.min(6, bh);
          return (
            <g key={it.label}>
              <path fill={i === 0 ? "#76e238" : "#3a3a3a"}
                    d={`M${x0},${h - b} V${top + r} Q${x0},${top} ${x0 + r},${top} H${x0 + bw - r} Q${x0 + bw},${top} ${x0 + bw},${top + r} V${h - b} Z`}>
                <title>{`${it.label}: ${it.v == null ? "—" : Math.round(it.v) + " ч"}`}</title>
              </path>
              <text x={x0 + bw / 2} y={top - 8} fontSize={15} fontWeight={600} textAnchor="middle" fill="#f2f2f2">
                {it.v == null ? "—" : Math.round(it.v)}
              </text>
              <text x={x0 + bw / 2} y={h - 10} fontSize={13} textAnchor="middle" fill="#9b9b9b">{it.label}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** Ошибка от числа своих циклов: подписаны только начало и конец. */
export function Curve({ points }: { points: { cycles: number; ttt_mae_h: number | null }[] }) {
  const [ref, W] = useWidth<HTMLDivElement>(420);
  const h = 200, l = 22, r = 22, t = 30, b = 34;
  const vals = points.map(p => p.ttt_mae_h ?? 0);
  const max = Math.max(...vals, 1) * 1.1, n = points.length - 1 || 1;
  const x = (i: number) => l + (i / n) * (W - l - r);
  const y = (v: number) => h - b - (v / max) * (h - t - b);
  const pts = points.map((p, i) => `${x(i)},${y(p.ttt_mae_h ?? 0)}`).join(" ");
  return (
    <div ref={ref}>
      <svg className="chart small" viewBox={`0 0 ${W} ${h}`}>
        <line x1={0} x2={W} y1={h - b} y2={h - b} stroke="#2a2a2a" />
        {points.length > 0 && <>
          <polygon points={`${x(0)},${h - b} ${pts} ${x(n)},${h - b}`} fill="#76e23814" />
          <polyline points={pts} fill="none" stroke="#76e238" strokeWidth={2} strokeLinejoin="round" />
        </>}
        {points.map((p, i) => (
          <g key={p.cycles}>
            <circle cx={x(i)} cy={y(p.ttt_mae_h ?? 0)} r={4.5} fill="#76e238" stroke="#161616" strokeWidth={2}>
              <title>{`${p.cycles} циклов: ${Math.round(p.ttt_mae_h ?? 0)} ч`}</title>
            </circle>
            {(i === 0 || i === n) && (
              <text x={x(i)} y={y(p.ttt_mae_h ?? 0) - 12} fontSize={15} fontWeight={600}
                    textAnchor="middle" fill="#f2f2f2">{Math.round(p.ttt_mae_h ?? 0)} ч</text>
            )}
            <text x={x(i)} y={h - 10} fontSize={13} textAnchor="middle" fill="#9b9b9b">{p.cycles}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}
