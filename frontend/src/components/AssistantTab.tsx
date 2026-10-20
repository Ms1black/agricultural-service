import { useEffect, useMemo, useRef, useState } from "react";
import type { VoiceItem } from "../api";
import { api, q } from "../api";
import { fmtClock, genitive } from "../format";
import { Jarvis } from "./Jarvis";

type Msg = { role: "user" | "assistant"; content: string };
const TEXT = "text";
const SILENT_WAV = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=";

/** Голосовые разговоры — отдельные чаты по часам: «сегодня, 14:00». */
function hourKey(ts: number) {
  const d = new Date(ts * 1000);
  d.setMinutes(0, 0, 0);
  return d.getTime();
}
function hourTitle(key: number) {
  const d = new Date(key), today = new Date();
  const yesterday = new Date(); yesterday.setDate(today.getDate() - 1);
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  const day = same(d, today) ? "Сегодня" : same(d, yesterday) ? "Вчера"
    : d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  return `${day}, ${d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`;
}

export function AssistantTab({ device, name }: { device: string; name: string }) {
  const [voice, setVoice] = useState<VoiceItem[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [open, setOpen] = useState<string>(TEXT);
  const [follow, setFollow] = useState(true);          // открыт последний голосовой чат — следовать за ним
  const lastId = useRef(0);
  const sound = useRef(false);
  const [soundOn, setSoundOn] = useState(false);
  const playedHere = useRef(new Set<number>());
  const player = useRef(new Audio());

  const play = (id: number) => {
    player.current.src = `web/api/voice/${id}.wav`;
    player.current.play().catch(() => {});
  };

  // журнал голоса: при входе — последние 300 фраз, дальше — только новые раз в 3 с
  useEffect(() => {
    let stop = false;
    lastId.current = 0;
    setVoice([]);
    const tick = async (first: boolean) => {
      try {
        const r = await api<{ enabled: boolean; items: VoiceItem[] }>(
          `web/api/voice/log?${q({ device, after: lastId.current, limit: first ? 300 : 50 })}`);
        if (stop) return;
        setEnabled(r.enabled);
        if (!r.items.length) return;
        const items = [...r.items].reverse();
        lastId.current = items[items.length - 1].id;
        if (!first && sound.current) items.filter(v => !playedHere.current.has(v.id)).forEach(v => play(v.id));
        setVoice(prev => [...prev, ...items]);
      } catch { /* сеть — попробуем в следующий раз */ }
    };
    tick(true);
    const t = setInterval(() => tick(false), 3000);
    return () => { stop = true; clearInterval(t); };
  }, [device]);

  const hours = useMemo(() => {
    const m = new Map<number, VoiceItem[]>();
    for (const v of voice) {
      const k = hourKey(v.ts);
      m.set(k, [...(m.get(k) ?? []), v]);
    }
    return [...m.entries()].sort((a, b) => b[0] - a[0]);
  }, [voice]);

  useEffect(() => {                    // новая фраза в новом часе — открыть его, если следим
    if (follow && hours.length && open !== TEXT) setOpen(String(hours[0][0]));
  }, [hours, follow, open]);

  const toggleSound = () => {
    sound.current = !sound.current;
    setSoundOn(sound.current);
    if (sound.current) { player.current.src = SILENT_WAV; player.current.play().catch(() => {}); }
  };

  const select = (key: string) => {
    setOpen(key);
    setFollow(key !== TEXT && hours.length > 0 && key === String(hours[0][0]));
  };

  return (
    <div className="chats">
      <aside className="chat-list">
        <button className={`chat-entry${open === TEXT ? " active" : ""}`} onClick={() => select(TEXT)}>
          <span>Чат</span>
        </button>
        {hours.map(([k, items]) => (
          <button key={k} className={`chat-entry${open === String(k) ? " active" : ""}`}
                  onClick={() => select(String(k))}>
            <span>{hourTitle(k)}</span><span className="tag">{items.length}</span>
          </button>
        ))}
        <div className="row chat-actions">
          <button className={`btn${soundOn ? " on" : ""}`} onClick={toggleSound} disabled={!enabled}>
            {soundOn ? "Звук включён" : "Включить звук"}
          </button>
          <Mic device={device} onReply={id => { playedHere.current.add(id); play(id); }} disabled={!enabled} />
        </div>
      </aside>
      <section className="card chat-window">
        {open === TEXT ? <TextChat device={device} name={name} />
          : <VoiceChat items={hours.find(([k]) => String(k) === open)?.[1] ?? []} name={name} play={play} />}
      </section>
    </div>
  );
}

function Bubble({ role, children }: { role: "user" | "assistant"; children: React.ReactNode }) {
  return (
    <div className={`msg ${role}`}>
      {role === "assistant" && <Jarvis className="msg-avatar" />}
      <div className={`bubble ${role}`}>{children}</div>
    </div>
  );
}

function useAutoScroll(dep: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.scrollTo({ top: ref.current.scrollHeight }); }, [dep]);
  return ref;
}

function VoiceChat({ items, name, play }: { items: VoiceItem[]; name: string; play: (id: number) => void }) {
  const ref = useAutoScroll(items.length);
  return (
    <div className="chat-log" ref={ref}>
      {items.map(v => (
        <div key={v.id} className="pair">
          <Bubble role="user">{v.transcript || "…"}<span className="time">{fmtClock(v.ts)}</span></Bubble>
          <Bubble role="assistant">
            {v.answer}
            <button className="replay" onClick={() => play(v.id)} aria-label={`Повторить ответ ${name}`}>▶</button>
          </Bubble>
        </div>
      ))}
    </div>
  );
}

function TextChat({ device, name }: { device: string; name: string }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ref = useAutoScroll(msgs.length + (busy ? 1 : 0));
  useEffect(() => { setMsgs([]); }, [device]);

  const ask = async (question: string, why = false) => {
    setBusy(true); setError("");
    const history = why ? msgs : [...msgs, { role: "user" as const, content: question }];
    setMsgs([...msgs, { role: "user", content: question }]);
    try {
      const r = await api<{ answer: string }>(why ? "web/api/why" : "web/api/chat",
                                              why ? { device } : { device, messages: history });
      setMsgs(m => [...m, { role: "assistant", content: r.answer }]);
    } catch (e) {
      setMsgs(m => m.slice(0, -1));
      if (!why) setText(question);
      setError((e as Error).message);
    } finally { setBusy(false); }
  };

  return (
    <>
      <div className="chat-log" ref={ref}>
        {msgs.map((m, i) => <Bubble key={i} role={m.role}>{m.content}</Bubble>)}
        {busy && <Bubble role="assistant"><span className="typing"><i /><i /><i /></span></Bubble>}
        {error && <div className="note warn">{error}</div>}
      </div>
      <form className="row chat-form" onSubmit={e => {
        e.preventDefault();
        const t = text.trim();
        if (t && !busy) { setText(""); ask(t); }
      }}>
        <div className="field grow">
          <input value={text} onChange={e => setText(e.target.value)} maxLength={500}
                 placeholder={`Спросите ${genitive(name)}`} autoComplete="off" aria-label="Вопрос" />
        </div>
        <button className="btn" disabled={busy}>Спросить</button>
        <button type="button" className="btn dark" disabled={busy}
                onClick={() => ask("Почему полили?", true)}>Почему полили?</button>
      </form>
    </>
  );
}

/** Фраза с микрофона браузера (только localhost или HTTPS). */
function Mic({ device, onReply, disabled }: { device: string; onReply: (id: number) => void; disabled: boolean }) {
  const [rec, setRec] = useState<MediaRecorder | null>(null);
  const [state, setState] = useState("");
  if (!window.isSecureContext || !navigator.mediaDevices) return null;
  const start = async () => {
    if (rec) { rec.stop(); return; }
    let stream: MediaStream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch { setState("нет доступа"); return; }
    const chunks: Blob[] = [];
    const r = new MediaRecorder(stream);
    r.ondataavailable = e => chunks.push(e.data);
    const limit = setTimeout(() => r.state === "recording" && r.stop(), 8000);     // фраза ≤ 8 с
    r.onstop = async () => {
      clearTimeout(limit);
      stream.getTracks().forEach(t => t.stop());
      setRec(null); setState("…");
      try {
        const resp = await fetch(`web/api/voice?${q({ device })}`, { method: "POST", body: new Blob(chunks) });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail ?? resp.status);
        onReply(data.id); setState("");
      } catch (e) { setState((e as Error).message); }
    };
    r.start(); setRec(r);
  };
  return (
    <button className={`btn ghost${rec ? " rec" : ""}`} onClick={start} disabled={disabled}>
      {rec ? "Стоп" : state || "Микрофон"}
    </button>
  );
}
