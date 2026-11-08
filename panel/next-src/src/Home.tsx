// Home: what needs the owner (the first three cards), who is working, today in figures (the four tiles, or the
// products' sum under «All») and the latest lines of the agents. On the phone a question shows its title and its
// options only; on a wide screen it is whole, as in «Needs you».
import { useEffect, useState, type ReactElement } from "react";
import { LoadFailed, PermissionCard, QuestionCard, askedSince } from "./cards";
import { useScope } from "./data";
import { ScreenHead, Skel } from "./head";
import { Small } from "./icons";
import { contextOf, openGrants, openQuestions, summary, waiting } from "./Needs";
import {
  dayWord, hhmm, minutesSince, shortDay, t, todayIso, usd, usd1, type Agent, type Metrics, type Product, type Quota,
} from "./lib";

const WIDE = "(min-width: 900px)";
export function useWide(): boolean {
  const mq = typeof matchMedia === "function" ? matchMedia(WIDE) : null;
  const [wide, setWide] = useState(mq?.matches ?? false);
  useEffect(() => {
    if (!mq) return;
    const on = () => setWide(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [mq]);
  return wide;
}

// ---------- who is working ----------

type Tone = "ok" | "wait" | "stuck";
function stateOf(a: Agent): { key: Parameters<typeof t>[0]; tone: Tone } {
  switch (a.status) {
    case "waiting for a lock":
      return { key: "st_lock", tone: "wait" };
    case "maybe stuck":
      return { key: "st_stuck", tone: "stuck" };
    case "waiting for its task":
      return { key: "st_task", tone: "wait" };
    default:
      return { key: "st_working", tone: "ok" };
  }
}
const KINDS: Record<string, Parameters<typeof t>[0]> = {
  dev: "kind_dev", mechanic: "kind_dev", reviewer: "kind_review", planner: "kind_plan",
};
const initials = (s: string): string => s.replace(/[^\p{L}\p{N} ]/gu, " ").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "").join("");
const HUES = ["#9cc0ff", "#f2a9cb", "#e3c49c", "#ffb98a", "#86d4e6", "#b9e0a5"];
const hue = (s: string): string => HUES[[...s].reduce((n, c) => n + c.charCodeAt(0), 0) % HUES.length] ?? "#9cc0ff";

function AgentCard({ a, shorts, tag }: { a: Agent; shorts: Record<string, string>; tag: string | null }) {
  const st = stateOf(a);
  const title = (a.issue && shorts[a.issue]) || a.name;
  const mins = minutesSince(a.start_ts);
  const quiet = minutesSince(a.activity_ts);
  const msg = st.tone === "stuck" && quiet !== null ? t("no_signs_n", { n: quiet }) : (a.last ?? "");
  const kind = a.type ? KINDS[a.type] : undefined;
  return (
    <article className="card agent">
      <div className="avatar" aria-hidden="true" style={{ background: hue(a.name) }}>
        {initials(title)}
      </div>
      <div className="body">
        <div className="line1">
          <h3>{title}</h3>
          {a.issue ? <span className="id">{a.issue}</span> : null}
        </div>
        <div className="line2">
          <span>{[a.sub, kind ? t(kind) : null].filter(Boolean).join(" · ")}</span>
          {tag ? <span className="prodtag">{tag}</span> : null}
        </div>
        {msg ? <p className={st.tone === "stuck" ? "msg2 alert" : "msg2"}>{msg}</p> : null}
        <div className="line3">
          <span className="status">
            <span className={`dot ${st.tone}`} />
            <span className={st.tone === "ok" ? "" : `strong ${st.tone}`}>{t(st.key)}</span>
            {mins !== null ? <span>· {t("for_min", { n: mins })}</span> : null}
          </span>
          {a.link ? (
            <a className="conv" href={a.link} target="_blank" rel="noreferrer">
              <Small name="eye" />
              {t("see_conversation")}
            </a>
          ) : null}
        </div>
      </div>
    </article>
  );
}

// ---------- today in figures ----------

const DAY_INITIAL = (iso: string): string => new Intl.DateTimeFormat(document.documentElement.lang || "en", { weekday: "narrow" }).format(new Date(iso + "T12:00:00"));

function Spark({ days, values, max }: { days: string[]; values: (number | null)[]; max: number }) {
  const last = days.length - 1;
  return (
    <div aria-hidden="true">
      <div className="spark">
        {values.map((v, i) => (
          <div key={days[i]} className="cell">
            <div className={v === null ? "b nodata" : i === last ? "b today" : "b"} style={{ height: v === null ? 2 : Math.max(2, Math.round(((v ?? 0) / (max || 1)) * 40)) }} />
          </div>
        ))}
      </div>
      <div className="sparklabels">
        {days.map((d, i) => (
          <span key={d} className={i === last ? "today" : ""}>
            {DAY_INITIAL(d)}
          </span>
        ))}
      </div>
    </div>
  );
}

const meter = (pct: number, label: string) => (
  <div className="meter" role="img" aria-label={label}>
    <div style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
  </div>
);
const hourOf = (s: string): string => new Intl.DateTimeFormat(document.documentElement.lang || "en", { hour: "2-digit", minute: "2-digit" }).format(new Date(s));
const localIso = (s: string): string => {
  const d = new Date(s);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const renews = (q: Quota, week: boolean): string =>
  !q.resets_at ? "" : week ? t("renews_on", { when: `${shortDay(localIso(q.resets_at))} ${t("at_time", { time: hourOf(q.resets_at) })}` }) : t("renews_at", { time: hourOf(q.resets_at) });

function QuotaTile({ quota, extra }: { quota: Quota[]; extra?: string }) {
  const five = quota.find((q) => q.kind === "five_hour");
  const week = quota.find((q) => q.kind === "seven_day");
  return (
    <div className="tile" data-tile="quota">
      <div className="label">{t("tile_quota")}</div>
      {five ? (
        <>
          <div className="big">
            <span className="v">{Math.round(five.percent_used)} %</span>
            <span className="unit">{t("of_5h")}</span>
          </div>
          {meter(five.percent_used, t("quota_aria_5h", { n: Math.round(five.percent_used) }))}
          <div className="sub">{renews(five, false)}</div>
        </>
      ) : (
        <>
          <div className="big">
            <span className="v">—</span>
          </div>
          <div className="sub">{t("quota_none")}</div>
        </>
      )}
      {week ? (
        <>
          <div className="weekrow">
            <span>{t("week")}</span>
            <strong>{Math.round(week.percent_used)} %</strong>
          </div>
          {meter(week.percent_used, t("quota_aria_week", { n: Math.round(week.percent_used) }))}
          <div className="sub">{renews(week, true)}</div>
        </>
      ) : null}
      {extra ? <div className="sub">{extra}</div> : null}
    </div>
  );
}

const reviewBars = (m: Metrics): (number | null)[] => {
  const first = (m as { since?: Record<string, string | null> }).since?.reviews ?? null;
  return m.days.slice(-7).map((d) => {
    const r = m.reviews?.[d];
    if (!r || !first || d < first) return null;
    const n = r.clean + r.back;
    return n ? (r.back / n) * 100 : 0;
  });
};

function Tiles({ m, quota }: { m: Metrics; quota: Quota[] }) {
  const td = m.today;
  if (!td) return null;
  const days = m.days.slice(-7);
  const c = td.cost;
  const fam = (["opus", "sonnet", "haiku", "other"] as const).filter((f) => c[f] > 0);
  const rv = td.reviews;
  return (
    <div className="tiles">
      <div className="tile" data-tile="cost">
        <div className="label">{t("tile_cost")}</div>
        <div className="big"><span className="v">{usd1(c.usd)}</span></div>
        <div className="fams">
          {fam.map((f) => (
            <span key={f}><span className={`sw ${f}`} />{f === "other" ? t("other_models") : f[0]?.toUpperCase() + f.slice(1)} {usd1(c[f])}</span>
          ))}
        </div>
        <div className="sub dim">{t("avg_before", { avg: usd(c.avg_before) })}</div>
        <Spark days={days} values={c.spark} max={Math.max(...c.spark, 1)} />
      </div>
      <div className="tile" data-tile="integrated">
        <div className="label">{t("tile_integrated")}</div>
        <div className="big"><span className="v">{td.integrated.integrated}</span><span className="unit">{t("changes")}</span></div>
        {td.integrated.measured ? <div className="sub">{t("and_measures", { n: td.integrated.measured })}</div> : null}
        <div className="sub dim">{t("yesterday_n", { n: td.integrated.yesterday })}</div>
        <Spark days={days} values={td.integrated.spark} max={Math.max(...td.integrated.spark, 1)} />
      </div>
      <QuotaTile quota={quota} />
      <div className="tile" data-tile="reviews">
        <div className="label">{t("tile_reviews")}</div>
        <div className="big"><span className="v">{rv.back}</span><span className="unit">{t("of_n", { n: rv.total })}</span></div>
        <div className="sub">{t("asked_changes_today")}</div>
        <div className="sub dim">{t("yesterday_of", { a: rv.yesterday_back, b: rv.yesterday_total })}</div>
        <Spark days={days} values={reviewBars(m)} max={100} />
      </div>
    </div>
  );
}

// «All»: each tile the sum of the products with a bar per product; the quota is the account's, not summed
function SumTiles({ scope, ms, quota }: { scope: Product[]; ms: (Metrics | undefined)[]; quota: Quota[] }) {
  const rows = scope.map((p, i) => ({ p, td: ms[i]?.today }));
  const sum = (f: (td: NonNullable<Metrics["today"]>) => number) => rows.reduce((n, r) => n + (r.td ? f(r.td) : 0), 0);
  const bars = (f: (td: NonNullable<Metrics["today"]>) => number, fmt: (n: number) => string) => {
    const max = Math.max(...rows.map((r) => (r.td ? f(r.td) : 0)), 1);
    return (
      <div className="prows">
        {rows.map(({ p, td }) => (
          <div key={p.name} className="prow">
            <span>{p.title}</span>
            <span className="track"><span style={{ width: `${((td ? f(td) : 0) / max) * 100}%` }} /></span>
            <span className="v">{td ? fmt(f(td)) : "—"}</span>
          </div>
        ))}
      </div>
    );
  };
  const back = sum((x) => x.reviews.back);
  const total = sum((x) => x.reviews.total);
  const five = quota.find((q) => q.kind === "five_hour");
  const week = quota.find((q) => q.kind === "seven_day");
  return (
    <div className="tiles">
      <div className="tile" data-tile="cost">
        <div className="label">{t("tile_cost")}</div>
        <div className="big"><span className="v">{usd1(sum((x) => x.cost.usd))}</span></div>
        <div className="sub dim">{t("avg_before", { avg: usd(sum((x) => x.cost.avg_before)) })}</div>
        {bars((x) => x.cost.usd, usd1)}
      </div>
      <div className="tile" data-tile="integrated">
        <div className="label">{t("tile_integrated")}</div>
        <div className="big"><span className="v">{sum((x) => x.integrated.integrated)}</span><span className="unit">{t("changes")}</span></div>
        <div className="sub dim">{t("yesterday_n", { n: sum((x) => x.integrated.yesterday) })}</div>
        {bars((x) => x.integrated.integrated, String)}
      </div>
      <div className="tile" data-tile="reviews">
        <div className="label">{t("tile_reviews")}</div>
        <div className="big"><span className="v">{back}</span><span className="unit">{t("of_n", { n: total })}</span></div>
        <div className="sub dim">{t("asked_changes_today")}</div>
        {bars((x) => x.reviews.back, (n) => String(n))}
      </div>
      <QuotaTile quota={five || week ? quota : []} extra={t("quota_shared", { n: scope.length })} />
    </div>
  );
}

// ---------- the screen ----------

const SHOWN = 3;

export function Home({ scope, all }: { scope: Product[]; all: boolean }) {
  const wide = useWide();
  const { data, failed, metricsFailed, reload } = useScope(scope.map((p) => p.name), true);
  const head = scope[0];
  if (!head) return null;
  const w = waiting(scope, data);
  const loaded = scope.every((p) => data[p.name]?.state);
  const tag = (p: Product) => (all ? p.title : null);
  const asks = openGrants(w) + openQuestions(w);
  const live = scope.flatMap((p) => (data[p.name]?.state?.agents ?? []).filter((a) => !a.end).map((a) => ({ a, p })));
  const stuckFirst = [...live].sort((x, y) => Number(y.a.status === "maybe stuck") - Number(x.a.status === "maybe stuck"));
  const ms = scope.map((p) => data[p.name]?.metrics);
  const quota = ms.find((m) => m?.quota?.length)?.quota ?? [];
  const latest = scope
    .flatMap((p) => (data[p.name]?.state?.progress ?? []).slice(0, 5).map((x) => ({ x, p })))
    .sort((a, b) => b.x.time.localeCompare(a.x.time))
    .slice(0, 3);
  const nWait = asks + w.messages.length;
  const now = new Date();
  const kicker = new Intl.DateTimeFormat(document.documentElement.lang || "en", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })
    .format(now)
    .replace(/^./, (c) => c.toUpperCase())
    .replace(",", " ·");
  type Item = { key: string; node: ReactElement };
  const items: Item[] = [
    ...w.grants.map(({ g, product }) => ({
      key: `g/${product.name}/${g.group}`,
      node: <PermissionCard g={g} product={product.name} tag={tag(product)} variant={wide ? "home" : "short"} reload={reload} />,
    })),
    ...w.questions.map(({ d, product }) => ({
      key: `q/${product.name}/${d.id}`,
      node: (
        <QuestionCard d={d} product={product.name} tag={tag(product)} mode={wide ? "full" : "short"} reload={reload}
          context={wide ? contextOf(d, data[product.name]?.shorts ?? {}) : t("kind_question")} since={wide ? askedSince(d) : d.date ? dayWord(d.date) : ""} />
      ),
    })),
  ].slice(0, SHOWN);
  return (
    <div className="screen home">
      <ScreenHead title={t("tab_home")} product={head} all={all ? scope : undefined} kicker={kicker} />
      <div className="home-grid">
        <section aria-label={t("tab_needs")}>
          <div className="sechead">
            <h2>{t("tab_needs")}</h2>
            {nWait ? <a href="#/needs">{nWait > 1 ? t("see_n", { n: nWait }) : t("see_one")}</a> : null}
          </div>
          {items.map((i) => <div key={i.key} className="slot">{i.node}</div>)}
          {loaded && !asks ? (
            <div className="card empty row">
              <span className="round"><Small name="check" size={20} /></span>
              <div>
                <div className="title">{t("nothing_waits")}</div>
                <div className="sub">{t("nothing_waits_short")}</div>
              </div>
            </div>
          ) : null}
          {!loaded && !failed ? <Skel h={160} /> : null}
          {failed ? <LoadFailed retry={reload} /> : null}
          {loaded && w.messages.length && !asks ? <p className="note">{summary(w)}</p> : null}
        </section>
        <section aria-label={t("working")}>
          <div className="sechead">
            <h2>{t("working")}</h2>
            {live.length ? <a href="#/agents">{live.length > SHOWN ? t("see_all_n", { n: live.length }) : t("see_all")}</a> : null}
          </div>
          {stuckFirst.slice(0, SHOWN + 1).map(({ a, p }) => (
            <AgentCard key={`${p.name}/${a.id}`} a={a} shorts={data[p.name]?.shorts ?? {}} tag={tag(p)} />
          ))}
          {loaded && !live.length ? (
            <div className="card empty row">
              <span className="round quiet" />
              <div>
                <div className="title">{t("nobody_working")}</div>
              </div>
            </div>
          ) : null}
        </section>
      </div>
      <section aria-label={t("today_figures")}>
        <div className="sechead">
          <h2>{all ? t("today_figures_all", { n: scope.length }) : t("today_figures")}</h2>
          <a href="#/how">{t("tab_how")}</a>
        </div>
        {ms.every((m) => m?.today) ? (
          all ? <SumTiles scope={scope} ms={ms} quota={quota} /> : ms[0] ? <Tiles m={ms[0]} quota={quota} /> : null
        ) : metricsFailed ? (
          <LoadFailed retry={reload} />
        ) : (
          <Skel h={120} />
        )}
      </section>
      {latest.length ? (
        <section aria-label={t("latest")}>
          <h2 className="plain">{t("latest")}</h2>
          <ul className="latest">
            {latest.map(({ x, p }) => (
              <li key={`${p.name}/${x.time}/${x.text}`}>
                <span className="time">{x.time.slice(0, 10) === todayIso() ? hhmm(x.time) : dayWord(x.time)}</span>
                <span className="text">{x.agent ? <strong>{x.agent}</strong> : null} {x.text}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
