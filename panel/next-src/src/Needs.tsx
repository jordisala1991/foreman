// «Needs you»: what waits for the owner, the most urgent first. Permissions (approve or deny, with their time left),
// questions (an option, his own words, or talking to whoever asks) and the orchestrator's messages, with a box to
// write to it. On a wide screen the messages go in a second column.
import { useRef, useState } from "react";
import { LoadFailed, PermissionCard, QuestionCard, askedSince, titleParts } from "./cards";
import { useScope, type Scope } from "./data";
import { ScreenHead, Skel } from "./head";
import { Small } from "./icons";
import { dayWord, post, t, type Decision, type Grant, type Message, type Product } from "./lib";

export interface Waiting {
  grants: { g: Grant; product: Product }[];
  questions: { d: Decision; product: Product }[];
  messages: { m: Message; product: Product }[]; // the unread ones of the orchestrator
}

// what waits in the products' states: the grants that can be answered (orphans too: they are only dropped), the
// questions (answered ones stay until the orchestrator reads them), the orchestrator's unread messages
export function waiting(scope: Product[], data: Record<string, Scope>): Waiting {
  const w: Waiting = { grants: [], questions: [], messages: [] };
  for (const product of scope) {
    const s = data[product.name]?.state;
    if (!s) continue;
    for (const g of s.grants ?? []) if (g.pending || g.orphan) w.grants.push({ g, product });
    for (const d of s.decisions ?? []) w.questions.push({ d, product });
    for (const m of s.messages ?? []) if (m.from === "orchestrator" && !m.read) w.messages.push({ m, product });
  }
  w.questions.sort((a, b) => Number(a.d.answer !== null) - Number(b.d.answer !== null)); // the open ones first
  w.grants.sort((a, b) => (a.g.expires ?? "~").localeCompare(b.g.expires ?? "~"));
  return w;
}

// the first words of a question's card: the issue it comes from (its short name and its id)
export function contextOf(d: Decision, shorts: Record<string, string>): string {
  const issue = /^[A-Z]{2,}-\d+/.exec(d.blocks?.[0] ?? "")?.[0];
  return issue ? `${shorts[issue] ?? issue} · ${issue}` : titleParts(d.title).code || d.id;
}

const plural = (n: number, one: Parameters<typeof t>[0], many: Parameters<typeof t>[0]) => t(n === 1 ? one : many, { n });

// what still waits for his answer (the badge counts the same: an answered question or an orphan is only listed)
export const openGrants = (w: Waiting): number => w.grants.filter((x) => x.g.pending).length;
export const openQuestions = (w: Waiting): number => w.questions.filter((x) => x.d.answer === null).length;

export function summary(w: Waiting): string {
  const g = openGrants(w);
  const q = openQuestions(w);
  const parts = [
    g ? plural(g, "n_permission", "n_permissions") : "",
    q ? plural(q, "n_question", "n_questions") : "",
    w.messages.length ? plural(w.messages.length, "n_message", "n_messages") : "",
  ].filter(Boolean);
  return parts.length > 1 ? `${parts.slice(0, -1).join(", ")} ${t("and")} ${parts[parts.length - 1]}` : (parts[0] ?? "");
}

export function Needs({ scope, all }: { scope: Product[]; all: boolean }) {
  const { data, failed, reload } = useScope(scope.map((p) => p.name), false);
  const w = waiting(scope, data);
  const box = useRef<HTMLTextAreaElement>(null);
  const [target, setTarget] = useState(scope[0]?.name ?? "");
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const to = scope.some((p) => p.name === target) ? target : (scope[0]?.name ?? "");
  const talk = (name: string) => () => {
    setTarget(name);
    box.current?.focus();
    box.current?.scrollIntoView?.({ block: "center" });
  };
  const send = async () => {
    if (!text.trim()) return;
    try {
      await post(`/api/message?p=${encodeURIComponent(to)}`, text.trim());
      setText("");
      setError("");
      reload();
    } catch (e) {
      setError(t("write_failed", { text: e instanceof Error ? e.message : String(e) }));
    }
  };
  const read = async (name: string, id: number) => {
    try {
      await post(`/api/message/read?p=${encodeURIComponent(name)}&id=${id}`);
      reload();
    } catch {
      setError(t("load_failed"));
    }
  };
  const tag = (p: Product) => (all ? p.title : null);
  const loaded = scope.every((p) => data[p.name]?.state);
  const nothing = loaded && !w.grants.length && !w.questions.length;
  const head = scope[0];
  if (!head) return null;
  return (
    <div className="screen needs">
      <ScreenHead title={t("tab_needs")} product={head} all={all ? scope : undefined} sub={summary(w)} />
      <div className="needs-grid">
        <div className="col">
          {!loaded && !failed ? <Skel h={160} /> : null}
          {failed ? <LoadFailed retry={reload} /> : null}
          {nothing ? (
            <div className="card empty big">
              <span className="round">
                <Small name="check" size={24} />
              </span>
              <div className="title">{t("nothing_waits")}</div>
              <div className="sub">{t("nothing_waits_hint")}</div>
            </div>
          ) : null}
          {w.grants.length ? (
            <section aria-label={t("permissions")}>
              <h2>
                {t("permissions")}
                <span className="count">{w.grants.length}</span>
              </h2>
              {w.grants.map(({ g, product }) => (
                <PermissionCard key={`${product.name}/${g.group}`} g={g} product={product.name} tag={tag(product)} variant="needs"
                  reload={reload} talk={talk(product.name)} />
              ))}
            </section>
          ) : null}
          {w.questions.length ? (
            <section aria-label={t("questions")}>
              <h2>
                {t("questions")}
                <span className="count">{w.questions.length}</span>
              </h2>
              {w.questions.map(({ d, product }) => (
                <QuestionCard key={`${product.name}/${d.id}`} d={d} product={product.name} tag={tag(product)} mode="full" reload={reload}
                  context={contextOf(d, data[product.name]?.shorts ?? {})} since={askedSince(d)} talk={talk(product.name)} />
              ))}
            </section>
          ) : null}
        </div>
        <section className="col" aria-label={t("orch_messages")}>
          <h2>
            {t("orch_messages")}
            {w.messages.length ? <span className="count">{t("n_unread", { n: w.messages.length })}</span> : null}
          </h2>
          {w.messages.map(({ m, product }) => (
            <article key={`${product.name}/${m.id}`} className="card msg">
              <div className="from">
                <span className="av" aria-hidden="true">OR</span>
                <div className="who">{t("orch_of", { name: product.title })}</div>
                <span className="unread" role="img" aria-label={t("unread")} />
                <span className="since">{dayWord(m.time)}</span>
              </div>
              <p>{m.text}</p>
              <button type="button" className="btn small" onClick={() => void read(product.name, m.id)}>
                {t("mark_read")}
              </button>
            </article>
          ))}
          {loaded && !w.messages.length ? <div className="quietline">{t("no_unread")}</div> : null}
          <div className="card compose">
            <label className="sr" htmlFor="to-orchestrator">{t("write_orch")}</label>
            {all ? (
              <select aria-label={t("product")} value={to} onChange={(e) => setTarget(e.target.value)}>
                {scope.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.title}
                  </option>
                ))}
              </select>
            ) : null}
            <textarea id="to-orchestrator" ref={box} rows={3} placeholder={t("write_orch")} value={text} onChange={(e) => setText(e.target.value)} />
            {error ? <p className="error" role="alert">{error}</p> : null}
            <div className="actions end">
              <button type="button" className="btn primary" disabled={!text.trim()} onClick={() => void send()}>
                {t("send")}
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

