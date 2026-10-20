import { useEffect, useState } from "react";
import type { CalibrationState, Decision, Mode } from "../api";
import { api, q } from "../api";
import { fmtTime, n1 } from "../format";
import { usePoll } from "../hooks";
import { Card, Empty, Seg } from "./ui";

const ACTIONS: Record<string, string> = { water: "полив", blocked: "не поливать", mode: "режим" };
const CONF: Record<string, string> = { none: "нет", low: "низкая", medium: "средняя", high: "высокая" };
const STEPS = ["Профиль", "Датчик", "Доза", "Высыхание", "Готово"];

export function WaterTab({ device, toast }: { device: string; toast: (t: string) => void }) {
  const p = q({ device });
  const mode = usePoll(() => api<Mode>(`web/api/mode?${p}`), 15000, [device]);
  const decisions = usePoll(() => api<Decision[]>(`web/api/decisions?${p}`), 15000, [device]);
  const [seconds, setSeconds] = useState(3);

  const setMode = async (m: Mode["mode"]) => {
    try { await api("web/api/mode", { device, mode: m }); mode.reload(); decisions.reload(); }
    catch (e) { toast((e as Error).message); }
  };
  const water = async () => {
    try {
      const r = await api<{ seconds: number }>("web/api/water", { device, seconds });
      toast(`Полив ${r.seconds} с поставлен в очередь`);
    } catch (e) { toast((e as Error).message); }
  };
  const m = mode.data;

  return (
    <>
      <div className="grid g-side">
        <Card title="Режим">
          {m && <Seg label="Режим полива" value={m.mode} onChange={setMode}
                     items={[["threshold", "По порогу"], ["ml", "По прогнозу ИИ"]]} />}
          {m?.mode === "ml" && !m.ml_available && <p className="note warn">Пока действует порог: {m.ml_reason}</p>}
        </Card>
        <Card title="Полить сейчас">
          <div className="row">
            <div className="field narrow">
              <input type="number" min={1} max={10} value={seconds} aria-label="Секунд"
                     onChange={e => setSeconds(Math.max(1, Math.min(10, +e.target.value || 1)))} />
              <span>с</span>
            </div>
            <button className="btn wide" onClick={water}>Полить</button>
          </div>
        </Card>
      </div>

      <Calibration device={device} toast={toast} />

      <Card className="section" title="Решения">
        {decisions.data?.length ? (
          <div className="list">{decisions.data.map(d => (
            <div className="item" key={d.ts + d.action}>
              <div className="main"><div>{d.reason}</div><div className="meta">{fmtTime(d.ts)}</div></div>
              <span className={`tag ${d.action}`}>
                {ACTIONS[d.action] ?? d.action}{d.action === "water" ? ` ${d.seconds} с` : ""}
              </span>
            </div>
          ))}</div>
        ) : <Empty>Решений пока не было</Empty>}
      </Card>
    </>
  );
}

function Calibration({ device, toast }: { device: string; toast: (t: string) => void }) {
  const p = q({ device });
  const { data: s, reload } = usePoll(() => api<CalibrationState>(`web/api/calibration/state?${p}`),
                                       15000, [device]);
  const [type, setType] = useState("");
  const [low, setLow] = useState("");
  const [high, setHigh] = useState("");
  const [manual, setManual] = useState({ dry: "", wet: "" });
  const c = s?.calibration;
  useEffect(() => {                      // заполнить поля, когда калибровка загрузилась
    if (!c) return;
    setType(t => t || c.plant_type || Object.keys(s?.profiles ?? {})[1] || "");
    if (c.target_low != null) { setLow(String(c.target_low)); setHigh(String(c.target_high)); }
    setManual(m => m.dry ? m : { dry: String(c.dry_raw ?? ""), wet: String(c.wet_raw ?? "") });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c?.plant_type, c?.target_low, c?.target_high, c?.dry_raw]);
  if (!s || !c) return null;
  const stage = c.stage ?? 0, st = s.state;
  const step = async (path: string, body?: unknown) => {
    try { await api(path + (body ? "" : `?${p}`), body ?? {}); reload(); }
    catch (e) { toast((e as Error).message); }
  };
  const rows: [string, string][] = [
    ["Диапазон", c.target_low == null ? "—" : `${c.target_low}–${c.target_high} %`],
    ["Сухо / влажно", `${c.raw_dry ?? "—"} / ${c.raw_wet ?? "—"}`],
    ["Доза, %/с", c.k_dose == null ? "—" : c.k_dose.toFixed(2)],
    ["Высыхание, %/ч", c.dry_rate == null ? "—" : c.dry_rate.toFixed(2)],
    ["Отклик, мин", n1(c.response_delay_min)],
    ["Уверенность", `${CONF[c.confidence ?? "none"]} · ${c.cycles ?? 0} цикл.`],
  ];

  return (
    <Card className="section" title="Калибровка под растение">
      <div className="steps">{STEPS.map((t, i) => (
        <div key={t} className={i < stage ? "done" : i === stage ? "now" : ""}>{t}</div>
      ))}</div>
      <p className={`hint${st.anomaly || st.error ? " warn" : ""}`}>{s.hint}</p>
      <div className="row section">
        <div className="field grow">
          <select value={type} aria-label="Тип растения"
                  onChange={e => { setType(e.target.value); setLow(""); setHigh(""); }}>
            {Object.entries(s.profiles).map(([k, pr]) => (
              <option key={k} value={k}>{pr.title} · {pr.low}–{pr.high} %</option>
            ))}
          </select>
        </div>
        <div className="field narrow"><input type="number" value={low} placeholder="от" aria-label="От, %"
                                             onChange={e => setLow(e.target.value)} /></div>
        <div className="field narrow"><input type="number" value={high} placeholder="до" aria-label="До, %"
                                             onChange={e => setHigh(e.target.value)} /></div>
        <button className="btn ghost" onClick={() => step("web/api/calibration/profile", {
          device, plant_type: type, target_low: low === "" ? null : +low, target_high: high === "" ? null : +high,
        })}>Выбрать</button>
        {stage === 1 && <button className="btn" onClick={() => step("web/api/calibration/dry")}>Сухо</button>}
        {stage === 1 && st.dry_done && !st.wet_started &&
          <button className="btn" onClick={() => step("web/api/calibration/wet")}>Пролил</button>}
        {st.anomaly && <button className="btn" onClick={() => step("web/api/calibration/confirm")}>Насос в порядке</button>}
      </div>
      <div className="table-wrap section"><table><tbody>{rows.map(([k, v]) => (
        <tr key={k}><td className="muted">{k}</td><td className="num">{v}</td></tr>
      ))}</tbody></table></div>
      <details>
        <summary>Вручную</summary>
        <div className="row">
          <div className="field narrow"><input type="number" value={manual.dry} placeholder="сухо" aria-label="Сухо, raw"
                                               onChange={e => setManual({ ...manual, dry: e.target.value })} /></div>
          <div className="field narrow"><input type="number" value={manual.wet} placeholder="мокро" aria-label="Мокро, raw"
                                               onChange={e => setManual({ ...manual, wet: e.target.value })} /></div>
          <button className="btn ghost" onClick={() => step("web/api/calibration", {
            device, dry_raw: +manual.dry, wet_raw: +manual.wet })}>Сохранить</button>
          <button className="btn ghost" onClick={() => step("web/api/calibration", { device, use_current: "dry" })}>Сейчас сухо</button>
          <button className="btn ghost" onClick={() => step("web/api/calibration", { device, use_current: "wet" })}>Сейчас мокро</button>
          <button className="btn dark danger" onClick={() => confirm("Начать калибровку заново?") &&
            step("web/api/calibration/reset")}>Сбросить</button>
        </div>
      </details>
    </Card>
  );
}
