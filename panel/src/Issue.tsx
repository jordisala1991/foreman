// The page of a task: what waits on it (its questions, the queue, triage, a block), what it is for (claim and done
// criterion), its detail, its agent's report and the queue's check, what was asked, its events, its data and cost.
import type { ReactNode } from "react";
import {
  AgentLine, Attach, BlockedCard, CheckView, LoadFailed, PermissionCard, QueueCard, QuestionCard, ReportView, TriageCard,
  useWrite,
} from "./cards";
import { useFeed } from "./feed";
import { enc, stateUrl, t, usd2, when, type IssueAnswer, type Key, type Product } from "./lib";
import { reloadAll } from "./feed";

const Section = ({ title, children }: { title: string; children: ReactNode }) => (
  <section className="sec">
    <div className="sechead">
      <h2>{title}</h2>
    </div>
    {children}
  </section>
);

function Cancel({ p, id, done }: { p: string; id: string; done: () => void }) {
  const w = useWrite(done);
  return (
    <>
      <button type="button" className="btn small" disabled={w.busy}
        onClick={() => { if (confirm(t("cancel_sure"))) void w.run(`/api/cancel?p=${enc(p)}&id=${enc(id)}`, t("canceled_panel")); }}>
        {t("cancel_task")}
      </button>
      {w.error ? <p className="error" role="alert">{w.error}</p> : null}
    </>
  );
}

const IMAGE = /!\[([^\]]*)\]\((\/evidence\?[^()\s]+)\)/g;

/** The description as text, with the report's images (served by /evidence) drawn where they are named. */
function Described({ text }: { text: string }) {
  const out: ReactNode[] = [];
  let at = 0;
  for (const m of text.matchAll(IMAGE)) {
    out.push(text.slice(at, m.index));
    out.push(<img key={m.index} src={m[2]} alt={m[1]} style={{ maxWidth: "100%", display: "block" }} />);
    at = m.index + m[0].length;
  }
  out.push(text.slice(at));
  return <>{out}</>;
}

export function Issue({ product, id }: { product: Product; id: string }) {
  const path = `/api/issue?p=${enc(product.name)}&id=${enc(id)}`;
  const a = useFeed<IssueAnswer>(path, 10_000);
  const reload = () => reloadAll([path, stateUrl(product.name)]);
  const crumbs = (
    <div className="crumbs">
      <a href="#/tasks">‹ {t("tab_tasks")}</a>
      <span className="sep"> / </span>
      <span className="here">{id}</span>
    </div>
  );
  if (!a.data)
    return (
      <div className="screen">
        {crumbs}
        {a.failed ? <LoadFailed retry={a.reload} /> : <div className="card quiet">{t("loading")}</div>}
      </div>
    );
  const { issue: i, events, questions, cost } = a.data;
  const p = product.name;
  const open = questions.filter((q) => q.answer === undefined && q.kind !== "rejection");
  const past = questions.filter((q) => q.answer !== undefined);
  const closed = ["done", "canceled", "duplicate"].includes(i.status);
  const rows: [string, ReactNode][] = [
    [t("isu_status"), t(`st_${i.status.replace(" ", "_")}` as Key)],
    ...(i.agent?.name ? [[t("isu_agent"), <AgentLine key="a" i={i} />] as [string, ReactNode]] : []),
    ...(i.agent?.model ? [[t("isu_model"), i.agent.model] as [string, ReactNode]] : []),
    ...(i.branch ? [[t("isu_branch"), <code key="b">{i.branch}</code>] as [string, ReactNode]] : []),
    [t("isu_created"), when(i.created)],
    ...(i.closed ? [[t("isu_closed"), when(i.closed)] as [string, ReactNode]] : []),
    [t("isu_cost"), t("isu_cost_v", { usd: usd2(cost.usd), min: Math.round(cost.minutes) })],
    ...(i.rework ? [[t("isu_rework"), String(i.rework)] as [string, ReactNode]] : []),
    ...(i.nudges ? [[t("isu_nudges"), String(i.nudges)] as [string, ReactNode]] : []),
    ...(i.commit ? [[t("isu_commit"), <code key="c">{i.commit.slice(0, 9)}</code>] as [string, ReactNode]] : []),
    ...(i.source ? [[t("isu_source"), i.source] as [string, ReactNode]] : []),
  ];
  return (
    <div className="screen">
      {crumbs}
      <div className="twocol">
        <div className="main">
          <header className="isu-head">
            <div className="badges">
              <span className="id mono">{i.id}</span>
              <span className="lanebadge">{t(`st_${i.status.replace(" ", "_")}` as Key)}</span>
            </div>
            <h1>{i.title}</h1>
          </header>
          {open.map((q) =>
            q.kind === "permission" ? <PermissionCard key={q.id} q={q} p={p} reload={reload} /> : <QuestionCard key={q.id} q={q} p={p} reload={reload} />,
          )}
          {i.status === "triage" ? <TriageCard i={i} p={p} reload={reload} /> : null}
          {i.status === "in review" ? <QueueCard i={i} p={p} reload={reload} /> : null}
          {i.status !== "triage" && i.status !== "in review" ? <BlockedCard i={i} p={p} reload={reload} /> : null}
          <Section title={t("isu_purpose")}>
            {i.acceptance?.claim || i.acceptance?.criteria ? (
              <div className="card purpose">
                {i.acceptance.claim ? (<><div className="lbl">{t("isu_goal")}</div><p>{i.acceptance.claim}</p></>) : null}
                {i.acceptance.criteria ? (<><div className="lbl">{t("isu_criteria")}</div><p>{i.acceptance.criteria}</p></>) : null}
                {i.acceptance.verdict ? (<><div className="lbl">{t("isu_verdict")}</div><p>{i.acceptance.verdict}</p></>) : null}
              </div>
            ) : (
              <div className="card quiet">{t("isu_no_goal")}</div>
            )}
          </Section>
          {i.detail ? (
            <Section title={t("isu_detail")}>
              <div className="card pre">{i.detail}</div>
            </Section>
          ) : null}
          {i.description ? (
            <Section title={t("isu_description")}>
              <div className="card pre"><Described text={i.description} /></div>
            </Section>
          ) : null}
          {i.report && i.status !== "in review" ? (
            <Section title={t("isu_report")}>
              <div className="card"><ReportView r={i.report} /></div>
            </Section>
          ) : null}
          {i.check && i.status !== "in review" ? (
            <Section title={t("isu_check")}>
              <div className="card"><CheckView c={i.check} /></div>
            </Section>
          ) : null}
          {past.length ? (
            <Section title={t("isu_asked")}>
              <div className="card journal">
                {past.map((q) => (
                  <div key={q.id} className="entry">
                    <div className="who">{[q.id, q.agent, t(`kind_${q.kind}` as Key)].filter(Boolean).join(" · ")}</div>
                    <p className="pre">{q.text}</p>
                    <p className="answer pre">→ {q.answer}</p>
                  </div>
                ))}
              </div>
            </Section>
          ) : null}
          {events.length ? (
            <Section title={t("isu_events")}>
              <div className="card list plain">
                {events.map((e, n) => (
                  <div key={n} className="moveline"><span className="when">{when(e.time)}</span><span className="pre">{e.text}</span></div>
                ))}
              </div>
            </Section>
          ) : null}
        </div>
        <aside className="datos">
          <h2>{t("isu_data")}</h2>
          <dl>
            {rows.map(([k, v]) => (<span key={k} className="kv"><dt>{k}</dt><dd>{v}</dd></span>))}
          </dl>
          <Attach agent={i.agent} />
          {i.agent?.short ? <p className="quietline">{t("attach_tip")}</p> : null}
          {!closed ? <Cancel p={p} id={i.id} done={reload} /> : null}
        </aside>
      </div>
    </div>
  );
}
