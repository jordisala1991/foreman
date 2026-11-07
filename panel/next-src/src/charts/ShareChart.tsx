// G5, the share of the day's tokens on Sonnet or Haiku, as a line with a dot a day; a day without work cuts the line.
import { scaleLinear } from "@visx/scale";
import { shortDay, t, type Metrics } from "../lib";
import { axisLabels } from "./specs";
import { PH } from "./StackChart";
import { useWidth } from "./useWidth";

const pc = (v: number): string => `${(v * 100).toFixed(1)} %`;

export function ShareChart({ m }: { m: Metrics }) {
  const [ref, width] = useWidth<HTMLDivElement>(300);
  const n = m.days.length;
  const shares = m.days.map((d) => m.cost[d]?.cheap_share ?? null);
  const have = shares.flatMap((v, i) => (v === null ? [] : [i]));
  const first = have[0];
  const last = have[have.length - 1];
  const top = Math.max(0.1, ...shares.map((v) => v ?? 0)) * 100 * 1.15;
  const y = scaleLinear<number>({ domain: [0, top], nice: true });
  const max = y.domain()[1] ?? 1;
  const ticks = y.ticks(3);
  const py = (v: number) => PH - (v * 100 / max) * PH;
  const px = (i: number) => ((i + 0.5) / n) * width;
  // runs of days with data: the line is cut where a day has none
  const runs: number[][] = [];
  shares.forEach((v, i) => {
    if (v === null) return;
    const run = runs[runs.length - 1];
    if (run && run[run.length - 1] === i - 1) run.push(i);
    else runs.push([i]);
  });
  const labels = axisLabels(m.days, width);
  const label = (i: number, v: number) => {
    const w = pc(v).length * 6.5;
    const x = Math.max(w / 2, Math.min(width - w / 2, px(i)));
    return (
      <text key={`l${i}`} className="bar-figure" x={x} y={Math.max(11, py(v) - 10)} textAnchor="middle">
        {pc(v)}
      </text>
    );
  };
  const day = (i: number): string => (i === n - 1 ? t("today") : shortDay(m.days[i] ?? ""));
  return (
    <article className="card chart" data-chart="g5">
      <div>
        <h2>{t("g5_title")}</h2>
        <div className="figure">
          <span className="big">{last === undefined ? "—" : pc(shares[last] ?? 0)}</span>
          <span className="sub">
            {last === undefined
              ? t("no_data")
              : `${day(last)}${first !== undefined && first !== last ? t("g5_first", { day: shortDay(m.days[first] ?? ""), v: pc(shares[first] ?? 0) }) : ""}`}
          </span>
        </div>
      </div>
      <div
        className="plot"
        ref={ref}
        role="img"
        aria-label={t("g5_aria", { days: m.days.map((d, i) => `${shortDay(d)} ${shares[i] === null ? t("no_cost") : pc(shares[i] ?? 0)}`).join(", ") })}
      >
        {ticks.map((v) => (
          <span key={v} className="ylabel" style={{ bottom: (v / max) * PH }}>
            {v} %
          </span>
        ))}
        <svg width={width} height={PH} aria-hidden="true">
          {ticks.map((v) => (
            <line key={v} className={v === 0 ? "axis" : "grid"} x1={0} x2={width} y1={PH - (v / max) * PH + 0.5} y2={PH - (v / max) * PH + 0.5} />
          ))}
          {runs.map((r) => (
            <polyline key={r[0]} className="line" points={r.map((i) => `${px(i)},${py(shares[i] ?? 0)}`).join(" ")} />
          ))}
          {have.map((i) => (
            <circle key={i} className="dot" data-day={m.days[i]} cx={px(i)} cy={py(shares[i] ?? 0)} r={5}>
              <title>{`${shortDay(m.days[i] ?? "")}: ${pc(shares[i] ?? 0)}`}</title>
            </circle>
          ))}
          {first !== undefined ? label(first, shares[first] ?? 0) : null}
          {last !== undefined && last !== first ? label(last, shares[last] ?? 0) : null}
        </svg>
      </div>
      <div className="xlabels" aria-hidden="true">
        {m.days.map((d, i) => (
          <span key={d} className={i === n - 1 ? "today" : undefined}>
            {labels[i]}
          </span>
        ))}
      </div>
      <p className="note">{t("g5_note")}</p>
    </article>
  );
}
