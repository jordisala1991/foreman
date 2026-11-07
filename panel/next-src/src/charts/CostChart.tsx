// G1, cost per day and model, as in the approved mockup: stacked bars of list-price dollars (Opus at the bottom,
// then Sonnet, Haiku and other models), one column per day of the period, the period's figure and mean above, the
// highest day and today labelled on their bar, a detail box on hover or tap, a dashed grid. A day without work is
// a thin bar, and so is a day before the data starts (in a fainter tone). SVG drawn by React with visx's scales and
// rounded bars; the colours are CSS variables (app.css), so the theme needs no code.
import { scaleLinear } from "@visx/scale";
import { BarRounded } from "@visx/shape";
import { useState } from "react";
import { dayMonth, shortDay, t, usd, usd1, type Family, type Metrics } from "../lib";
import { useWidth } from "./useWidth";

const FAMILIES: Family[] = ["opus", "sonnet", "haiku", "other"]; // from the bottom of a bar up
const NAMES: Record<Family, string> = { opus: "Opus", sonnet: "Sonnet", haiku: "Haiku", other: "" };
export const familyName = (f: Family): string => (f === "other" ? t("other_models") : NAMES[f]);

const PH = 140; // the plot's height
const BAR = 28; // the widest a bar gets
const SEG_GAP = 2; // between the segments of a bar
const STUB = 2; // the height of the bar of a day without work

export interface Column {
  day: string;
  today: boolean;
  label: string; // under the axis ("" when skipped)
  kind: "cost" | "zero" | "none"; // with cost, a day without work, a day before the data starts
  segs: { family: Family; usd: number }[]; // with cost only, from the bottom up
  usd: number;
  top: string; // the figure on the bar ("" if none)
}

export interface Model {
  families: Family[]; // the families with cost in the period, in stacking order
  columns: Column[];
  max: number; // the top of the y scale
  ticks: number[];
  total: number;
  mean: number; // over the days with work
  worked: number;
  gap: number; // between columns
}

// everything the chart draws, from /api/metrics and the plot's width (pure: the tests read it through the page)
export function costModel(m: Metrics, width: number): Model {
  const n = m.days.length;
  const families = FAMILIES.filter((f) => m.days.some((d) => (m.cost[d]?.[f] ?? 0) > 0));
  const usdOf = (d: string) => m.cost[d]?.usd ?? 0;
  const highest = m.days.reduce((best, d) => (usdOf(d) > usdOf(best) ? d : best), m.days[0] ?? "");
  const gap = n > 7 ? 2 : 6;
  const colW = (width - gap * (n - 1)) / n;
  // with narrow columns, one label in three, counted back from today, so today always has its label
  const every = colW >= 22 ? 1 : 3;
  const columns: Column[] = m.days.map((d, i) => {
    const today = i === n - 1;
    const v = usdOf(d);
    const kind = v > 0 ? "cost" : m.since.cost && d >= m.since.cost ? "zero" : "none";
    const segs = kind === "cost" ? families.map((f) => ({ family: f, usd: m.cost[d]?.[f] ?? 0 })).filter((s) => s.usd > 0) : [];
    const label = (n - 1 - i) % every === 0 ? (n <= 7 ? shortDay(d) : String(Number(d.slice(8)))) : "";
    const top = v > 0 && (d === highest || today) ? usd(v) : "";
    return { day: d, today, label, kind, segs, usd: v, top };
  });
  const y = scaleLinear<number>({ domain: [0, Math.max(1, usdOf(highest) * 1.12)], nice: true });
  const worked = columns.filter((c) => c.kind === "cost").length;
  return {
    families,
    columns,
    max: y.domain()[1] ?? 1,
    ticks: y.ticks(3),
    total: m.total,
    mean: worked ? m.total / worked : 0,
    worked,
    gap,
  };
}

function Tip({ c, left }: { c: Column; left: number }) {
  return (
    <div role="tooltip" className="tip" style={{ left }}>
      <div className="tiphead">
        {shortDay(c.day)}
        {c.today ? ` · ${t("today")}` : ""}
      </div>
      {c.kind === "cost" ? (
        <>
          {[...c.segs].reverse().map((s) => (
            <div key={s.family} className="row">
              <span className={`sw ${s.family}`} />
              {familyName(s.family)}
              <span className="v">{usd1(s.usd)}</span>
            </div>
          ))}
          <div className="row sum">
            {t("total")}
            <span className="v">{usd1(c.usd)}</span>
          </div>
        </>
      ) : (
        <div className="row">{c.kind === "zero" ? t("no_cost") : t("no_data_day")}</div>
      )}
    </div>
  );
}

export function CostChart({ m }: { m: Metrics }) {
  const [ref, width] = useWidth<HTMLDivElement>(300);
  const [active, setActive] = useState<number | null>(null);
  const g = costModel(m, width);
  const n = g.columns.length;
  const colW = (width - g.gap * (n - 1)) / n;
  const barW = Math.min(BAR, colW);
  const h = (v: number) => (v / g.max) * PH;
  const since = m.since.cost && m.days[0] && m.since.cost > m.days[0] ? m.since.cost : null;
  const per = since ? t("since_day", { day: dayMonth(since) }) : t("in_days", { n });
  const aria = t("g1_aria", {
    days: g.columns.map((c) => `${shortDay(c.day)} ${c.kind === "none" ? t("no_data_day") : usd(c.usd)}`).join(", "),
  });
  const shown = active === null ? null : g.columns[active];
  const tipLeft = active === null ? 0 : Math.max(0, Math.min(width - 160, active * (colW + g.gap) + colW / 2 - 80));
  return (
    <article className="card chart" data-chart="g1">
      <div>
        <h2>{t("g1_title")}</h2>
        <div className="figure">
          <span className="big">{usd(g.total)}</span>
          <span className="sub">
            {per} · {g.worked ? t("mean_worked", { mean: usd(g.mean) }) : t("no_work")}
          </span>
        </div>
      </div>
      <div className="legend">
        {g.families.map((f) => (
          <span key={f}>
            <span className={`sw ${f}`} />
            {familyName(f)}
          </span>
        ))}
      </div>
      <div className="plot" ref={ref} role="img" aria-label={aria} onPointerLeave={() => setActive(null)}>
        {g.ticks.map((v) => (
          <span key={v} className="ylabel" style={{ bottom: h(v) }}>
            {usd(v)}
          </span>
        ))}
        <svg width={width} height={PH} aria-hidden="true">
          {g.ticks.map((v) => (
            <line key={v} className={v === 0 ? "axis" : "grid"} x1={0} x2={width} y1={PH - h(v) + 0.5} y2={PH - h(v) + 0.5} />
          ))}
          {g.columns.map((c, i) => {
            const x = i * (colW + g.gap) + (colW - barW) / 2;
            // a figure on a bar: kept inside the plot, and above every bar it spans (narrow columns: 30 days)
            const top = (text: string, centre: number) => {
              const w = text.length * 6.5 + 4;
              const left = Math.max(0, Math.min(width - w, centre - w / 2));
              const under = g.columns.filter((_, j) => {
                const l = j * (colW + g.gap);
                return l < left + w && l + colW > left;
              });
              const tallest = Math.max(...under.map((u) => h(u.usd)));
              return (
                <text className="top" x={left + w / 2} y={Math.max(11, PH - tallest - 5)} textAnchor="middle">
                  {text}
                </text>
              );
            };
            let base = PH;
            const segs = c.segs.map((s, k) => {
              const sh = Math.max(STUB, h(s.usd) - (k ? SEG_GAP : 0));
              const top = k === c.segs.length - 1;
              const y = base - sh;
              base = y - SEG_GAP;
              return (
                <BarRounded key={s.family} className={`seg ${s.family}`} x={x} y={y} width={barW} height={sh}
                  radius={top ? 4 : 0} top={top} data-family={s.family} data-usd={s.usd} />
              );
            });
            return (
              <g key={c.day} data-day={c.day} data-kind={c.kind}>
                {c.kind === "cost" ? segs : <rect className={c.kind} x={x} y={PH - STUB} width={barW} height={STUB} rx={1} />}
                {c.top ? top(c.top, x + barW / 2) : null}
                <rect className="hit" x={i * (colW + g.gap)} y={0} width={colW} height={PH}
                  onPointerEnter={() => setActive(i)} onClick={() => setActive(active === i ? null : i)} />
              </g>
            );
          })}
        </svg>
        {shown ? <Tip c={shown} left={tipLeft} /> : null}
      </div>
      <div className="xlabels" aria-hidden="true" style={{ gap: g.gap }}>
        {g.columns.map((c) => (
          <span key={c.day} className={c.today ? "today" : undefined}>
            {c.label}
          </span>
        ))}
      </div>
      <p className="note">{t("g1_note")}</p>
    </article>
  );
}
