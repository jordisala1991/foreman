// The two kinds of card that wait for the owner, shared by Home and «Needs you»: a permission (approve or deny) and a
// question (an option, or his own words). A card writes with the same endpoints as today's panel and then asks the
// screen to read again; what went wrong stays on the card.
import { useState, type ReactNode } from "react";
import { Small } from "./icons";
import { dayWord, hhmm, localDate, post, t, type Decision, type Grant } from "./lib";

export const ProdTag = ({ name }: { name: string | null }) => (name ? <span className="prodtag">{name}</span> : null);

const enc = encodeURIComponent;

// run a write; `error` is what to say if it fails
function useWrite(done: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (path: string, body?: string): Promise<boolean> => {
    setBusy(true);
    setError("");
    try {
      await post(path, body);
      done();
      return true;
    } catch (e) {
      setError(t("write_failed", { text: e instanceof Error ? e.message : String(e) }));
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

// the screen could not read its data: say so, with a way to try again
export const LoadFailed = ({ retry }: { retry: () => void }) => (
  <div className="card quiet" role="alert">
    {t("load_failed")}{" "}
    <button type="button" className="link" onClick={retry}>
      {t("retry")}
    </button>
  </div>
);

const Talk = ({ talk }: { talk?: (() => void) | undefined }) =>
  talk ? (
    <button type="button" className="talk" onClick={talk}>
      <Small name="talk" />
      {t("talk")}
    </button>
  ) : (
    <a className="talk" href="#/needs">
      <Small name="talk" />
      {t("talk")}
    </a>
  );

// ---------- permissions ----------

const minutesLeft = (g: Grant): number | null => (g.expires ? Math.round((localDate(g.expires).getTime() - Date.now()) / 60000) : null);

function grantTitle(g: Grant): string {
  const who = g.orchestrator || t("orchestrator");
  const s = g.say;
  if (g.summary) return g.summary;
  if (s?.kind === "push") return t("grant_say_push", { who, what: (s.refs ?? []).join(", "), to: s.to ?? "" });
  if (s?.kind === "push_all") return t("grant_say_push_all", { who, to: s.to ?? "" });
  if (s?.kind === "release") return t("grant_say_release", { who, what: s.what ?? "" });
  if (s?.kind === "deploy") return t("grant_say_deploy", { who });
  return t("grant_generic", { who, group: g.group });
}

export function PermissionCard({ g, product, tag, variant, reload, talk }: {
  g: Grant; product: string; tag: string | null; variant: "short" | "home" | "needs"; reload: () => void; talk?: (() => void) | undefined;
}) {
  const w = useWrite(reload);
  const [confirming, setConfirming] = useState(false); // approving asks first, as today's panel does
  const left = minutesLeft(g);
  const soon = left !== null && left < 10;
  const q = `g=${enc(g.group)}&p=${enc(product)}`;
  const not_told = /^(no:|error)/.test(g.notified ?? "");
  const who = g.orchestrator || t("orchestrator");
  return (
    <article className={soon ? "card ask urgent" : "card ask"} data-kind="permission">
      <div className="askhead">
        {variant !== "needs" ? <span className="kind">{t("kind_permission")}</span> : null}
        {left !== null && !g.orphan ? (
          <span className={soon ? "expires red" : "expires"}>
            <Small name="clock" />
            {t("expires_in", { n: Math.max(0, left) })}
            {variant === "needs" && g.expires ? ` · ${t("at_time", { time: hhmm(g.expires) })}` : ""}
          </span>
        ) : null}
        <ProdTag name={tag} />
      </div>
      <div>
        <h3>{grantTitle(g)}</h3>
        <p className="why">{g.orphan ? t("grant_orphan") : t(variant === "short" ? "grant_why_short" : "grant_why", { who })}</p>
      </div>
      {variant === "needs" && not_told ? (
        <div className="notice">
          <Small name="bell_off" />
          {t("grant_not_told")}
        </div>
      ) : null}
      <div className="actions">
        {g.orphan ? (
          <button type="button" className="btn" disabled={w.busy} onClick={() => void w.run(`/api/grants/deny?${q}`)}>
            {t("grant_discard")}
          </button>
        ) : confirming ? (
          <>
            <p className="why">{t("confirm_grant", { group: g.group, n: g.minutes ?? 30 })}</p>
            <button type="button" className="btn" disabled={w.busy} onClick={() => setConfirming(false)}>
              {t("cancel")}
            </button>
            <button type="button" className="btn primary" disabled={w.busy} onClick={() => void w.run(`/api/grants/approve?${q}`)}>
              {t("confirm")}
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn" disabled={w.busy} onClick={() => void w.run(`/api/grants/deny?${q}`)}>
              {t("deny")}
            </button>
            <button type="button" className="btn primary" disabled={w.busy} onClick={() => setConfirming(true)}>
              {t("approve")}
            </button>
          </>
        )}
      </div>
      {w.error ? <p className="error" role="alert">{w.error}</p> : null}
      <details className="cmd">
        <summary>{t("grant_see_cmd")}</summary>
        <code>{g.part || g.command}</code>
      </details>
      {variant !== "short" ? <Talk talk={talk} /> : null}
    </article>
  );
}

// ---------- questions ----------

// a decision's title may start with its code («FO J1, ¿…?»): the text, and the code apart
export function titleParts(title: string): { code: string; text: string } {
  const m = /^((?:[A-Z]{1,6}-\d+|[A-Z]{2,6})\s+J\d+|J\d+)\s*[,:]\s*(\S.*)$/s.exec(title);
  return m ? { code: m[1] ?? "", text: m[2] ?? title } : { code: "", text: title };
}

const firstSentence = (s: string): string => {
  const m = /^[^.?!]*[.?!]/.exec(s.trim());
  return (m ? m[0] : s).trim();
};

export function QuestionCard({ d, product, tag, context, since, mode, reload, talk }: {
  d: Decision; product: string; tag: string | null; context: ReactNode; since: string; mode: "short" | "full";
  reload: () => void; talk?: (() => void) | undefined;
}) {
  const w = useWrite(reload);
  const [free, setFree] = useState<string | null>(null); // the text of «another answer» while it is open
  const [changing, setChanging] = useState(false);
  const [more, setMore] = useState(false);
  const answered = d.answer !== null && !changing;
  const base = `/api/decide?p=${enc(product)}&id=${enc(d.id)}${d.answer !== null ? "&change=1" : ""}`;
  const answer = async (op: string) => {
    if (await w.run(`${base}&op=${enc(op)}`)) setChanging(false);
  };
  const sendFree = async () => {
    if (free && free.trim() && (await w.run(`${base.replace("/api/decide?", "/api/decide?free=1&")}`, free.trim()))) {
      setFree(null);
      setChanging(false);
    }
  };
  const full = mode === "full";
  return (
    <article className="card ask" data-kind="question">
      <div className="askhead">
        <span className={full ? "ctx" : "kind"}>{context}</span>
        <ProdTag name={tag} />
        <span className="since">{since}</span>
      </div>
      <div>
        <h3>{titleParts(d.title).text}</h3>
        {full && d.text ? <p className="why">{more ? d.text : firstSentence(d.text)}</p> : null}
        {full && d.text && d.text.trim() !== firstSentence(d.text) ? (
          <button type="button" className="link small" onClick={() => setMore(!more)}>
            {more ? t("read_less") : t("read_more")}
          </button>
        ) : null}
      </div>
      {answered ? (
        <div className="answered">
          <Small name="check" />
          <span>{t(d.free ? "answered_free" : "answered", { answer: d.answer ?? "" })}</span>
          <button type="button" className="link small" onClick={() => setChanging(true)}>
            {t("change_answer")}
          </button>
        </div>
      ) : (
        <>
          <div className={full ? "options full" : "options short"}>
            {d.options.map((o) => {
              const rec = d.recommended === o;
              const effect = full ? d.details?.[o]?.effect : undefined;
              return (
                <button key={o} type="button" className={rec ? "option rec" : "option"} disabled={w.busy} onClick={() => void answer(o)}>
                  <span className="label">
                    {o}
                    {rec && full ? <span className="recommended">{`(${t("recommended")})`}</span> : null}
                  </span>
                  {effect ? <span className="effect">{effect}</span> : null}
                </button>
              );
            })}
          </div>
          {!full && d.options.some((o) => o === d.recommended) ? <p className="note">{t("marked_recommended")}</p> : null}
          {d.sent ? <p className="note">{t("sent_not_saved")}</p> : null}
          {full && free !== null ? (
            <div className="free">
              <label htmlFor={`free-${product}-${d.id}`}>{t("other_answer")}</label>
              <textarea id={`free-${product}-${d.id}`} rows={3} value={free} onChange={(e) => setFree(e.target.value)} />
              <div className="actions end">
                <button type="button" className="btn ghost" onClick={() => setFree(null)}>
                  {t("cancel")}
                </button>
                <button type="button" className="btn primary" disabled={w.busy || !free.trim()} onClick={() => void sendFree()}>
                  {t("send")}
                </button>
              </div>
            </div>
          ) : null}
        </>
      )}
      {w.error ? <p className="error" role="alert">{w.error}</p> : null}
      {full ? (
        <div className="foot">
          {!answered && free === null ? (
            <button type="button" className="link muted" onClick={() => setFree("")}>
              {t("other_answer_open")}
            </button>
          ) : (
            <span />
          )}
          <Talk talk={talk} />
        </div>
      ) : (
        <a className="more" href="#/needs">
          {t("what_changes")}
          <Small name="go" />
        </a>
      )}
    </article>
  );
}

export const askedSince = (d: Decision): string => (d.date ? t("waiting_since", { day: dayWord(d.date) }) : "");
