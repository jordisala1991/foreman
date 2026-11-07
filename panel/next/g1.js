// G1, cost per day and model: stacked bars (Opus, Sonnet, Haiku, other) of list-price dollars, one bar per day of
// the period, today marked; a day without work keeps a thin grey bar, not a gap. Chart.js is the pre-built copy in
// vendor/ (window.Chart, loaded by index.html); the series come from /api/metrics (metrics.cost).
import { useEffect, useRef } from "preact/hooks";
import { dayLabel, html, t, usd } from "next/lib.js";

// model colours approved for the panel (validated for colour blindness, light and dark)
const MODELS = {
  opus: { label: "Opus", light: "#5e5ce6", dark: "#7a78f0" },
  sonnet: { label: "Sonnet", light: "#0f8f80", dark: "#1aa894" },
  haiku: { label: "Haiku", light: "#2a78d6", dark: "#3987e5" },
  other: { label: "other_models", light: "#8e8e93", dark: "#8e8e93" },
};
const INK = {
  light: { surface: "#ffffff", text: "#1d1d1f", muted: "#6e6e73", grid: "rgba(0,0,0,0.07)", empty: "#d2d2d7" },
  dark: { surface: "#16181c", text: "#f5f5f7", muted: "#8e8e93", grid: "rgba(255,255,255,0.08)", empty: "#3a3a3f" },
};

// what the card says above the chart: the period's total and the mean of the days with work
export function summary(m) {
  const worked = m.days.filter((d) => m.cost[d].usd > 0).length;
  return { total: m.total, mean: worked ? m.total / worked : 0, worked };
}

export function chartConfig(m, theme) {
  const ink = INK[theme];
  const today = m.days.length - 1;
  const long = m.days.length <= 7;
  const fams = Object.keys(MODELS).filter((f) => m.days.some((d) => m.cost[d][f] > 0));
  const top = (i) => fams.filter((f) => m.cost[m.days[i]][f] > 0).pop(); // the highest segment of bar i
  const datasets = fams.map((f) => ({
    label: MODELS[f].label === "other_models" ? t("other_models") : MODELS[f].label,
    data: m.days.map((d) => m.cost[d][f]),
    backgroundColor: MODELS[f][theme],
    borderColor: ink.surface,
    borderWidth: { top: 2, left: 0, right: 0, bottom: 0 }, // the 2 px gap between stacked segments
    borderSkipped: "start",
    borderRadius: (c) => (top(c.dataIndex) === f ? { topLeft: 4, topRight: 4 } : 0),
    maxBarThickness: 28,
  }));
  datasets.push({
    label: "",
    data: m.days.map((d) => (m.cost[d].usd > 0 || !m.since || d < m.since ? null : 0)), // none before the data starts
    backgroundColor: ink.empty,
    minBarLength: 3,
    maxBarThickness: 28,
  });
  const label = (iso, i) =>
    i === today ? t("today") : long ? dayLabel(iso, { weekday: "short", day: "numeric" }) : dayLabel(iso, { day: "numeric" });
  const labels = m.days.map(label);
  // Chart.js's own label skipping can drop «today»; on a narrow chart with many days, show every third label
  // counted back from today instead, so «today» is always there.
  const tick = function (v, i) {
    const narrow = m.days.length > 7 && ((this && this.chart && this.chart.width) || 0) < 700;
    return !narrow || (today - i) % 3 === 0 ? labels[i] : "";
  };
  return {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          filter: (c) => c.dataset.label && c.parsed.y > 0,
          callbacks: {
            title: (items) => dayLabel(m.days[items[0].dataIndex], { weekday: "long", day: "numeric", month: "short" }),
            label: (c) => ` ${c.dataset.label}: $${c.parsed.y.toFixed(2)}`,
            footer: (items) => t("total_day", { usd: "$" + m.cost[m.days[items[0].dataIndex]].usd.toFixed(2) }),
          },
        },
      },
      scales: {
        x: {
          stacked: true,
          grid: { display: false },
          border: { display: false },
          ticks: {
            color: (c) => (c.index === today ? ink.text : ink.muted),
            font: (c) => ({ weight: c.index === today ? "600" : "400" }),
            autoSkip: false,
            callback: tick,
            maxRotation: 0,
          },
        },
        y: {
          stacked: true,
          grid: { color: ink.grid },
          border: { display: false },
          ticks: { color: ink.muted, maxTicksLimit: 5, callback: (v) => "$" + v },
        },
      },
    },
  };
}

export function CostChart({ m, theme }) {
  const canvas = useRef(null);
  const missing = !window.Chart;
  useEffect(() => {
    if (!window.Chart || !canvas.current) return undefined;
    const chart = new window.Chart(canvas.current, chartConfig(m, theme));
    return () => chart.destroy();
  }, [m, theme]);
  const s = summary(m);
  const fams = Object.keys(MODELS).filter((f) => m.days.some((d) => m.cost[d][f] > 0));
  const by = (f) => m.days.reduce((a, d) => a + m.cost[d][f], 0);
  const name = (f) => (f === "other" ? t("other_models") : MODELS[f].label);
  const since = m.since && m.since > m.days[0] ? t("data_since", { day: dayLabel(m.since, { day: "numeric", month: "short" }) }) : "";
  const said = t("g1_summary", { total: usd(s.total), days: m.days.length, mean: usd(s.mean) });
  return html`<section class="card chart">
    <header>
      <h2>${t("g1_title")}</h2>
      <p class="figure"><b>${usd(s.total)}</b> ${t("in_days", { n: m.days.length })}</p>
      <p class="sub">${s.worked ? t("mean_worked", { mean: usd(s.mean) }) : t("no_work")}</p>
    </header>
    <ul class="legend">
      ${fams.map((f) => html`<li><i class=${"sw " + f}></i>${name(f)} <span>${usd(by(f))}</span></li>`)}
    </ul>
    ${missing
      ? html`<p class="quiet">${t("chart_missing")}</p>`
      : html`<div class=${"plot" + (m.days.length > 7 ? " wide" : "")}><canvas ref=${canvas} role="img" aria-label=${said}></canvas></div>`}
    <p class="note">${t("g1_note")}${since ? " · " + since : ""}</p>
    <details class="table">
      <summary>${t("see_table")}</summary>
      <table>
        <tr><th>${t("day")}</th>${fams.map((f) => html`<th>${name(f)}</th>`)}<th>${t("total")}</th></tr>
        ${m.days.map((d) => html`<tr><td>${dayLabel(d, { weekday: "short", day: "numeric", month: "short" })}</td>
          ${fams.map((f) => html`<td>${m.cost[d][f].toFixed(2)}</td>`)}<td>${m.cost[d].usd.toFixed(2)}</td></tr>`)}
      </table>
    </details>
  </section>`;
}
