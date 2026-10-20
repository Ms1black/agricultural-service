import type { State } from "../api";
import { api, q } from "../api";
import { EVENTS, NOTES, fmtTime } from "../format";
import { usePoll } from "../hooks";
import { Card, Empty } from "./ui";

export function LogTab({ device }: { device: string }) {
  const { data: s } = usePoll(() => api<State>(`web/api/state?${q({ device })}`), 15000, [device]);
  return (
    <>
      <Card title="События">
        {s?.events.length ? (
          <div className="list">{s.events.map(e => (
            <div className="item" key={`${e.ts}-${e.type}`}>
              <div className="main">
                <div>{EVENTS[e.type] ?? e.type}{e.type === "water" && e.value ? ` · ${e.value} с` : ""}</div>
                <div className="meta">{fmtTime(e.ts)}</div>
              </div>
              {e.note && <span className={`tag${e.type === "water" ? " water" : ""}`}>{NOTES[e.note] ?? e.note}</span>}
            </div>
          ))}</div>
        ) : <Empty>Событий пока нет</Empty>}
      </Card>
      <div className="row section">
        <a className="btn ghost" href="web/api/export.csv?table=measurements">Измерения CSV</a>
        <a className="btn ghost" href="web/api/export.csv?table=events">События CSV</a>
      </div>
    </>
  );
}
