import type { ModelCard } from "../api";
import { api } from "../api";
import { n1, pct } from "../format";
import { usePoll } from "../hooks";
import { Bars, Curve } from "./SoilChart";
import { Card, Empty } from "./ui";

export function ModelTab({ present }: { present: boolean }) {
  const { data: card } = usePoll(() => api<ModelCard>("web/api/model"), 60000, []);
  const c = card?.champion;
  if (!card) return null;
  if (!c) return <Card><Empty>Модель ещё не обучена</Empty></Card>;
  const m = c.metrics, b = m.baselines ?? {};
  const rows: [string, number | null, number | null, number | null, number | null][] = [
    ["Когда полить, ч", m.ttt_mae_h, null, b.linear_ttt_mae_h, b.mean_rate_ttt_mae_h],
    ["Медиана, ч", m.ttt_median_h, null, b.linear_ttt_median_h, b.mean_rate_ttt_median_h],
    ["Влажность через 6 ч", m.mae_6h, b.persistence_mae_6h, b.linear_mae_6h, null],
    ["Влажность через 24 ч", m.mae_24h, b.persistence_mae_24h, b.linear_mae_24h, null],
  ];
  const p = card.policy_sim;
  const policy: [string, number, number, (v: number) => string, boolean | null][] = p ? [
    ["Время в норме", p.threshold.in_range_share, p.ml.in_range_share, pct, false],
    ["Часы пересыхания", p.threshold.hours_dry, p.ml.hours_dry, n1, true],
    ["Средняя влажность, %", p.threshold.mean_soil, p.ml.mean_soil, n1, false],
    ["Поливов", p.threshold.waterings, p.ml.waterings, v => String(v), true],
    ["Насос, с", p.threshold.pump_seconds, p.ml.pump_seconds, n1, true],
  ] : [];
  const best = (x: number, y: number, low: boolean | null) => low !== null && (low ? x < y : x > y);

  return (
    <>
      <div className="grid g-2">
        <Card title="Ошибка «когда полить», ч">
          <Bars items={[{ label: "ИИ", v: m.ttt_mae_h }, { label: "линейно", v: b.linear_ttt_mae_h },
                        { label: "средняя", v: b.mean_rate_ttt_mae_h }]} />
        </Card>
        <Card title="Подстройка под растение">
          <Curve points={m.adaptation?._mean ?? []} />
        </Card>
      </div>

      <Card className="section" title="ИИ против простых способов">
        <div className="table-wrap"><table>
          <thead><tr><th /><th className="num">ИИ</th><th className="num">Без изменений</th>
            <th className="num">Линейно</th><th className="num">Средняя</th></tr></thead>
          <tbody>{rows.map(([k, ...vals]) => (
            <tr key={k}><td>{k}</td>{vals.map((v, i) => (
              <td key={i} className={`num${i === 0 ? " hi" : ""}`}>{n1(v)}</td>))}</tr>
          ))}</tbody>
        </table></div>
      </Card>

      {p && (
        <Card className="section" title="Порог против ИИ">
          <div className="table-wrap"><table>
            <thead><tr><th /><th className="num">По порогу</th><th className="num">По ИИ</th></tr></thead>
            <tbody>{policy.map(([k, x, y, f, low]) => (
              <tr key={k}><td>{k}</td>
                <td className={`num${best(x, y, low) ? " hi" : ""}`}>{f(x)}</td>
                <td className={`num${best(y, x, low) ? " hi" : ""}`}>{f(y)}</td></tr>
            ))}</tbody>
          </table></div>
        </Card>
      )}

      {!present && card.history.length > 1 && (
        <Card className="section" title="Версии">
          <div className="list">{card.history.map(h => (
            <div className="item" key={h.version}>
              <span>{h.version}</span>
              <span className="row">
                <span className="tag">{n1(h.ttt_mae_h)} ч</span>
                {h.is_champion && <span className="tag water">рабочая</span>}
              </span>
            </div>
          ))}</div>
        </Card>
      )}
    </>
  );
}
