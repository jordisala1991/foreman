// «How it goes»: the period (7 | 30 days) and the eight charts, two columns on a wide screen. The data is read again
// every minute; when that fails the charts stay, dimmed, under a «no connection» line with the time of the data.
import { useState } from "react";
import { useFeed } from "./feed";
import { QuotaChart } from "./charts/QuotaChart";
import { Rules } from "./charts/Rules";
import { ShareChart } from "./charts/ShareChart";
import { SpendChart } from "./charts/SpendChart";
import { g1, g2, g3, g4, g7 } from "./charts/specs";
import { StackChart } from "./charts/StackChart";
import { ScreenHead } from "./head";
import { clock, dayMonth, t, type Metrics, type Product } from "./lib";
import { Refresh } from "./icons";

const PERIODS = [7, 30] as const;
const REFRESH_MS = 60_000;

export function How({ product }: { product: Product }) {
  const [days, setDays] = useState<number>(7);
  const feed = useFeed<Metrics>(`/api/metrics?p=${encodeURIComponent(product.name)}&days=${days}`, REFRESH_MS);
  // the figures stay while they are read again; a failed first read offers to retry, a later one dims what is there
  // under «no connection» with the time of the data
  const m = feed.data ?? null;
  const failed = feed.failed && !m;
  const offline = feed.failed && m ? feed.since : null;
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
          <button type="button" className="link" onClick={feed.reload}>
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
