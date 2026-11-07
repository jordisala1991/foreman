// «How it goes»: the period (7 | 30 days) and the charts, two columns on a wide screen. Today only G1, cost per day
// and model.
import { useEffect, useState } from "react";
import { CostChart } from "./charts/CostChart";
import { api, dayMonth, t, type Metrics, type Product } from "./lib";
import { ScreenHead } from "./head";

const PERIODS = [7, 30] as const;

export function How({ product }: { product: Product }) {
  const [days, setDays] = useState<number>(7);
  const [m, setM] = useState<Metrics | null>(null);
  const [failed, setFailed] = useState(false);
  const [tries, setTries] = useState(0);
  useEffect(() => {
    let live = true;
    setFailed(false);
    setM(null); // never the old product's or period's chart under the new choice
    api<Metrics>(`/api/metrics?p=${encodeURIComponent(product.name)}&days=${days}`)
      .then((x) => live && setM(x))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [product.name, days, tries]);
  const since = m?.since.cost && m.days[0] && m.since.cost > m.days[0] ? m.since.cost : null;
  return (
    <div className="screen">
      <ScreenHead title={t("tab_how")} product={product}>
        <div className="seg" role="group" aria-label={t("period")}>
          {PERIODS.map((n) => (
            <button key={n} type="button" aria-pressed={days === n} onClick={() => setDays(n)}>
              {t("n_days", { n })}
            </button>
          ))}
        </div>
        {since ? <span className="since">{t("data_since", { day: dayMonth(since) })}</span> : null}
      </ScreenHead>
      {failed ? (
        <div className="card quiet">
          {t("load_failed")}{" "}
          <button type="button" className="link" onClick={() => setTries(tries + 1)}>
            {t("retry")}
          </button>
        </div>
      ) : !m ? (
        <div className="card quiet">{t("loading")}</div>
      ) : !m.since.cost ? (
        <div className="card quiet">{t("no_data")}</div>
      ) : (
        <div className="charts">
          <CostChart m={m} />
        </div>
      )}
    </div>
  );
}
