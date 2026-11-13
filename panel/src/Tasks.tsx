// The tasks: the backlog grouped by where each task is (what waits for the owner first), each card with its agent and
// how to open its conversation, and a form to add a task (with a done criterion it starts ready, else to define).
import { useState } from "react";
import { AgentLine, Attach, LoadFailed, Risk, useWrite } from "./cards";
import { useFeed } from "./feed";
import { ScreenHead } from "./head";
import { blocked, enc, stateUrl, t, when, type Issue, type Key, type Product, type State } from "./lib";

const GROUPS = ["waiting", "in progress", "refining", "in review", "ready", "backlog", "triage", "closed"] as const;
type Group = (typeof GROUPS)[number];

function groupOf(i: Issue, asking: Set<string>): Group {
  if (["done", "canceled", "duplicate"].includes(i.status)) return "closed";
  if ((blocked(i) || asking.has(i.id)) && i.status !== "in review" && i.status !== "triage") return "waiting";
  return i.status as Group;
}

function TaskCard({ i, asking }: { i: Issue; asking: boolean }) {
  const closed = ["done", "canceled", "duplicate"].includes(i.status);
  return (
    <article className="card task">
      <div className="line1">
        <a href={`#/issue/${i.id}`}>
          <span className="mono id">{i.id}</span> <span className="tt">{i.title}</span>
        </a>
        {i.check && i.status === "in review" ? <Risk risk={i.check.risk} /> : null}
      </div>
      {closed ? (
        <div className="line2">{t(`st_${i.status}` as Key)} · {when(i.closed)}</div>
      ) : (
        <>
          {i.status !== "triage" && i.status !== "backlog" && i.status !== "ready" ? <AgentLine i={i} /> : null}
          {asking ? <div className="line2 warn">{t("has_question")}</div> : null}
          {blocked(i) ? <div className="line2 warn clamp2">{t("blocked_why", { why: i.blocked ?? "" })}</div> : null}
          {i.status === "ready" && i.acceptance?.criteria ? <div className="line2 clamp2">{i.acceptance.criteria}</div> : null}
          {i.status === "refining" || i.status === "in progress" || blocked(i) ? <Attach agent={i.agent} /> : null}
        </>
      )}
    </article>
  );
}

function AddTask({ p, done }: { p: string; done: () => void }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ title: "", detail: "", claim: "", criteria: "" });
  const w = useWrite(done);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const add = async () => {
    if (await w.run(`/api/add?p=${enc(p)}`, JSON.stringify(f))) {
      setF({ title: "", detail: "", claim: "", criteria: "" });
      setOpen(false);
    }
  };
  if (!open)
    return (
      <button type="button" className="btn primary small-btn" onClick={() => setOpen(true)}>
        {t("add_task")}
      </button>
    );
  return (
    <form className="card addform" onSubmit={(e) => { e.preventDefault(); void add(); }}>
      <label>{t("f_title")}<input value={f.title} onChange={set("title")} required autoFocus /></label>
      <label>{t("f_detail")}<textarea rows={4} value={f.detail} onChange={set("detail")} /></label>
      <label>{t("f_claim")}<input value={f.claim} onChange={set("claim")} /></label>
      <label>{t("f_criteria")}<textarea rows={2} value={f.criteria} onChange={set("criteria")} /></label>
      <p className="note">{t(f.criteria.trim() ? "f_goes_ready" : "f_goes_define")}</p>
      {w.error ? <p className="error" role="alert">{w.error}</p> : null}
      <div className="actions end">
        <button type="button" className="btn ghost" onClick={() => setOpen(false)}>{t("cancel")}</button>
        <button type="submit" className="btn primary" disabled={w.busy || !f.title.trim()}>{t("add")}</button>
      </div>
    </form>
  );
}

export function Tasks({ product }: { product: Product }) {
  const s = useFeed<State>(stateUrl(product.name), 10_000);
  const d = s.data;
  const asking = new Set((d?.questions ?? []).map((q) => q.issue));
  const by = new Map<Group, Issue[]>();
  for (const i of d?.issues ?? []) {
    const g = groupOf(i, asking);
    by.set(g, [...(by.get(g) ?? []), i]);
  }
  const open = (d?.issues ?? []).filter((i) => !["done", "canceled", "duplicate"].includes(i.status)).length;
  return (
    <div className="screen tasks">
      <ScreenHead title={t("tab_tasks")} product={product} sub={d ? t("n_open", { n: open }) : ""}>
        <AddTask p={product.name} done={s.reload} />
      </ScreenHead>
      {!d && !s.failed ? <div className="card quiet">{t("loading")}</div> : null}
      {s.failed && !d ? <LoadFailed retry={s.reload} /> : null}
      {d && !d.issues.length ? <div className="card quiet">{t("no_tasks")}</div> : null}
      <div className="groups">
        {GROUPS.filter((g) => by.get(g)?.length).map((g) => (
          <section key={g} className={`group g-${g.replace(" ", "-")}`} aria-label={t(`g_${g.replace(" ", "_")}` as Key)}>
            <h2>
              {t(`g_${g.replace(" ", "_")}` as Key)}
              <span className="count">{by.get(g)?.length}</span>
            </h2>
            <p className="ghint">{t(`gh_${g.replace(" ", "_")}` as Key)}</p>
            {(by.get(g) ?? []).map((i) => (
              <TaskCard key={i.id} i={i} asking={asking.has(i.id)} />
            ))}
          </section>
        ))}
      </div>
      {d?.events.length ? (
        <section className="sec">
          <h2 className="plain">{t("latest")}</h2>
          <ul className="latest">
            {d.events.slice(0, 8).map((e, n) => (
              <li key={n}>
                <span className="time">{when(e.time)}</span>
                <span className="text">{e.issue ? <a href={`#/issue/${e.issue}`}><strong>{e.issue}</strong></a> : null} {e.text}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
