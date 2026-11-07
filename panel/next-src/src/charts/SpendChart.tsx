// G6, where the cost goes: the most expensive tasks of the period and the cost of each kind of agent, as bars.
import { t, usd1, type Key, type Metrics } from "../lib";

function Bar({ v, max }: { v: number; max: number }) {
  return <span className="hbar" style={{ width: `${Math.max(1, (v / max) * 100)}%` }} />;
}

const TYPES = ["dev", "mechanic", "planner", "reviewer", "other"] as const;
// an agent type in words; a type foreman does not know shows as it is
export const typeName = (type: string): string =>
  (TYPES as readonly string[]).includes(type) ? t(`type_${type}` as Key) : type;

export function SpendChart({ m }: { m: Metrics }) {
  const tasks = m.top_issues;
  const types = m.by_type;
  const top = tasks[0]?.usd ?? 0;
  const topType = types[0]?.usd ?? 0;
  return (
    <article className="card chart" data-chart="g6">
      <div>
        <h2>{t("g6_title")}</h2>
        <div className="figure">
          <span className="big">{tasks.length ? usd1(top) : "—"}</span>
          <span className="sub">{tasks.length ? t("g6_sub") : t("no_data")}</span>
        </div>
      </div>
      {tasks.length ? (
        <ul className="hbars">
          {tasks.map((x) => (
            <li key={x.issue}>
              <span className="what">
                {x.short} <span className="id">· {x.issue}</span>
              </span>
              <span className="line">
                <span className="track">
                  <Bar v={x.usd} max={top} />
                </span>
                <span className="v">{usd1(x.usd)}</span>
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {types.length ? (
        <div className="types">
          <div className="sub-title">{t("g6_types")}</div>
          {types.map((x) => (
            <div key={x.type} className="line">
              <span className="kind">{typeName(x.type)}</span>
              <span className="track">
                <Bar v={x.usd} max={topType} />
              </span>
              <span className="v">{usd1(x.usd)}</span>
            </div>
          ))}
        </div>
      ) : null}
      <p className="note">{t("g6_note")}</p>
    </article>
  );
}
