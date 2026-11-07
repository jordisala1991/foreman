// «How it goes»: the period (7 | 30 days) and the eight charts, two columns on a wide screen. The data is read again
// every minute; when that fails the charts stay, dimmed, under a «no connection» line with the time of the data.
import { useEffect, useState } from "react";
import { QuotaChart } from "./charts/QuotaChart";
import { Rules } from "./charts/Rules";
import { ShareChart } from "./charts/ShareChart";
import { SpendChart } from "./charts/SpendChart";
import { g1, g2, g3, g4, g7 } from "./charts/specs";
import { StackChart } from "./charts/StackChart";
import { ScreenHead } from "./head";
import { api, clock, dayMonth, t, type Metrics, type Product } from "./lib";
import { Refresh } from "./icons";

const PERIODS = [7, 30] as const;
const REFRESH_MS = 60_000;

export function How({ product }: { product: Product }) {
  const [days, setDays] = useState<number>(7);
  const [m, setM] = useState<Metrics | null>(null);
  const [failed, setFailed] = useState(false); // the first answer did not come
  const [offline, setOffline] = useState<Date | null>(null); // a later one did not: when the data on screen is from
  const [tries, setTries] = useState(0);
  useEffect(() => {
    let live = true;
    const path = `/api/metrics?p=${encodeURIComponent(product.name)}&days=${days}`;
    let at = new Date();
    setFailed(false);
    setOffline(null);
    setM(null); // never the old product's or period's chart under the new choice
    const read = (first: boolean) =>
      api<Metrics>(path)
        .then((x) => {
          if (!live) return;
          at = new Date();
          setM(x);
          setOffline(null);
        })
        .catch(() => {
          if (!live) return;
          if (first) setFailed(true);
          else setOffline(at);
        });
    void read(true);
    const timer = setInterval(() => void read(false), REFRESH_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [product.name, days, tries]);
  const since = m?.since.cost && m.days[0] && m.since.cost > m.days[0] ? m.since.cost : null;
  return (
    <div className="screen">
      {offline ? (
        <div role="status" className="offline">
          <span>
            <Refresh />
            <strong>{t("offline")}</strong> · {t("offline_retrying")}
          </span>
          <span className="at">{t("offline_since", { at: clock(offline) })}</span>
        </div>
      ) : null}
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
        <div className={offline ? "charts dim" : "charts"}>
          <StackChart spec={g1(m)} />
          <StackChart spec={g2(m)} />
          <StackChart spec={g3(m)} />
          <StackChart spec={g4(m)} />
          <ShareChart m={m} />
          <SpendChart m={m} />
          <StackChart spec={g7(m)}>
            <Rules m={m} />
          </StackChart>
          <QuotaChart m={m} />
        </div>
      )}
    </div>
  );
}
