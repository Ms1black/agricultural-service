// Запросы к бэкенду (/web/api — под паролем; браузер спрашивает его один раз при входе на сайт).

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data as { detail?: string }).detail ?? String(r.status));
  return data as T;
}

export const q = (params: Record<string, string | number | undefined>) =>
  Object.entries(params).filter(([, v]) => v !== undefined)
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");

export interface Measurement {
  ts: number; temp: number | null; air_hum: number | null; soil_raw: number | null;
  soil_pct: number | null; soil_ok: number; soil_dry_d0: number;
}
export interface Event { ts: number; type: string; value: number | null; note: string | null }
export interface Calibration {
  dry_raw?: number; wet_raw?: number; raw_dry?: number | null; raw_wet?: number | null;
  plant_type?: string | null; target_low?: number | null; target_high?: number | null;
  k_dose?: number | null; response_delay_min?: number | null; dry_rate?: number | null;
  confidence?: string; cycles?: number; stage?: number;
}
export interface State {
  now: number; demo: boolean; latest: Measurement | null; calibration: Calibration | null;
  pending: { seconds: number; note: string } | null; events: Event[];
}
export interface TrajPoint { h: number; soil: number; lo: number; hi: number }
export interface Forecast {
  status: "ok" | "insufficient_data"; now: number; threshold: number; note?: string;
  hours_to_threshold?: number; next_water_ts?: number | null; trajectory?: TrajPoint[];
  adaptation?: { own_cycles: number; scale: number };
  model?: { version: string } | null;
}
export interface Watering { ts: number; value?: number | null; note: string | null }
export interface HistoryRow { ts: number; soil_pct: number; soil_ok: number; temp?: number | null;
                              air_hum?: number | null }
export interface DemoView {
  series: string; source: string; period: [string, string]; history: HistoryRow[];
  waterings: Watering[]; forecast: Forecast | null;
}
export interface Baselines { [k: string]: number | null }
export interface AdaptPoint { cycles: number; ttt_mae_h: number | null }
export interface ModelCard {
  champion: null | {
    version: string; trained_on: { sources: string[]; series: unknown[]; own_cycles: number };
    metrics: {
      kind: string; mae_6h: number; mae_24h: number; ttt_mae_h: number; ttt_median_h: number;
      gain_ttt_vs_linear: number; baselines: Baselines; adaptation?: { _mean?: AdaptPoint[] };
    };
  };
  policy_sim: null | Record<"threshold" | "ml", {
    days: number; in_range_share: number; hours_dry: number; hours_wet: number; mean_soil: number;
    waterings: number; pump_seconds: number;
  }>;
  history: { version: string; created: number; is_champion: boolean; ttt_mae_h: number | null;
             sources: string[]; own_cycles: number }[];
  monitoring: { checked: number; pending: number; ttt_mae_h: number | null; days: number };
}
export interface Mode { mode: "threshold" | "ml"; active: string; ml_available: boolean;
                        ml_reason: string }
export interface Decision { ts: number; mode: string; action: string; seconds: number | null;
                            reason: string }
export interface CalibrationState {
  calibration: Calibration; state: { anomaly?: { text: string } | null; error?: string | null;
                                     dry_done?: boolean; wet_started?: number };
  hint: string; profiles: Record<string, { title: string; low: number; high: number }>;
  anomalies: { ts: number; type: string; note: string | null }[];
}
export interface VoiceItem { id: number; ts: number; transcript: string; answer: string;
                             intent: string; ms_stt: number; ms_llm: number; ms_tts: number }
