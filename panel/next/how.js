// «How it goes»: the period (7 | 30 days) and the charts. Today only G1, cost per day and model.
import { useEffect, useState } from "preact/hooks";
import { CostChart } from "next/g1.js";
import { api, html, t } from "next/lib.js";

export function How({ product, theme }) {
  const [days, setDays] = useState(7);
  const [m, setM] = useState(null);
  const [failed, setFailed] = useState(false);
  const [tries, setTries] = useState(0);
  useEffect(() => {
    let live = true;
    setFailed(false);
    setM(null);
    api(`/api/metrics?p=${encodeURIComponent(product)}&days=${days}`)
      .then((x) => live && setM(x))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [product, days, tries]);
  const empty = m && !m.since.cost;
  return html`<div class="screen">
    <div class="screenhead">
      <h1>${t("tab_how")}</h1>
      <div class="seg" role="group" aria-label=${t("period")}>
        ${[7, 30].map(
          (n) => html`<button aria-pressed=${days === n} onClick=${() => setDays(n)}>${t("n_days", { n })}</button>`,
        )}
      </div>
    </div>
    ${failed
      ? html`<div class="card quiet">${t("load_failed")} <button class="link" onClick=${() => setTries(tries + 1)}>${t("retry")}</button></div>`
      : !m
        ? html`<div class="card quiet">${t("loading")}</div>`
        : empty
          ? html`<div class="card quiet">${t("no_data")}</div>`
          : html`<${CostChart} m=${m} theme=${theme} />`}
  </div>`;
}
