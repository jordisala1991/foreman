// G8, the account's quota now: the 5-hour window and the week, a bar each with when it renews. Only the value of now,
// from Claude Code's last measure; there is no history. The amber mark is 80 %: from there the bar turns amber.
import { clock, shortDay, t, type Metrics } from "../lib";

const WARN = 80;
type Window = NonNullable<Metrics["quota"]>[number];

function renews(w: Window): string {
  const at = w.resets_at ? new Date(w.resets_at) : null;
  if (!at || Number.isNaN(at.getTime())) return "";
  const now = new Date();
  const sameDay = at.toDateString() === now.toDateString();
  const iso = `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(at.getDate()).padStart(2, "0")}`;
  return sameDay ? t("g8_today", { at: clock(at) }) : t("g8_on", { day: shortDay(iso), at: clock(at) });
}

function Meter({ name, w, aria }: { name: string; w: Window; aria: string }) {
  const p = Math.max(0, Math.min(100, w.percent_used));
  return (
    <div className="meter" data-window={w.kind}>
      <div className="row">
        <span>{name}</span>
        <b>{Math.round(w.percent_used)} %</b>
      </div>
      <div className="gauge" role="img" aria-label={aria}>
        <div className={p >= WARN ? "fill warn" : "fill"} style={{ width: `${p}%` }} />
        <span className="mark" style={{ left: `${WARN}%` }} />
      </div>
      <div className="when">{renews(w)}</div>
    </div>
  );
}

export function QuotaChart({ m }: { m: Metrics }) {
  const five = m.quota?.find((w) => w.kind === "five_hour");
  const week = m.quota?.find((w) => w.kind === "seven_day");
  return (
    <article className="card chart" data-chart="g8">
      <div>
        <h2>{t("g8_title")}</h2>
        <div className="figure">
          <span className="big">{week ? `${Math.round(week.percent_used)} %` : "—"}</span>
          <span className="sub">{week ? t("g8_sub") : t("g8_none")}</span>
        </div>
      </div>
      {five ? <Meter name={t("g8_five")} w={five} aria={t("g8_five_aria", { n: Math.round(five.percent_used) })} /> : null}
      {week ? <Meter name={t("g8_week")} w={week} aria={t("g8_week_aria", { n: Math.round(week.percent_used) })} /> : null}
      <p className="note">{t("g8_note")}</p>
    </article>
  );
}
