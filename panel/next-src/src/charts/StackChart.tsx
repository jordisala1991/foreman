// The day-by-day bar charts of «How it goes» (G1 cost, G2 deliveries, G3 how agents end, G4 reviews, G7 guard
// refusals), all one component fed by a spec (specs.ts): stacked bars of one column per day, the period's figure
// above, a legend, a figure or pill on the bar, a detail box on hover or tap, a dashed grid. A day without work is a
// thin bar, and so is a day before the data starts (in a fainter tone). SVG drawn by React with visx's scales and
// rounded bars; the colours are CSS variables (app.css), so the theme needs no code.
import { scaleLinear } from "@visx/scale";
import { BarRounded } from "@visx/shape";
import { useState, type ReactNode } from "react";
import { shortDay, t } from "../lib";
import { axisLabels, gapOf, type Col, type Spec } from "./specs";
import { useWidth } from "./useWidth";

export const PH = 140; // the plot's height
const BAR = 28; // the widest a bar gets
const SEG_GAP = 2; // between the segments of a bar
const STUB = 2; // the height of the bar of a day without work

function Tip({ c, left }: { c: Col; left: number }) {
  return (
    <div role="tooltip" className="tip" style={{ left }}>
      <div className="tiphead">
        {shortDay(c.day)}
        {c.today ? ` · ${t("today")}` : ""}
      </div>
      {c.kind === "none" ? (
        <div className="row">{t("no_data_day")}</div>
      ) : c.rows.length ? (
        <>
          {c.rows.map((r) => (
            <div key={r.key} className="row">
              <span className={`sw ${r.key}`} />
              {r.label}
              <span className="v">{r.value}</span>
            </div>
          ))}
          {c.sum ? (
            <div className="row sum">
              {c.sum.label}
              <span className="v">{c.sum.value}</span>
            </div>
          ) : null}
        </>
      ) : (
        <div className="row">{t("no_cost")}</div>
      )}
    </div>
  );
}

export function Legend({ items }: { items: Spec["legend"] }) {
  return (
    <div className="legend">
      {items.map((l) => (
        <span key={l.key}>
          {l.pill ? <span className="pillmark">{l.pill}</span> : <span className={`sw ${l.key}`} />}
          {l.label}
        </span>
      ))}
    </div>
  );
}

export function StackChart({ spec, children }: { spec: Spec; children?: ReactNode }) {
  const [ref, width] = useWidth<HTMLDivElement>(300);
  const [active, setActive] = useState<number | null>(null);
  const cols = spec.columns;
  const n = cols.length;
  const gap = gapOf(n);
  const colW = (width - gap * (n - 1)) / n;
  const barW = Math.min(BAR, colW);
  const highest = Math.max(1e-9, ...cols.map((c) => c.total));
  const y = scaleLinear<number>({ domain: [0, Math.max(spec.floor, highest * 1.12)], nice: true });
  const max = y.domain()[1] ?? 1;
  const ticks = y.ticks(3);
  const h = (v: number) => (v / max) * PH;
  const labels = axisLabels(cols.map((c) => c.day), width);
  const shown = active === null ? null : cols[active];
  const tipLeft = active === null ? 0 : Math.max(0, Math.min(width - 160, active * (colW + gap) + colW / 2 - 80));
  // the figures on the bars: today's first, then the others from the left; one that would touch another is left out
  const placed = new Map<number, { text: string; left: number; w: number }>();
  const taken: [number, number][] = [];
  [...cols.keys()].sort((a, b) => Number(cols[b]?.today) - Number(cols[a]?.today)).forEach((i) => {
    const c = cols[i];
    if (!c?.top) return;
    const text = c.topShort && colW < c.top.length * 6.5 + 4 ? c.topShort : c.top;
    const w = text.length * 6.5 + (c.pill ? 12 : 4);
    const left = Math.max(0, Math.min(width - w, i * (colW + gap) + colW / 2 - w / 2));
    if (taken.some(([a, b]) => left < b + 4 && left + w + 4 > a)) return;
    taken.push([left, left + w]);
    placed.set(i, { text, left, w });
  });
  const firstNone = cols.findIndex((c) => c.kind !== "none");
  const nones = firstNone < 0 ? n : firstNone;
  return (
    <article className="card chart" data-chart={spec.id}>
      <div>
        <h2>{spec.title}</h2>
        <div className="figure">
          <span className="big">{spec.big}</span>
          <span className="sub">{spec.sub}</span>
        </div>
      </div>
      {spec.legend.length ? <Legend items={spec.legend} /> : null}
      <div className="plot" ref={ref} role="img" aria-label={spec.aria} onPointerLeave={() => setActive(null)}>
        {ticks.map((v) => (
          <span key={v} className="ylabel" style={{ bottom: h(v) }}>
            {spec.fmt(v)}
          </span>
        ))}
        <svg width={width} height={PH} aria-hidden="true">
          {ticks.map((v) => (
            <line key={v} className={v === 0 ? "axis" : "grid"} x1={0} x2={width} y1={PH - h(v) + 0.5} y2={PH - h(v) + 0.5} />
          ))}
          {cols.map((c, i) => {
            const x = i * (colW + gap) + (colW - barW) / 2;
            // a figure on a bar: kept inside the plot, and above every bar it spans (narrow columns: 30 days)
            const top = ({ text, left, w }: { text: string; left: number; w: number }) => {
              const under = cols.filter((_, j) => {
                const l = j * (colW + gap);
                return l < left + w && l + colW > left;
              });
              const tallest = Math.max(...under.map((u) => h(u.total)));
              const ty = Math.max(c.pill ? 20 : 11, PH - tallest - (c.pill ? 12 : 5));
              return c.pill ? (
                <g className="bar-pill" transform={`translate(${left + w / 2} ${ty})`}>
                  <rect x={-w / 2} y={-11} width={w} height={18} rx={9} />
                  <text textAnchor="middle" y={2}>
                    {text}
                  </text>
                </g>
              ) : (
                <text className="bar-figure" x={left + w / 2} y={ty} textAnchor="middle">
                  {text}
                </text>
              );
            };
            let base = PH;
            const segs = c.segs.map((s, k) => {
              const sh = Math.max(STUB, h(s.value) - (k ? SEG_GAP : 0));
              const isTop = k === c.segs.length - 1;
              const sy = base - sh;
              base = sy - SEG_GAP;
              return (
                <BarRounded key={s.key} className={`seg ${s.key}`} x={x} y={sy} width={barW} height={sh}
                  radius={isTop ? 4 : 0} top={isTop} data-key={s.key} data-value={s.value} />
              );
            });
            return (
              <g key={c.day} data-day={c.day} data-kind={c.kind}>
                {c.segs.length ? segs : <rect className={c.kind === "none" ? "none" : "zero"} x={x} y={PH - STUB} width={barW} height={STUB} rx={1} />}
                {placed.has(i) ? top(placed.get(i) as { text: string; left: number; w: number }) : null}
                <rect className="hit" x={i * (colW + gap)} y={0} width={colW} height={PH}
                  onPointerEnter={() => setActive(i)} onClick={() => setActive(active === i ? null : i)} />
              </g>
            );
          })}
        </svg>
        {spec.empty && nones > 0 ? (
          <div className="empty-in-plot" style={{ width: `${(nones / n) * 100}%` }}>
            {spec.empty}
          </div>
        ) : null}
        {shown ? <Tip c={shown} left={tipLeft} /> : null}
      </div>
      <div className="xlabels" aria-hidden="true" style={{ gap }}>
        {cols.map((c, i) => (
          <span key={c.day} className={c.today ? "today" : undefined}>
            {labels[i]}
          </span>
        ))}
      </div>
      {children}
      <p className="note">{spec.note}</p>
    </article>
  );
}
