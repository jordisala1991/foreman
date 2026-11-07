// What each day-by-day chart draws, from /api/metrics (pure: StackChart only paints it). G1 cost per day and model,
// G2 deliveries, G3 how agents end, G4 reviews sent back, G7 guard refusals.
import { dayMonth, shortDay, t, usd, usd1, type Family, type Key, type Metrics } from "../lib";

export interface Col {
  day: string;
  today: boolean;
  kind: "data" | "zero" | "none"; // with data, a day without work, a day before the data starts
  segs: { key: string; value: number }[]; // with data only, from the bottom up
  total: number; // the height of the bar
  top: string; // the figure or pill on the bar ("" if none)
  topShort?: string; // the same in less room, used when the columns are narrow
  pill: boolean;
  rows: { key: string; label: string; value: string }[]; // the detail box, top down
  sum?: { label: string; value: string };
}

export interface Spec {
  id: string;
  title: string;
  big: string;
  sub: string;
  legend: { key: string; label: string; pill?: string }[];
  columns: Col[];
  floor: number; // the least the top of the y scale gets
  fmt: (v: number) => string; // a y label
  aria: string;
  note: string;
  empty?: string; // said over the days before the data starts
}

export const gapOf = (n: number): number => (n > 7 ? 2 : 6);

// the labels under the axis: «sáb 1» for 7 days, the day number for 30; with narrow columns one in three, counted
// back from today, so today always has its label
export function axisLabels(days: string[], width: number): string[] {
  const n = days.length;
  const colW = (width - gapOf(n) * (n - 1)) / n;
  const every = colW >= 22 ? 1 : 3;
  return days.map((d, i) => ((n - 1 - i) % every === 0 ? (n <= 7 ? shortDay(d) : String(Number(d.slice(8)))) : ""));
}

// «en 7 días», or «desde el 1 nov» when the series starts inside the period
export function per(m: Metrics, since: string | null): string {
  return since && m.days[0] && since > m.days[0] ? t("since_day", { day: dayMonth(since) }) : t("in_days", { n: m.days.length });
}

const kindOf = (day: string, v: number, since: string | null): Col["kind"] =>
  v > 0 ? "data" : since && day >= since ? "zero" : "none";

// the day with the highest bar, and today, carry a figure
function marked(m: Metrics, totals: number[]): Set<number> {
  const best = totals.reduce((b, v, i) => (v > (totals[b] ?? 0) ? i : b), 0);
  return new Set([best, m.days.length - 1]);
}

const pct = (a: number, b: number): string => (b ? `${Math.round((a / b) * 100)} %` : "—");

interface Piece {
  key: string;
  label: string;
}

// the common shape: each day gives values per piece (bottom up); a column is built from them
function columns(
  m: Metrics,
  since: string | null,
  pieces: Piece[],
  valuesOf: (day: string) => Record<string, number>,
  fmt: (v: number) => string,
  top: (c: { day: string; i: number; total: number; mark: boolean; values: Record<string, number> }) => string,
  pill = false,
  sum = true,
  totalOf?: (day: string) => number,
): Col[] {
  const sums = m.days.map((d) => {
    const v = valuesOf(d);
    return totalOf ? totalOf(d) : Object.values(v).reduce((a, b) => a + b, 0);
  });
  const mark = marked(m, sums);
  return m.days.map((day, i) => {
    const values = valuesOf(day);
    const total = sums[i] ?? 0;
    const kind = kindOf(day, total, since);
    const segs = kind === "data" ? pieces.map((p) => ({ key: p.key, value: values[p.key] ?? 0 })).filter((s) => s.value > 0) : [];
    const rows = kind === "data" ? [...pieces].reverse().map((p) => ({ key: p.key, label: p.label, value: fmt(values[p.key] ?? 0) })) : [];
    return {
      day,
      today: i === m.days.length - 1,
      kind,
      segs,
      total,
      top: top({ day, i, total, mark: mark.has(i), values }),
      pill,
      rows,
      ...(sum && kind === "data" ? { sum: { label: t("total"), value: fmt(total) } } : {}),
    };
  });
}

const aria = (key: Key, cols: Col[], show: (c: Col) => string): string =>
  t(key, { days: cols.map((c) => `${shortDay(c.day)} ${c.kind === "none" ? t("no_data_day") : show(c)}`).join(", ") });

export const FAMILIES: Family[] = ["opus", "sonnet", "haiku", "other"]; // from the bottom of a bar up
const NAMES: Record<Family, string> = { opus: "Opus", sonnet: "Sonnet", haiku: "Haiku", other: "" };
export const familyName = (f: Family): string => (f === "other" ? t("other_models") : NAMES[f]);

// G1 — cost per day and model, in list-price dollars
export function g1(m: Metrics): Spec {
  const families = FAMILIES.filter((f) => m.days.some((d) => (m.cost[d]?.[f] ?? 0) > 0));
  const cols = columns(
    m,
    m.since.cost,
    families.map((f) => ({ key: f, label: familyName(f) })),
    (d) => Object.fromEntries(families.map((f) => [f, m.cost[d]?.[f] ?? 0])),
    usd1,
    (c) => (c.total > 0 && c.mark ? usd(c.total) : ""),
    false,
    true,
    (d) => m.cost[d]?.usd ?? 0, // the day's own cost, not the sum of the families shown
  );
  const worked = cols.filter((c) => c.kind === "data").length;
  return {
    id: "g1",
    title: t("g1_title"),
    big: usd(m.total),
    sub: `${per(m, m.since.cost)} · ${worked ? t("mean_worked", { mean: usd(m.total / worked) }) : t("no_work")}`,
    legend: families.map((f) => ({ key: f, label: familyName(f) })),
    columns: cols,
    floor: 1,
    fmt: usd,
    aria: aria("g1_aria", cols, (c) => usd(c.total)),
    note: t("g1_note"),
  };
}

const sumOf = (xs: number[]): number => xs.reduce((a, b) => a + b, 0);
const dl = (m: Metrics, d: string) => m.deliveries[d] ?? { integrated: 0, measured: 0, ok: 0, partial: 0, failed: 0 };

// G2 — deliveries: changes integrated into main per day, measures closed on top as a pill
export function g2(m: Metrics): Spec {
  const integrated = sumOf(m.days.map((d) => dl(m, d).integrated));
  const measured = sumOf(m.days.map((d) => dl(m, d).measured));
  const cols = columns(
    m,
    m.since.deliveries,
    [{ key: "integrated", label: t("g2_integrated") }],
    (d) => ({ integrated: dl(m, d).integrated }),
    String,
    ({ day }) => (dl(m, day).measured ? String(dl(m, day).measured) : ""),
    true,
    false,
  ).map((c) => {
    const x = dl(m, c.day);
    const kind = x.integrated + x.measured > 0 ? (x.integrated > 0 ? "data" : "zero") : c.kind;
    const rows = kind === "none" ? [] : [
      { key: "integrated", label: t("g2_integrated"), value: String(x.integrated) },
      { key: "measured", label: t("g2_measured"), value: String(x.measured) },
    ];
    return { ...c, kind: kind as Col["kind"], rows };
  });
  return {
    id: "g2",
    title: t("g2_title"),
    big: String(integrated),
    sub: t("g2_sub", { per: per(m, m.since.deliveries), avg: Math.round(integrated / m.days.length), n: measured }),
    legend: [
      { key: "integrated", label: t("g2_integrated") },
      { key: "measured", label: t("g2_measured"), pill: "2" },
    ],
    columns: cols,
    floor: 4,
    fmt: String,
    aria: aria("g2_aria", cols, (c) => String(c.total)),
    note: t("g2_note"),
  };
}

// G3 — how agents end: complete (green), partly (amber), failed (red)
export function g3(m: Metrics): Spec {
  const ok = sumOf(m.days.map((d) => dl(m, d).ok));
  const partial = sumOf(m.days.map((d) => dl(m, d).partial));
  const failed = sumOf(m.days.map((d) => dl(m, d).failed));
  const all = ok + partial + failed;
  const pieces = [
    { key: "ok", label: t("g3_ok") },
    { key: "partial", label: t("g3_partial") },
    { key: "failed", label: t("g3_failed") },
  ];
  const cols = columns(
    m,
    m.since.deliveries,
    pieces,
    (d) => ({ ok: dl(m, d).ok, partial: dl(m, d).partial, failed: dl(m, d).failed }),
    String,
    ({ i, total }) => (total > 0 && i === m.days.length - 1 ? String(total) : ""),
  );
  return {
    id: "g3",
    title: t("g3_title"),
    big: pct(ok, all),
    sub: all ? `${t("g3_sub", { ok, all })}${failed ? t("g3_failed_n", { n: failed }) : t("g3_none_failed")}` : t("no_work"),
    legend: pieces,
    columns: cols,
    floor: 4,
    fmt: String,
    aria: aria("g3_aria", cols, (c) => String(c.total)),
    note: t("g3_note"),
  };
}

const rv = (m: Metrics, d: string) => m.reviews[d] ?? { clean: 0, back: 0 };

// G4 — reviews: clean (grey) and sent back (amber), «12 de 17» on the bar
export function g4(m: Metrics): Spec {
  const back = sumOf(m.days.map((d) => rv(m, d).back));
  const all = back + sumOf(m.days.map((d) => rv(m, d).clean));
  const pieces = [
    { key: "clean", label: t("g4_clean") },
    { key: "back", label: t("g4_back") },
  ];
  const cols = columns(
    m,
    m.since.reviews,
    pieces,
    (d) => ({ clean: rv(m, d).clean, back: rv(m, d).back }),
    String,
    ({ day, total, mark }) => (total > 0 && (m.days.length <= 7 || mark) ? t("g4_of", { back: rv(m, day).back, total }) : ""),
    false,
    false,
  ).map((c) => ({
    ...c,
    ...(c.top ? { topShort: `${rv(m, c.day).back}/${c.total}` } : {}),
    rows: c.kind === "data" ? [{ key: "back", label: t("g4_tip", { back: rv(m, c.day).back, total: c.total }), value: "" }] : c.rows,
  }));
  const since = m.since.reviews && m.days[0] && m.since.reviews > m.days[0] ? m.since.reviews : null;
  const rounds = Object.entries(m.rounds).map(([r, n]) => t("g4_round", { r, n })).join(", ");
  return {
    id: "g4",
    title: t("g4_title"),
    big: pct(back, all),
    sub: all ? `${t("g4_sub", { back, all })}${since ? t("g4_since", { day: shortDay(since) }) : ""}` : t("no_work"),
    legend: pieces,
    columns: cols,
    floor: 4,
    fmt: String,
    aria: aria("g4_aria", cols, (c) => t("g4_tip", { back: rv(m, c.day).back, total: c.total })),
    note: `${t("g4_note")}${rounds ? ` ${t("g4_rounds", { rounds })}` : ""}`,
    empty: t("no_data"),
  };
}

const gd = (m: Metrics, d: string) => m.guard[d] ?? { agent: 0, orchestrator: 0, other: 0 };

// G7 — guard refusals per day by who was refused
export function g7(m: Metrics): Spec {
  const total = sumOf(m.days.map((d) => sumOf(Object.values(gd(m, d)))));
  const pieces = [
    { key: "agent", label: t("g7_agent") },
    { key: "orchestrator", label: t("g7_orchestrator") },
    { key: "other", label: t("g7_other") },
  ];
  const last = m.days[m.days.length - 1] ?? "";
  const cols = columns(
    m,
    m.since.guard,
    pieces,
    (d) => ({ agent: gd(m, d).agent, orchestrator: gd(m, d).orchestrator, other: gd(m, d).other }),
    String,
    ({ day, total: v }) => (v > 0 && day === last ? String(v) : ""),
  );
  const since = m.since.guard && m.days[0] && m.since.guard > m.days[0] ? m.since.guard : null;
  return {
    id: "g7",
    title: t("g7_title"),
    big: String(total),
    sub: t("g7_sub", { per: per(m, m.since.guard), today: sumOf(Object.values(gd(m, last))) }),
    legend: pieces,
    columns: cols,
    floor: 4,
    fmt: String,
    aria: aria("g7_aria", cols, (c) => String(c.total)),
    note: since ? t("g7_note_since", { day: shortDay(since) }) : t("g7_note"),
  };
}
