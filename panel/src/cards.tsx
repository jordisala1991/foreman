// The cards that wait for the owner, shared by «Needs you», the tasks and the task page: a question (his own words
// back to the agent that asked), a permission (allow once or deny), a task in the integration queue (accept or send
// back with a note), a triage task («Go» or cancel) and a blocked task (unblock). Each writes with the panel's API
// (the same functions as `fm`) and then asks the screen to read again; what went wrong stays on the card.
import { useState } from "react";
import { Small } from "./icons";
import { blocked, enc, post, t, when, type Agent, type Task, type Key, type Question } from "./lib";

// run a write; `error` is what to say if it fails
export function useWrite(done: () => void) {
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

const Err = ({ text }: { text: string }) => (text ? <p className="error" role="alert">{text}</p> : null);

// how to open an agent's conversation: `claude attach SHORT` (it is also in the Claude app's agent view)
export function Attach({ agent }: { agent: Agent | undefined }) {
  const [copied, setCopied] = useState(false);
  if (!agent?.short) return null;
  const cmd = `claude attach ${agent.short}`;
  const copy = () => {
    void navigator.clipboard?.writeText(cmd).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <span className="attach" title={t("attach_tip")}>
      <Small name="talk" />
      <code>{cmd}</code>
      <button type="button" className="link small" onClick={copy}>
        {copied ? t("copied") : t("copy")}
      </button>
    </span>
  );
}

// the agent of a task and what its session does
export function AgentLine({ i }: { i: Task }) {
  if (!i.agent?.name) return null;
  const st = i.agent_state ?? "gone";
  const tone = st === "working" ? "ok" : st === "done" ? "wait" : "off";
  return (
    <span className="agentline">
      <span className={`dot ${tone}`} />
      <strong>{i.agent.name}</strong>
      <span>{t(`as_${st}` as Key)}</span>
    </span>
  );
}

const TaskRef = ({ id, title }: { id: string; title?: string | undefined }) => (
  <a className="ctx" href={`#/task/${id}`}>
    <span className="mono">{id}</span>
    {title ? ` · ${title}` : ""}
  </a>
);

export function QuestionCard({ q, p, reload }: { q: Question; p: string; reload: () => void }) {
  const w = useWrite(reload);
  const [text, setText] = useState("");
  const send = async () => {
    if (text.trim() && (await w.run(`/api/answer?p=${enc(p)}&id=${enc(q.id)}`, text.trim()))) setText("");
  };
  return (
    <article className="card ask" data-kind="question">
      <div className="askhead">
        <TaskRef id={q.task} title={q.title} />
        <span className="since">{when(q.asked ?? q.date)}</span>
      </div>
      <div>
        <div className="who">{t("asks", { agent: q.agent ?? "?" })}</div>
        <h3 className="pre">{q.text}</h3>
      </div>
      <div className="free">
        <label htmlFor={`a-${q.id}`}>{t("your_answer")}</label>
        <textarea id={`a-${q.id}`} rows={3} value={text} onChange={(e) => setText(e.target.value)} />
        <div className="actions end">
          <button type="button" className="btn primary" disabled={w.busy || !text.trim()} onClick={() => void send()}>
            {t("send")}
          </button>
        </div>
      </div>
      <p className="note">{t("answer_goes_back", { agent: q.agent ?? "?" })}</p>
      <Err text={w.error} />
    </article>
  );
}

export function PermissionCard({ q, p, reload }: { q: Question; p: string; reload: () => void }) {
  const w = useWrite(reload);
  const base = `p=${enc(p)}&id=${enc(q.id)}`;
  return (
    <article className="card ask urgent" data-kind="permission">
      <div className="askhead">
        <span className="kind">{t("kind_permission")}</span>
        <TaskRef id={q.task} />
        <span className="since">{when(q.asked ?? q.date)}</span>
      </div>
      <div>
        <h3>{t("perm_title", { agent: q.agent ?? "?", action: q.action ?? "?" })}</h3>
        <p className="why pre">{q.text}</p>
      </div>
      <div className="actions">
        <button type="button" className="btn" disabled={w.busy} onClick={() => void w.run(`/api/deny?${base}`)}>
          {t("deny")}
        </button>
        <button type="button" className="btn primary" disabled={w.busy} onClick={() => void w.run(`/api/allow?${base}`)}>
          {t("allow_once")}
        </button>
      </div>
      <Err text={w.error} />
    </article>
  );
}

export const Risk = ({ risk }: { risk: string }) => <span className={`risk ${risk}`}>{t(`risk_${risk}` as Key)}</span>;

export function CheckView({ c }: { c: NonNullable<Task["check"]> }) {
  return (
    <div className="check">
      <div className="checkline">
        <Risk risk={c.risk} />
        <span>{t("check_figs", { tests: t(`tests_${c.tests}` as Key), files: c.files, lines: c.lines })}</span>
      </div>
      <ul className="reasons">
        {c.reasons.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
      {c.paths?.length ? (
        <details className="cmd">
          <summary>{t("see_files")}</summary>
          <code>{c.paths.join("\n")}</code>
        </details>
      ) : null}
    </div>
  );
}

export function ReportView({ r }: { r: NonNullable<Task["report"]> }) {
  return (
    <dl className="report">
      {r.summary ? (<><dt>{t("rep_summary")}</dt><dd className="pre">{r.summary}</dd></>) : null}
      {r.review ? (<><dt>{t("rep_review")}</dt><dd className="pre">{r.review}</dd></>) : null}
      {r.confused ? (<><dt>{t("rep_confused")}</dt><dd className="pre">{r.confused}</dd></>) : null}
    </dl>
  );
}

export function QueueCard({ i, p, reload }: { i: Task; p: string; reload: () => void }) {
  const w = useWrite(reload);
  const [note, setNote] = useState<string | null>(null);
  const base = `p=${enc(p)}&id=${enc(i.id)}`;
  const back = async () => {
    if (note?.trim() && (await w.run(`/api/reject?${base}`, note.trim()))) setNote(null);
  };
  return (
    <article className="card ask" data-kind="queue">
      <div className="askhead">
        <TaskRef id={i.id} />
        {i.rework ? <span className="kind">{t("rework_n", { n: i.rework })}</span> : null}
        <span className="since">{when(i.report?.at)}</span>
      </div>
      <h3>{i.title}</h3>
      {i.check ? <CheckView c={i.check} /> : <p className="why">{t("not_checked")}</p>}
      {i.report ? <ReportView r={i.report} /> : null}
      {note === null ? (
        <div className="actions">
          <button type="button" className="btn" disabled={w.busy} onClick={() => setNote("")}>
            {t("send_back")}
          </button>
          <button type="button" className="btn primary" disabled={w.busy || !i.check} onClick={() => void w.run(`/api/accept?${base}`)}>
            {t("accept")}
          </button>
        </div>
      ) : (
        <div className="free">
          <label htmlFor={`n-${i.id}`}>{t("send_back_note", { agent: i.agent?.name ?? "?" })}</label>
          <textarea id={`n-${i.id}`} rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="actions end">
            <button type="button" className="btn ghost" onClick={() => setNote(null)}>{t("cancel")}</button>
            <button type="button" className="btn primary" disabled={w.busy || !note.trim()} onClick={() => void back()}>
              {t("send_back")}
            </button>
          </div>
        </div>
      )}
      <Err text={w.error} />
      <Attach agent={i.agent} />
    </article>
  );
}

export function TriageCard({ i, p, reload }: { i: Task; p: string; reload: () => void }) {
  const w = useWrite(reload);
  const base = `p=${enc(p)}&id=${enc(i.id)}`;
  return (
    <article className="card ask" data-kind="triage">
      <div className="askhead">
        <TaskRef id={i.id} />
        <span className="since">{when(i.created)}</span>
      </div>
      <h3>{i.title}</h3>
      {i.detail ? <p className="why pre clamp3">{i.detail}</p> : null}
      <div className="actions">
        <button type="button" className="btn" disabled={w.busy} onClick={() => void w.run(`/api/cancel?${base}`, t("not_wanted"))}>
          {t("cancel_task")}
        </button>
        <button type="button" className="btn primary" disabled={w.busy || !i.acceptance?.criteria} onClick={() => void w.run(`/api/go?${base}`)}>
          {t("go")}
        </button>
      </div>
      <p className="note">{t(i.acceptance?.criteria ? "go_ready" : "go_define")}</p>
      <Err text={w.error} />
    </article>
  );
}

export function BlockedCard({ i, p, reload }: { i: Task; p: string; reload: () => void }) {
  const w = useWrite(reload);
  const running = i.status === "refining" || i.status === "in progress";
  const [relaunch, setRelaunch] = useState(running && i.agent_state !== "working");
  if (!blocked(i)) return null;
  return (
    <article className="card ask" data-kind="blocked">
      <div className="askhead">
        <TaskRef id={i.id} />
        <span className="kind">{t(`st_${i.status.replace(" ", "_")}` as Key)}</span>
      </div>
      <h3>{i.title}</h3>
      <p className="why pre">{t("blocked_why", { why: i.blocked ?? "" })}</p>
      {running ? (
        <label className="check-label">
          <input type="checkbox" checked={relaunch} onChange={(e) => setRelaunch(e.target.checked)} />
          {t("relaunch")}
        </label>
      ) : null}
      <div className="actions">
        <button type="button" className="btn primary" disabled={w.busy}
          onClick={() => void w.run(`/api/unblock?p=${enc(p)}&id=${enc(i.id)}${relaunch && running ? "&relaunch=1" : ""}`)}>
          {t("unblock")}
        </button>
      </div>
      <Err text={w.error} />
      <Attach agent={i.agent} />
    </article>
  );
}
