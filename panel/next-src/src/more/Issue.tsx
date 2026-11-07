// The page of a task: what it is for, how its parts go, its documents grouped by type, what has been said and what
// the owner did on it, and its data. A task in triage asks first «¿La quieres hacer?».
import { useState } from "react";
import { t, type Key } from "../lib";
import { go, post, q, useApi } from "./api";
import { isBlocked, nameOf, StatusIcon, SubRows, subLive, subState, whyOf } from "./Backlog";
import { Crumbs, Loading, Section, Split } from "./parts";
import type { BacklogAnswer, Card, DocRef, IssueAnswer, MoreProduct, ProjectState } from "./types";

interface Entry {
  date?: string;
  who?: string;
  text: string;
}
// the detail as a journal: «2026-11-07 Ana (chat): …» entries, and what comes before the first date as one entry
export function journal(detail: string): Entry[] {
  const out: Entry[] = [];
  for (const part of detail.split(/(?<=^|[\s.»)])(?=20\d\d-\d\d-\d\d\s)/)) {
    const text = part.trim();
    if (!text) continue;
    const m = text.match(/^(\d{4}-\d\d-\d\d)\s+([^:()]{1,40}?)(?:\s*\(([^)]*)\))?:\s+([\s\S]*)$/);
    if (m) out.push({ date: m[1] ?? "", who: [m[2], m[3] ? `(${m[3]})` : ""].filter(Boolean).join(" "), text: (m[4] ?? "").trim() });
    else out.push({ text });
  }
  return out;
}

const DOC_KINDS = ["plan", "report", "review", "decision", "cited"] as const;

function DocButton({ d }: { d: DocRef }) {
  const label =
    d.kind === "report"
      ? (d.sub ?? t("doc_open"))
      : d.kind === "review"
        ? t(d.open ? "doc_review_open" : "doc_review_clean", { sub: d.sub ?? "", round: d.round ?? 1, open: d.open ?? 0 })
        : d.kind === "plan"
          ? t("doc_read_here")
          : (d.title ?? d.id ?? t("doc_open"));
  return (
    <a className={d.kind === "plan" ? "btn primary" : d.kind === "report" ? "btn chip" : "btn"} href={go("backlog", "doc", d.doc)} title={d.title}>
      {label}
    </a>
  );
}

function Docs({ docs }: { docs: DocRef[] }) {
  if (!docs.length) return null;
  return (
    <Section title={t("isu_docs")}>
      <div className="card docs">
        {DOC_KINDS.map((k) => {
          const mine = docs.filter((d) => d.kind === k);
          if (!mine.length) return null;
          return (
            <div key={k} className="docgroup">
              <div className="dlabel">{t(`doc_${k}` as Key)}</div>
              <div className="dbtns">
                {mine.map((d) => (
                  <span key={d.doc} className="dwrap">
                    {k === "plan" && d.title ? <span className="dtitle">{d.title}</span> : null}
                    <DocButton d={d} />
                  </span>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </Section>
  );
}

function Want({ c, product, done }: { c: Card; product: MoreProduct; done: () => void }) {
  const [sure, setSure] = useState(false);
  const [msg, setMsg] = useState("");
  const call = (path: string) =>
    void post(`${path}?p=${q(product.name)}&id=${q(c.id)}${path.endsWith("want") ? "&on=" + (c.wanted ? "0" : "1") : ""}`).then((r) => {
      setMsg(r.ok ? t("want_done") : t("want_failed", { text: r.error }));
      setSure(false);
      if (r.ok) done();
    });
  return (
    <div className="card want">
      <h2>{t("want_title")}</h2>
      {c.wanted ? <p>{t("want_wanted")}</p> : null}
      {sure ? (
        <>
          <p>{t("want_sure")}</p>
          <div className="btnrow">
            <button type="button" className="btn danger" onClick={() => call("/api/reject")}>{t("want_confirm")}</button>
            <button type="button" className="btn" onClick={() => setSure(false)}>{t("want_cancel")}</button>
          </div>
        </>
      ) : (
        <div className="btnrow">
          <button type="button" className="btn primary" onClick={() => call("/api/want")}>{c.wanted ? t("want_undo") : t("want_yes")}</button>
          {!c.wanted ? <button type="button" className="btn" onClick={() => setSure(true)}>{t("want_no")}</button> : null}
        </div>
      )}
      {msg ? <p className="msg" role="status">{msg}</p> : null}
    </div>
  );
}

export function Issue(props: {
  product: MoreProduct;
  id: string;
  backlog: BacklogAnswer["project"]["backlog"] | undefined;
  state: ProjectState | undefined;
  reload: () => void;
}) {
  const { product, id, backlog, state } = props;
  const a = useApi<IssueAnswer>(`/api/issue?p=${q(product.name)}&id=${q(id)}`, 60000);
  const [all, setAll] = useState(false);
  if (a.failed && !a.data)
    return (
      <div className="screen">
        <Crumbs steps={[{ label: t("bl_title"), href: go("backlog") }, { label: id }]} />
        <div className="card quiet">{t("isu_missing")}</div>
      </div>
    );
  if (!a.data)
    return (
      <div className="screen">
        <Crumbs steps={[{ label: t("bl_title"), href: go("backlog") }, { label: id }]} />
        <Loading />
      </div>
    );
  const c = a.data.issue;
  const live = state?.issue_live[c.id];
  const status = live?.status ?? c.status;
  const project = backlog?.projects.find((p) => p.id === c.project);
  const open = (project?.issues ?? []).filter((i) => !["done", "canceled", "duplicate", "triage"].includes(i.status));
  const nth = open.findIndex((i) => i.id === c.id) + 1;
  const parts = subLive(c);
  const done = parts.filter((s) => subState(s) === "done").length;
  const entries = journal(c.detail ?? "");
  const shown = all ? entries : entries.slice(-3).reverse();
  const mine = (state?.activity ?? []).filter((m) => m.move === c.id).slice(0, 6);
  const blocked = isBlocked(c) && !["done", "canceled", "duplicate"].includes(status);
  const cost = a.data.cost;
  const rows: [string, string][] = [];
  if (c.project && project) rows.push([t("isu_project"), `${project.id} · ${project.title}`]);
  if (nth) rows.push([t("isu_priority"), t("isu_priority_v", { n: nth, total: open.length, p: project?.id ?? "" })]);
  if (cost) rows.push([t("isu_cost"), t("isu_cost_v", { usd: cost.usd.toFixed(2), runs: cost.runs, share: Math.round(cost.cheap_share) })]);
  if (c.integrated) rows.push([t("isu_integrated"), t("isu_integrated_v", { n: c.integrated.n, of: c.integrated.of, last: c.integrated.commits[c.integrated.commits.length - 1] ?? "" })]);
  if (c.repo) rows.push([t("isu_repo"), c.repo]);
  if (live?.agent) rows.push([t("isu_agent"), live.agent]);
  if (live?.branch) rows.push([t("isu_branch"), live.branch]);
  if (blocked) rows.push([t("isu_blocked"), c.blocked || whyOf(c)]);
  if (c.closed) rows.push([t("isu_closed"), c.closed]);
  const idle = !live?.agent && !live?.branch && !["done", "canceled", "duplicate", "triage"].includes(status);
  return (
    <div className="screen">
      <Crumbs narrow={{ label: t("bl_title"), href: go("backlog") }} steps={[{ label: t("bl_title"), href: go("backlog") }, ...(project ? [{ label: project.id, href: go("backlog", "p", project.id) }] : []), { label: c.id }]} />
      <div className="twocol">
        <div className="main">
          <header className="isu-head">
            <div className="badges">
              <span className="id mono">{c.id}</span>
              <span className="lanebadge">
                <StatusIcon status={status} blocked={blocked} />
                {t(`lane_${status}` as Key)}
              </span>
            </div>
            <h1>{nameOf(c)}</h1>
            {c.short_name ? <p className="longtitle">{c.title}</p> : null}
          </header>
          {status === "triage" ? <Want c={c} product={product} done={() => { a.reload(); props.reload(); }} /> : null}
          <Section title={t("isu_purpose")}>
            {c.outcome?.claim || c.outcome?.criterion ? (
              <div className="card purpose">
                {c.outcome.claim ? (<><div className="lbl">{t("isu_goal")}</div><p>{c.outcome.claim}</p></>) : null}
                {c.outcome.criterion ? (<><div className="lbl">{t("isu_criterion")}</div><p>{c.outcome.criterion}</p></>) : null}
              </div>
            ) : (
              <div className="card quiet">{t("isu_no_goal")}</div>
            )}
          </Section>
          {parts.length ? (
            <Section title={t("isu_parts")} aside={<span className="figs">{t("isu_parts_done", { done, total: parts.length })}</span>}>
              <div className="card">
                <Split parts={[{ n: done, cls: "done" }, { n: parts.length - done, cls: "rest" }]} label={t("isu_parts_done", { done, total: parts.length })} />
                <SubRows c={c} />
              </div>
            </Section>
          ) : null}
          <Docs docs={c.docs ?? []} />
          {entries.length ? (
            <Section title={t("isu_said")} aside={entries.length > 3 ? <button type="button" className="link" onClick={() => setAll(!all)}>{all ? t("isu_see_less") : t("isu_see_all", { n: entries.length })}</button> : undefined}>
              <div className="card journal">
                {shown.map((e, i) => (
                  <div key={i} className="entry">
                    <div className="who">{[e.who, e.date].filter(Boolean).join(" · ")}</div>
                    <p className={all ? "" : "clamp3"}>{e.text}</p>
                  </div>
                ))}
              </div>
            </Section>
          ) : null}
          {mine.length ? (
            <Section title={t("isu_you")}>
              <div className="card list plain">
                {mine.map((m) => (<div key={m.id} className="moveline"><span className="when">{m.time}</span><span>{m.text}</span></div>))}
              </div>
            </Section>
          ) : null}
        </div>
        <aside className="datos">
          <h2>{t("isu_data")}</h2>
          <dl>
            {rows.map(([k, v]) => (<span key={k} className="kv"><dt>{k}</dt><dd>{v}</dd></span>))}
          </dl>
          {idle ? <p className="quietline">{blocked ? t("isu_nothing_blocked") : t("isu_nothing")}</p> : null}
        </aside>
      </div>
    </div>
  );
}
