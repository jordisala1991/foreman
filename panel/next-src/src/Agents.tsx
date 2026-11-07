// «Agents», as in the approved mockups: the cards of who is running (what they work on, their state and how long,
// their last words, «See conversation» for a session agent), then who finished today with result, time, model and
// list-price cost. With «All» (several products) each entry carries its product's tag. It asks /api/agents again
// every 10 s and the durations tick by themselves.
import { useCallback, useEffect, useState } from "react";
import { ScreenHead } from "./head";
import { api, duration, lang, post, t, usd2, type AgentCard, type AgentsData, type Family, type Key, type Product } from "./lib";

const REFRESH_MS = 10_000;
const AVATARS = ["#9cc0ff", "#f2a9cb", "#e3c49c", "#ffb98a", "#86d4e6", "#d6b3f0", "#a8c5d9", "#c9d98f"];
const MODEL: Record<Family, string> = { opus: "Opus", sonnet: "Sonnet", haiku: "Haiku", other: "" };

// the initials on the avatar: the first letters of the first two words of the name; its colour, from the name
const initials = (s: string): string =>
  (s.split(/\s+/).filter((w) => w.length > 3).slice(0, 2).map((w) => w[0]).join("") || s.slice(0, 2)).toUpperCase();
const avatar = (s: string): string => AVATARS[[...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 997, 7) % AVATARS.length] ?? "";

const KINDS = ["dev", "mechanic", "planner", "reviewer"];
// «T1 · desarrollo»: the part and the kind of agent (foreman:dev → dev)
function kind(sub: string | null, type: string | null): string {
  const k = (type ?? "").split(":").pop() ?? "";
  const word = KINDS.includes(k) ? t(`ty_${k}` as Key) : k;
  return [sub, word].filter(Boolean).join(" · ");
}

const Chip = ({ family }: { family: Family | null }) =>
  family && MODEL[family] ? <span className={`chip ${family === "opus" ? "opus" : "cheap"}`}>{MODEL[family]}</span> : null;

type Tone = "ok" | "wait" | "stuck";
const STATE: Record<AgentCard["status"], { tone: Tone; text: Key }> = {
  working: { tone: "ok", text: "st_working" },
  "holds a lock": { tone: "ok", text: "st_working" },
  "waiting for a lock": { tone: "wait", text: "st_lock" },
  "waiting for its task": { tone: "wait", text: "st_task" },
  "maybe stuck": { tone: "stuck", text: "st_stuck" },
};

function Watch({ agent }: { agent: AgentCard }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ link?: string; error?: string } | null>(null);
  // the window is opened by the click itself (a browser blocks one opened after the wait) and pointed at the link
  // when it comes: the server turns Remote Control on for the session, which takes seconds
  const open = async () => {
    const w = window.open("", "_blank");
    setBusy(true);
    setNote(null);
    try {
      const { link } = await post<{ link: string }>(
        `/api/agent/watch?id=${encodeURIComponent(agent.id)}&p=${encodeURIComponent(agent.p)}`,
      );
      if (w) w.location.href = link;
      else setNote({ link });
    } catch (e) {
      w?.close();
      setNote({ error: e instanceof Error ? e.message : String(e) });
    }
    setBusy(false);
  };
  return (
    <>
      <button type="button" className="watch" disabled={busy} onClick={() => void open()}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M4 5h16v11H9l-5 4z" />
        </svg>
        {t(busy ? "watch_busy" : "watch")}
      </button>
      {note?.link ? (
        <p className="msg">
          {t("watch_blocked")}:{" "}
          <a href={note.link} target="_blank" rel="noopener">
            {note.link}
          </a>
        </p>
      ) : null}
      {note?.error ? <p className="msg alert">{t("watch_failed", { text: note.error })}</p> : null}
    </>
  );
}

function Card({ a, now, tag, one }: { a: AgentCard; now: number; tag: string | null; one: boolean }) {
  const s = STATE[a.status];
  const stuck = s.tone === "stuck";
  const quiet = a.sign ? Math.round((now - a.sign) / 60) : 0;
  const msg = stuck && quiet > 0 ? t("no_signs", { time: duration(quiet) }) : a.msg;
  return (
    <article className={stuck ? "agent stuck" : "agent"}>
      <div className="avatar" aria-hidden="true" style={{ background: avatar(a.task) }}>
        {initials(a.task)}
      </div>
      <div className="body">
        <div className="headline">
          <h3>{a.task}</h3>
          {a.issue ? <span className="id">{a.issue}</span> : null}
        </div>
        <div className="meta">
          <span>{kind(a.sub, a.type)}</span>
          <Chip family={a.family} />
          {tag ? <span className="tag">{tag}</span> : null}
        </div>
        <div className="state">
          <span className={`dot ${s.tone}`} />
          <span className={`st ${s.tone}`}>{t(s.text)}</span>
          {a.since ? <span>· {t("for_time", { time: duration((now - a.since) / 60) })}</span> : null}
        </div>
        {msg ? <p className={`msg${stuck ? " alert" : ""}${one ? "" : " one"}`}>{msg}</p> : null}
        {a.session ? <Watch agent={a} /> : null}
      </div>
    </article>
  );
}

// «19:38»: the hour of an epoch, 24 h
const clock = (epoch: number): string =>
  new Intl.DateTimeFormat(lang(), { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(epoch * 1000));

const RESULT = { ok: "res_ok", partial: "res_partial", failed: "res_failed" } as const;

export function Agents({ product, titles }: { product: Product; titles: Record<string, string> }) {
  const [data, setData] = useState<AgentsData | null>(null);
  const [failed, setFailed] = useState(false);
  const [tries, setTries] = useState(0);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const all = product.name === "*";
  const load = useCallback(
    () =>
      api<AgentsData>(`/api/agents?p=${encodeURIComponent(product.name)}`).then(
        (x) => {
          setFailed(false);
          setData(x);
          setNow(Date.now() / 1000);
          return true;
        },
        () => false,
      ),
    [product.name],
  );
  useEffect(() => {
    let live = true;
    setData(null); // never the old product's agents under the new choice
    setFailed(false);
    const again = () =>
      void load().then((ok) => {
        if (live && !ok) setFailed(true);
      });
    again();
    const timer = setInterval(again, REFRESH_MS);
    const tick = setInterval(() => setNow(Date.now() / 1000), 30_000);
    return () => {
      live = false;
      clearInterval(timer);
      clearInterval(tick);
    };
  }, [load, tries]);
  const tag = (p: string): string | null => (all ? (titles[p] ?? p) : null);
  const running = data?.live.length ?? 0;
  const finished = data?.done.length ?? 0;
  const products = new Set(data?.live.map((a) => a.p)).size;
  const summary = data
    ? [
        running ? (all ? t("agents_sum_all", { n: running, k: products }) : t("agents_sum", { n: running })) : t("agents_none"),
        finished === 1 ? t("done_today_1") : t("done_today_n", { n: finished }),
      ].join(" · ")
    : "";
  return (
    <div className="screen agents">
      <ScreenHead title={t("tab_agents")} product={product}>
        {summary ? <span className="summary">{summary}</span> : null}
      </ScreenHead>
      {failed && !data ? (
        <div className="card quiet">
          {t("load_failed")}{" "}
          <button type="button" className="link" onClick={() => setTries(tries + 1)}>
            {t("retry")}
          </button>
        </div>
      ) : !data ? (
        <div className="card quiet">{t("loading")}</div>
      ) : (
        <>
          <section aria-label={t("agents_running")} className="block">
            <h2 className="sect">
              {t("agents_running")}
              {running ? <span className="count">{running}</span> : null}
            </h2>
            {running ? (
              <div className="cards">
                {data.live.map((a) => (
                  <Card key={`${a.p}/${a.id}`} a={a} now={now} tag={tag(a.p)} one={running < 5} />
                ))}
              </div>
            ) : (
              <div className="nobody">
                <span className="grey" />
                <div>
                  <div className="big">{t("nobody")}</div>
                  {data.next_pass ? (
                    <div className="small">
                      {data.next_pass > now
                        ? t("next_pass", {
                            time: clock(data.next_pass),
                          })
                        : t("next_pass_due")}
                    </div>
                  ) : null}
                </div>
              </div>
            )}
          </section>
          <section aria-label={t("agents_done")} className="block">
            <h2 className="sect">
              {t("agents_done")}
              {finished ? <span className="count">{finished}</span> : null}
            </h2>
            {finished ? (
              <ul className="done">
                {data.done.map((d) => (
                  <li key={`${d.p}/${d.agent}`}>
                    <span className="what">
                      {d.task}
                      <span className="sub"> · {kind(d.sub, d.type)}</span>
                    </span>
                    <span className="facts">
                      <span className={`res ${d.outcome ?? "stopped"}`}>
                        {t(d.outcome ? RESULT[d.outcome] : "res_stopped")}
                      </span>
                      {d.minutes !== null ? <span>{duration(d.minutes)}</span> : null}
                      <Chip family={d.family} />
                      <span className="cost">{usd2(d.usd)}</span>
                      {tag(d.p) ? <span className="tag">{tag(d.p)}</span> : null}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="card quiet">{t("no_done")}</div>
            )}
            <div className="note">{t("cost_note")}</div>
          </section>
        </>
      )}
    </div>
  );
}
