import type { Calibration, DemoView, Forecast, HistoryRow, State, Watering } from "../api";
import { api, q } from "../api";
import { ago, genitive, inHours, until } from "../format";
import { usePoll, useStored } from "../hooks";
import { Jarvis } from "./Jarvis";
import { SoilChart } from "./SoilChart";
import { Card, Seg, Tile } from "./ui";

const HOURS: [string, string][] = [["6", "6 ч"], ["24", "24 ч"], ["72", "3 дня"], ["168", "7 дней"],
                                   ["87600", "всё время"]];

interface View {
  soil: number | null; dryText?: string; temp: number | null; hum: number | null;
  lastWater: number | null; online: boolean | null; hoursToWater: number | null;
  pending: boolean; rows: HistoryRow[]; marks: Watering[]; forecast: Forecast | null;
  low: number; high: number; demo: boolean;
}

export function range(cal: Calibration | null | undefined): [number, number] {
  return [cal?.target_low ?? 30, Math.min(cal?.target_high ?? 70, 70)];
}

async function loadDevice(device: string, hours: number): Promise<View> {
  const p = q({ device });
  const [s, rows, fc, marks] = await Promise.all([
    api<State>(`web/api/state?${p}`),
    api<HistoryRow[]>(`web/api/history?${p}&hours=${hours}`),
    api<Forecast>(`web/api/forecast?${p}`).catch(() => null),
    api<Watering[]>(`web/api/waterings?${p}&hours=${hours}`).catch(() => []),
  ]);
  const m = s.latest;
  const [low, high] = range(s.calibration);
  const water = s.events.find(e => e.type === "water");
  return {
    soil: m?.soil_ok ? m.soil_pct : null, dryText: m && !m.soil_ok ? (m.soil_dry_d0 ? "сухо" : "мокро") : undefined,
    temp: m?.temp ?? null, hum: m?.air_hum ?? null, lastWater: water ? s.now - water.ts : null,
    online: m ? s.now - m.ts < 120 : null,
    hoursToWater: fc?.status === "ok" && fc.next_water_ts ? fc.hours_to_threshold ?? null : null,
    pending: !!s.pending, rows: rows.filter(r => r.soil_ok), marks, forecast: fc, low, high, demo: false,
  };
}

async function loadDemo(series: string, hours: number): Promise<View> {
  const d = await api<DemoView>(`web/api/demo?${q({ series, hours })}`);
  const last = d.history[d.history.length - 1];
  const w = d.waterings[d.waterings.length - 1];
  return {
    soil: last?.soil_pct ?? null, temp: last?.temp ?? null, hum: last?.air_hum ?? null,
    lastWater: w ? Date.now() / 1000 - w.ts : null, online: null,
    hoursToWater: d.forecast?.hours_to_threshold ?? null, pending: false, rows: d.history,
    marks: d.waterings, forecast: d.forecast, low: 30, high: 70, demo: true,
  };
}

function mood(v: View) {
  if (v.pending) return "Ух, поливают! Вода уже в пути.";
  if (v.soil == null) return "Жду данных с датчика…";
  const s = Math.round(v.soil);
  if (v.soil < v.low) return `Пить хочу! Почва ${s} %.`;
  if (v.soil > v.high) return `Мне пока хватает воды: ${s} %.`;
  if (v.hoursToWater != null && v.hoursToWater < 6) return `Почва ${s} %. Скоро попрошу воды.`;
  return `Всё хорошо: почва ${s} %.` +
    (v.hoursToWater != null && v.hoursToWater < 24 * 14 ? ` Полив ${inHours(v.hoursToWater)}.` : "");
}

export function Overview({ source, name, onAsk, present }: {
  source: string; name: string; onAsk: () => void; present: boolean;
}) {
  const [hours, setHours] = useStored<string>("hours", "24");
  const demo = source.startsWith("demo:");
  const { data: v } = usePoll(() => demo ? loadDemo(source.slice(5), +hours) : loadDevice(source, +hours),
                              present ? 10000 : 15000, [source, hours]);
  const state = v?.soil == null ? (v?.dryText ? "датчик D0" : "нет данных")
    : v.soil < v.low ? "суховато" : v.soil > v.high ? "влажно" : "в норме";
  const stateClass = v?.soil == null ? "" : v.soil < v.low ? "bad" : v.soil > v.high ? "warn" : "good";

  return (
    <>
      <div className="grid g-hero">
        <Card>
          <h3 className="muted-title">Влажность почвы</h3>
          <div className="hero-value">
            {v?.soil != null ? Math.round(v.soil) : v?.dryText ?? "--"}<small> %</small>
          </div>
          <div className="row">
            <span className={`chip ${stateClass}`}><span className="dot" />{state}</span>
            <span className="chip">Полив <b>{until(v?.hoursToWater)}</b></span>
          </div>
        </Card>
        <Card className="jarvis-card">
          <Jarvis blink fast={v?.pending} className="jarvis-big" />
          <div className="speech">{v ? mood(v) : "Привет!"}</div>
          {!demo && !present && <button className="btn ghost" onClick={onAsk}>Спросить {genitive(name)}</button>}
        </Card>
      </div>

      <div className="grid g-tiles section">
        <Tile label="Температура" value={v?.temp != null ? v.temp.toFixed(1) : "--"} unit="°C" />
        <Tile label="Воздух" value={v?.hum != null ? Math.round(v.hum) : "--"} unit="%" />
        <Tile label="Последний полив" value={v?.lastWater != null ? ago(v.lastWater) : "--"} />
        <Tile label="Устройство" alert={v?.online === false}
              value={v?.demo ? "датасет" : v?.online == null ? "--" : v.online ? "на связи" : "нет связи"} />
      </div>

      <Card className="section" title="Влажность и прогноз"
            action={!present && <Seg label="Период" items={HOURS} value={hours} onChange={setHours} />}>
        {v && <SoilChart rows={v.rows} marks={v.marks} forecast={v.forecast} hours={+hours}
                         low={v.low} high={v.high} />}
      </Card>
    </>
  );
}
