import { useEffect, useState } from "react";
import type { State } from "./api";
import { api, q } from "./api";
import { AssistantTab } from "./components/AssistantTab";
import { Jarvis } from "./components/Jarvis";
import { LogTab } from "./components/LogTab";
import { ModelTab } from "./components/ModelTab";
import { Overview } from "./components/Overview";
import { WaterTab } from "./components/WaterTab";
import { usePoll } from "./hooks";

const TABS = [
  ["overview", "Обзор", false],
  ["assistant", "", true],            // название — имя помощника
  ["model", "ИИ-прогноз", false],
  ["water", "Полив", true],
  ["log", "Журнал", true],
] as const;
type Tab = (typeof TABS)[number][0];
const PRESENT = new URLSearchParams(location.search).get("present") === "1";

function initialTab(): Tab {
  const h = location.hash.slice(1);
  if (TABS.some(([t]) => t === h)) return h as Tab;
  try { return (localStorage.getItem("tab") as Tab) || "overview"; } catch { return "overview"; }
}

export function App() {
  const [tab, setTabState] = useState<Tab>(PRESENT ? "overview" : initialTab());
  const [source, setSource] = useState(new URLSearchParams(location.search).get("source") ?? "plant1");
  const [name, setName] = useState("Джарвис");
  const [toastText, setToastText] = useState("");
  const demo = source.startsWith("demo:");
  const device = demo ? "plant1" : source;

  const setTab = (t: Tab) => {
    setTabState(t);
    history.replaceState(null, "", `#${t}`);
    try { localStorage.setItem("tab", t); } catch { /* приватный режим */ }
  };
  useEffect(() => {
    const onHash = () => { const h = location.hash.slice(1); if (TABS.some(([t]) => t === h)) setTabState(h as Tab); };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => { if (demo && TABS.find(([t]) => t === tab)?.[2]) setTab("overview"); }, [demo]);   // eslint-disable-line
  useEffect(() => { api<{ name: string }>("web/api/assistant").then(r => setName(r.name)).catch(() => {}); }, []);

  const toast = (t: string) => { setToastText(t); setTimeout(() => setToastText(""), 3500); };

  return (
    <div className={PRESENT ? "present" : ""}>
      <Header name={name} onName={setName} source={source} onSource={setSource} device={device}
              demo={demo} toast={toast} />
      {!PRESENT && (
        <nav className="tabs" aria-label="Разделы">
          <div className="wrap">
            {TABS.filter(([, , dev]) => !(demo && dev)).map(([t, title]) => (
              <button key={t} className={`tab${tab === t ? " active" : ""}`} onClick={() => setTab(t)}>
                {title || name}
              </button>
            ))}
          </div>
        </nav>
      )}
      <main className="wrap">
        {(PRESENT || tab === "overview") &&
          <Overview source={source} name={name} onAsk={() => setTab("assistant")} present={PRESENT} />}
        {(PRESENT || tab === "model") && <div className={PRESENT ? "section" : ""}><ModelTab present={PRESENT} /></div>}
        {!PRESENT && !demo && tab === "assistant" && <AssistantTab device={device} name={name} />}
        {!PRESENT && !demo && tab === "water" && <WaterTab device={device} toast={toast} />}
        {!PRESENT && !demo && tab === "log" && <LogTab device={device} />}
      </main>
      {toastText && <div className="toast" role="status">{toastText}</div>}
    </div>
  );
}

function Header({ name, onName, source, onSource, device, demo, toast }: {
  name: string; onName: (n: string) => void; source: string; onSource: (s: string) => void;
  device: string; demo: boolean; toast: (t: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(name);
  const [err, setErr] = useState("");
  const [devices, setDevices] = useState<string[]>(["plant1"]);
  const [series, setSeries] = useState<string[]>([]);
  const { data: s } = usePoll(() => api<State>(`web/api/state?${q({ device })}`), 15000, [device]);

  useEffect(() => {
    api<string[]>("web/api/devices").then(d => d.length && setDevices(d)).catch(() => {});
    api<string[]>("web/api/demo/series").then(setSeries).catch(() => {});
  }, []);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest(".brand")) setOpen(false); };
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, [open]);

  const save = async (n: string) => {
    try {
      const r = await api<{ name: string }>("web/api/assistant", { name: n });
      onName(r.name); setOpen(false); toast(`Теперь меня зовут «${r.name}»`);
    } catch (e) { setErr((e as Error).message); }
  };
  const online = s?.latest ? s.now - s.latest.ts < 120 : null;

  return (
    <header className="top">
      <div className="wrap">
        <div className="brand">
          <div className="avatar"><Jarvis blink /></div>
          <button className="name-btn" aria-expanded={open} disabled={PRESENT}
                  onClick={() => { setDraft(name); setErr(""); setOpen(!open); }}>
            {name}
            {!PRESENT && <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={2}><path d="M3 6l5 5 5-5" /></svg>}
          </button>
          {open && (
            <div className="popover" role="dialog" aria-label="Имя помощника">
              <input className="plain" value={draft} maxLength={20} autoFocus aria-label="Имя помощника"
                     onChange={e => setDraft(e.target.value)}
                     onKeyDown={e => { if (e.key === "Enter") save(draft); if (e.key === "Escape") setOpen(false); }} />
              {err && <div className="note warn">{err}</div>}
              <div className="row">
                <button className="btn" onClick={() => save(draft)}>Сохранить</button>
                <button className="btn ghost" onClick={() => save("Джарвис")}>По умолчанию</button>
              </div>
            </div>
          )}
        </div>
        <div className="top-right">
          {s?.demo && <span className="badge">DEMO</span>}
          <span className={`dot${online === true ? " on" : online === false ? " off" : ""}`}
                title={demo ? "открытый датасет" : online ? "на связи" : "нет связи"} />
          {!PRESENT && (
            <select className="pill-select" value={source} onChange={e => onSource(e.target.value)}
                    aria-label="Источник данных">
              {devices.map(d => <option key={d} value={d}>{d}</option>)}
              {series.length > 0 && (
                <optgroup label="Открытый датасет">
                  {series.map(x => <option key={x} value={`demo:${x}`}>{x}</option>)}
                </optgroup>
              )}
            </select>
          )}
        </div>
      </div>
    </header>
  );
}
