export const NOTES: Record<string, string> = {
  auto: "по порогу", "auto-D0": "по порогу", claps: "хлопки", web: "кнопка на сайте",
  manual: "вручную", voice: "голос", ml: "прогноз ИИ", calib: "калибровка", start: "запуск",
  inferred: "по данным", dataset: "из датасета",
};
export const EVENTS: Record<string, string> = {
  water: "Полив", boot: "Запуск", anomaly: "Аномалия", anomaly_confirmed: "Аномалия снята",
};

export const fmtTime = (ts: number) => new Date(ts * 1000).toLocaleString("ru-RU", {
  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
});
export const fmtClock = (ts: number) => new Date(ts * 1000).toLocaleTimeString("ru-RU", {
  hour: "2-digit", minute: "2-digit",
});

export function ago(sec: number) {
  const m = Math.floor(sec / 60);
  if (m < 1) return "сейчас";
  if (m < 60) return `${m} мин назад`;
  if (m < 48 * 60) return `${Math.floor(m / 60)} ч назад`;
  return `${Math.floor(m / 1440)} дн назад`;
}

export function until(h: number | null | undefined) {
  if (h == null) return "--";
  if (h <= 0) return "сейчас";
  if (h < 1) return "< 1 ч";
  if (h < 48) return `≈ ${Math.round(h)} ч`;
  if (h < 24 * 14) return `≈ ${Math.round(h / 24)} дн`;
  return "> 2 нед";
}

const plural = (n: number, one: string, few: string, many: string) =>
  n % 10 === 1 && n % 100 !== 11 ? one
    : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? few : many;

export function inHours(h: number) {
  if (h < 1) return "меньше чем через час";
  if (h < 48) return `примерно через ${Math.round(h)} ${plural(Math.round(h), "час", "часа", "часов")}`;
  const d = Math.round(h / 24);
  return `примерно через ${d} ${plural(d, "день", "дня", "дней")}`;
}

/** «Спросить Джарвиса»: простое склонение имени, иначе — как есть. */
export function genitive(name: string) {
  if (/[бвгджзклмнпрстфхцчшщ]$/i.test(name)) return name + "а";
  if (/а$/i.test(name)) return name.slice(0, -1) + "у";
  if (/я$/i.test(name)) return name.slice(0, -1) + "ю";
  return name;
}

export const n1 = (v: number | null | undefined) => (v == null ? "—" : (+v).toFixed(1));
export const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)} %`);
