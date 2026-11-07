// Backlog in the new panel: the list of the tasks grouped by status (as the panel of today, refreshed), the Projects
// view, a project's page and a task's page («ficha»), and the viewer of a document. The routes are the hash after
// #/backlog: nothing (the tasks), projects, p/ID (a project), doc/PATH (a document) and ID (a task).
import { useMemo, useState, type ReactNode } from "react";
import "./more.css";
import { ScreenHead } from "../head";
import { t, type Key } from "../lib";
import { Doc } from "./Doc";
import { Issue } from "./Issue";
import { Failed, Loading, Section, Split } from "./parts";
import { go, post, q, rest, useApi } from "./api";
import { useSortable } from "./sortable";
import type { BacklogAnswer, Card, MoreProduct, ProjectCard, StateAnswer } from "./types";

export const LANES = ["in progress", "ready", "refining", "backlog", "triage", "done", "canceled", "duplicate"] as const;
export type Lane = (typeof LANES)[number];
const INACTIVE = new Set(["done", "canceled", "duplicate", "triage"]);
const FOLDED = new Set<Lane>(["triage", "done", "canceled", "duplicate"]); // from «Por clasificar» on, folded

export const isBlocked = (c: Card): boolean => !!(c.blocked || c.blocked_rel?.length);
export const nameOf = (c: Card): string => c.short_name || c.title;
export const whyOf = (c: Card): string =>
  c.why || (c.blocked_rel?.length ? t("waits_for", { ids: c.blocked_rel.join(", ") }) : c.blocked ? t("blocked") : "");

export function StatusIcon({ status, blocked }: { status: string; blocked?: boolean }) {
  const common = { width: 16, height: 16, viewBox: "0 0 16 16", "aria-hidden": true, className: "sico" } as const;
  if (blocked)
    return (
      <svg {...common} className="sico red">
        <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
        <path d="M4.2 11.8 11.8 4.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    );
  switch (status) {
    case "in progress":
      return (
        <svg {...common} className="sico amber">
          <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <path d="M8 4.2a3.8 3.8 0 0 1 0 7.6z" fill="currentColor" />
        </svg>
      );
    case "done":
      return (
        <svg {...common} className="sico green">
          <circle cx="8" cy="8" r="6.2" fill="currentColor" />
          <path d="m5.2 8.2 2 2 3.6-4" fill="none" stroke="var(--surface)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      );
    case "canceled":
    case "duplicate":
      return (
        <svg {...common} className="sico grey">
          <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <path d="m5.6 5.6 4.8 4.8M10.4 5.6l-4.8 4.8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      );
    case "triage":
      return (
        <svg {...common} className="sico accent">
          <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="8" cy="8" r="1.8" fill="currentColor" />
        </svg>
      );
    case "refining":
    case "backlog":
      return (
        <svg {...common} className="sico grey">
          <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.6" strokeDasharray={status === "refining" ? "3 2.4" : undefined} />
        </svg>
      );
    default:
      return (
        <svg {...common} className="sico accent">
          <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
        </svg>
      );
  }
}

export const subState = (s: { status?: string | undefined }): "done" | "doing" | "todo" | "dropped" | "moved" | "unknown" =>
  s.status === "done" ? "done" : s.status === "in progress" ? "doing" : s.status === "todo" ? "todo" : s.status === "canceled" ? "dropped" : s.status === "moved" ? "moved" : "unknown";
export const subLive = (c: Card) => c.subs.filter((s) => !["dropped", "moved"].includes(subState(s)));

export function SubRows({ c }: { c: Card }) {
  return (
    <div className="subs">
      {c.subs.map((s) => {
        const st = subState(s);
        return (
          <div key={s.id} className={`srow st-${st}`}>
            <StatusIcon status={st === "done" ? "done" : st === "doing" ? "in progress" : st === "dropped" ? "canceled" : "ready"} />
            <span className="id">{s.id}</span>
            <span className="tt">{s.title || t(`sub_${st}` as Key)}</span>
            <span className="meta">
              {s.doc ? <a href={go("backlog", "doc", s.doc)}>{(s.ref ?? "").replace(/\.md$/, "")}</a> : s.ref ? <code>{s.ref}</code> : null}
            </span>
          </div>
        );
      })}
    </div>
  );
}

interface View {
  cards: { c: Card; lane: Lane; production: boolean; agent: string | undefined }[];
  projects: ProjectCard[];
  loose: Card[];
}

function useView(a: BacklogAnswer["project"]["backlog"], st: StateAnswer["project"] | undefined): View {
  return useMemo(() => {
    const live = st?.issue_live ?? {};
    const all = [...a.projects.flatMap((p) => p.issues), ...a.issues];
    const cards = all.map((c) => {
      const l = live[c.id];
      const status = (l?.status ?? c.status) as Lane;
      return { c, lane: status, production: !!l?.production, agent: l?.agent };
    });
    return { cards, projects: a.projects, loose: a.issues };
  }, [a, st]);
}

function Group(props: { lane: Lane; rows: View["cards"]; state: StateAnswer["project"] | undefined; showProject: boolean }) {
  const { lane, rows, state } = props;
  const [open, setOpen] = useState(!FOLDED.has(lane));
  const [subsOpen, setSubsOpen] = useState<Set<string>>(new Set());
  const shown = rows.filter((r) => !r.c.stub);
  const stubs = rows.length - shown.length;
  const slots = state?.targets.production_slots ?? null;
  const want = state?.targets.ready_target ?? null;
  const blocked = rows.filter((r) => isBlocked(r.c)).length;
  const ready = rows.length - blocked;
  let meta: ReactNode = null;
  if (lane === "in progress") {
    const n = rows.filter((r) => r.production).length;
    meta =
      slots != null ? (
        <span className={n < slots ? "meta red" : "meta"}>{t("meta_working", { n, goal: slots })}</span>
      ) : (
        <span className="meta">{t("meta_working_plain", { n })}</span>
      );
  } else if (lane === "ready") {
    meta = (
      <span className="meta">
        {[t("meta_ready", { n: ready }), want != null ? t("meta_wanted", { n: want }) : "", blocked ? t("meta_blocked", { n: blocked }) : ""]
          .filter(Boolean)
          .join(" · ")}
      </span>
    );
  }
  return (
    <section className="group">
      <button type="button" className="grouphead" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className={open ? "car open" : "car"} aria-hidden="true">
          ▾
        </span>
        <StatusIcon status={lane} />
        <span className="gname">{t(`lane_${lane}` as Key)}</span>
        <span className="gcount">{rows.length}</span>
        {meta}
      </button>
      {open ? (
        <div className="grows">
          {shown.length === 0 ? (
            <div className="empty">
              {t("empty_lane")}
            </div>
          ) : null}
          {shown.map(({ c, agent }) => {
            const why = isBlocked(c) && !INACTIVE.has(c.status) ? whyOf(c) : "";
            const live = subLive(c);
            const done = live.filter((s) => subState(s) === "done").length;
            const unfolded = subsOpen.has(c.id);
            return (
              <div key={c.id}>
                <div className="row">
                  <a className="rowmain" href={go("backlog", c.id)} title={c.title}>
                    <StatusIcon status={lane} blocked={isBlocked(c) && !INACTIVE.has(lane)} />
                    <span className="id mono">{c.id}</span>
                    <span className="nm clamp2">{nameOf(c)}</span>
                    {why ? <span className="why">{why}</span> : null}
                  </a>
                  <span className="tags">
                    {live.length ? (
                      <button
                        type="button"
                        className="subt"
                        aria-expanded={unfolded}
                        aria-label={t("subs_toggle")}
                        onClick={() => {
                          const n = new Set(subsOpen);
                          if (!n.delete(c.id)) n.add(c.id);
                          setSubsOpen(n);
                        }}
                      >
                        {done}/{live.length}
                      </button>
                    ) : null}
                    {props.showProject && c.project ? <span className="ptag">{c.project}</span> : null}
                    {agent ? <span className="adot" title={t("agent_working", { name: agent })} /> : null}
                    {c.plan.length ? (
                      <span className="plan" title={t("has_plan")}>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                          <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
                          <path d="M14 3v5h5M9 13h6M9 17h4" />
                        </svg>
                      </span>
                    ) : null}
                  </span>
                </div>
                {unfolded ? <SubRows c={c} /> : null}
              </div>
            );
          })}
          {stubs ? <div className="empty">{t("older_closed", { n: stubs })}</div> : null}
        </div>
      ) : null}
    </section>
  );
}

function emptyText(lane: Lane, ready: number): string {
  if (lane === "in progress") return ready ? t("empty_working", { n: ready }) : t("empty_working_none");
  return t("empty_lane");
}

function Moves({ state }: { state: StateAnswer["project"] | undefined }) {
  const moves = (state?.activity ?? []).slice(0, 5);
  if (!moves.length) return null;
  return (
    <Section title={t("bl_recent_moves")}>
      <div className="card list plain">
        {moves.map((m) => (
          <div key={m.id} className="moveline">
            <span className="when">{m.time}</span>
            <span>{m.text}</span>
          </div>
        ))}
      </div>
    </Section>
  );
}

export function projectStats(cards: Card[]) {
  const live = cards.filter((c) => !["canceled", "duplicate", "triage"].includes(c.status));
  const open = cards.filter((c) => !INACTIVE.has(c.status));
  return {
    total: live.length,
    done: live.filter((c) => c.status === "done").length,
    started: open.filter((c) => c.status === "in progress").length,
    ready: open.filter((c) => c.status === "ready" && !isBlocked(c)).length,
    blocked: open.filter(isBlocked).length,
    refining: open.filter((c) => c.status === "refining").length,
    waiting: open.filter((c) => c.status === "backlog").length,
  };
}

export const StatsLine = ({ s }: { s: ReturnType<typeof projectStats> }) => (
  <span className="stats">
    {s.started ? <span>{t("n_started", { n: s.started })}</span> : null}
    {s.ready ? <span>{t("n_ready", { n: s.ready })}</span> : null}
    {s.blocked ? <span className="red">{t("n_blocked", { n: s.blocked })}</span> : null}
  </span>
);

function ProjectsView({ view, reload, product }: { view: View; reload: () => void; product: MoreProduct }) {
  const [error, setError] = useState("");
  const move = (id: string, target: string) => {
    const at = view.projects.findIndex((p) => p.id === target);
    void post(`/api/move?p=${q(product.name)}&kind=project&id=${q(id)}&a=${at}`).then((r) => {
      setError(r.ok ? "" : r.error);
      reload();
    });
  };
  const sort = useSortable(move);
  const ids = view.projects.map((p) => p.id);
  return (
    <div className="card list plain" data-sortable>
      {error ? <div className="inline-error">{error}</div> : null}
      {view.projects.map((p) => {
        const s = projectStats(p.issues);
        return (
          <div key={p.id} className={`prow ${sort.rowClass(p.id)}`} data-sid={p.id}>
            <button {...sort.grip(p.id, ids)} />
            <a href={go("backlog", "p", p.id)} className="pmain">
              <span className="phead">
                <span className="id mono">{p.id}</span>
                <span className="nm">{p.title}</span>
                <span className="pstatus">{t(`ps_${p.status}` as Key)}</span>
              </span>
              <span className="pbar">
                <Split parts={[{ n: s.done, cls: "done" }, { n: s.total - s.done, cls: "rest" }]} />
                <span className="figs">{t("n_of_done", { done: s.done, total: s.total })}</span>
              </span>
              <StatsLine s={s} />
            </a>
          </div>
        );
      })}
      {view.loose.length ? (
        <div className="prow fixed">
          <span className="gap" />
          <a href={go("backlog", "p", "-")} className="pmain">
            <span className="phead">
              <span className="nm">{t("bl_no_project")}</span>
            </span>
            <span className="figs">{t("n_tasks", { n: view.loose.length })}</span>
          </a>
        </div>
      ) : null}
    </div>
  );
}

export function Backlog({ product }: { product: MoreProduct }) {
  const parts = rest();
  const bl = useApi<BacklogAnswer>(`/api/backlog?p=${q(product.name)}&closed=recent`, 30000);
  const st = useApi<StateAnswer>(`/api/state?p=${q(product.name)}`, 30000);
  const [filter, setFilter] = useState("");
  const empty = { projects: [], issues: [], archive: [], counts: {} };
  const view = useView(bl.data?.project.backlog ?? empty, st.data?.project);
  const head = parts[0];
  if (head === "doc") return <Doc product={product} path={parts.slice(1).join("/")} />;
  if (head && head !== "projects" && head !== "p")
    return <Issue product={product} id={head} backlog={bl.data?.project.backlog} state={st.data?.project} reload={() => { bl.reload(); st.reload(); }} />;
  if (head === "p" && parts[1])
    return (
      <ProjectPage product={product} id={parts[1]} view={view} state={st.data?.project} reload={bl.reload} />
    );
  const projectsView = head === "projects";
  const rows = view.cards.filter((r) => !filter || r.c.project === filter);
  const ready = rows.filter((r) => r.lane === "ready" && !isBlocked(r.c)).length;
  return (
    <div className="screen">
      <ScreenHead title={t("bl_title")} product={product}>
        <div className="seg" role="group" aria-label={t("bl_view")}>
          <button type="button" aria-pressed={!projectsView} onClick={() => (location.hash = go("backlog"))}>
            {t("bl_tasks")}
          </button>
          <button type="button" aria-pressed={projectsView} onClick={() => (location.hash = go("backlog", "projects"))}>
            {t("bl_projects")}
          </button>
        </div>
        {!projectsView && view.projects.length > 1 ? (
          <label className="select">
            <span className="lbl">{t("bl_project_filter")}:</span>
            <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label={t("bl_project_filter")}>
              <option value="">{t("bl_all_projects")}</option>
              {view.projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id} · {p.title}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </ScreenHead>
      {bl.failed && !bl.data ? <Failed retry={bl.reload} /> : !bl.data ? <Loading /> : bl.data.project.backlog.missing ? (
        <div className="card quiet">{t("bl_missing")}</div>
      ) : projectsView ? (
        <ProjectsView view={view} reload={bl.reload} product={product} />
      ) : (
        <div className="groups">
          {LANES.map((lane) => {
            const inLane = rows.filter((r) => r.lane === lane);
            if (!inLane.length && !["in progress", "ready"].includes(lane)) return null;
            return (
              <GroupWrap key={lane} lane={lane} rows={inLane} state={st.data?.project} showProject={!filter && view.projects.length > 1} ready={ready} />
            );
          })}
        </div>
      )}
      {!projectsView ? <Moves state={st.data?.project} /> : null}
    </div>
  );
}

// a group, with its explanation when it is empty
function GroupWrap(props: { lane: Lane; rows: View["cards"]; state: StateAnswer["project"] | undefined; showProject: boolean; ready: number }) {
  if (props.rows.length === 0)
    return (
      <section className="group">
        <div className="grouphead static">
          <StatusIcon status={props.lane} />
          <span className="gname">{t(`lane_${props.lane}` as Key)}</span>
          <span className="gcount">0</span>
        </div>
        <div className="empty">{emptyText(props.lane, props.ready)}</div>
      </section>
    );
  return <Group {...props} />;
}

function ProjectPage(props: { product: MoreProduct; id: string; view: View; state: StateAnswer["project"] | undefined; reload: () => void }) {
  const { product, id, view, state } = props;
  const p = view.projects.find((x) => x.id === id);
  const loose = id === "-";
  const cards = loose ? view.loose : (p?.issues ?? []);
  const [error, setError] = useState("");
  const rows = view.cards.filter((r) => cards.includes(r.c));
  const active = rows.filter((r) => !INACTIVE.has(r.lane)).map((r) => r.c.id);
  const move = (cid: string, target: string) => {
    const at = active.indexOf(target);
    void post(`/api/move?p=${q(product.name)}&id=${q(cid)}&a=${at}`).then((r) => {
      setError(r.ok ? "" : r.error);
      props.reload();
    });
  };
  const sort = useSortable(move);
  if (!p && !loose) return <div className="screen"><ScreenHead title={t("bl_title")} product={product} /><Loading /></div>;
  const s = projectStats(cards);
  const mine = (state?.activity ?? []).filter((m) => m.move && cards.some((c) => c.id === m.move)).slice(0, 8);
  const title = loose ? t("bl_no_project") : (p?.title ?? id);
  return (
    <div className="screen">
      <div className="crumbs">
        <span className="crumb-wide">
          <span aria-hidden="true">‹ </span>
          <a href={go("backlog")}>{t("bl_title")}</a>
          <span className="sep"> / </span>
          <a href={go("backlog", "projects")}>{t("bl_projects")}</a>
          <span className="sep"> / </span>
          <span className="here">{loose ? "-" : id}</span>
        </span>
        <a className="crumb-narrow" href={go("backlog", "projects")}>
          ‹ {t("bl_projects")}
        </a>
      </div>
      <div className="twocol">
        <div className="main">
          <header className="isu-head">
            {p ? (
              <div className="badges">
                <span className="id mono">{p.id}</span>
                <span className="pstatus">{t(`ps_${p.status}` as Key)}</span>
              </div>
            ) : null}
            <h1>{title}</h1>
          </header>
          <Section title={t("pj_progress")}>
            <div className="card">
              <Split parts={[{ n: s.done, cls: "done" }, { n: s.started, cls: "doing" }, { n: s.ready, cls: "ready" }, { n: s.blocked, cls: "blocked" }, { n: Math.max(0, s.total - s.done - s.started - s.ready - s.blocked), cls: "rest" }]} label={t("n_of_done", { done: s.done, total: s.total })} />
              <div className="legend">
                <span><i className="sw done" />{t("n_of_done", { done: s.done, total: s.total })}</span>
                {s.started ? <span><i className="sw doing" />{t("n_started", { n: s.started })}</span> : null}
                {s.ready ? <span><i className="sw ready" />{t("n_ready", { n: s.ready })}</span> : null}
                {s.blocked ? <span className="red"><i className="sw blocked" />{t("n_blocked", { n: s.blocked })}</span> : null}
              </div>
            </div>
          </Section>
          <Section title={t("pj_tasks")}>
            {error ? <div className="inline-error">{error}</div> : null}
            <div className="groups">
              {LANES.map((lane) => {
                const inLane = rows.filter((r) => r.lane === lane);
                if (!inLane.length) return null;
                const sortable = !INACTIVE.has(lane);
                return (
                  <section className="group" key={lane}>
                    <div className="grouphead static">
                      <StatusIcon status={lane} />
                      <span className="gname">{t(`lane_${lane}` as Key)}</span>
                      <span className="gcount">{inLane.length}</span>
                    </div>
                    <div className="grows" data-sortable>
                      {inLane.filter((r) => !r.c.stub).map(({ c }) => (
                        <div key={c.id} className={`row ${sortable ? sort.rowClass(c.id) : ""}`} data-sid={c.id}>
                          {sortable ? <button {...sort.grip(c.id, inLane.map((r) => r.c.id))} /> : null}
                          <a className="rowmain" href={go("backlog", c.id)} title={c.title}>
                            <StatusIcon status={lane} blocked={isBlocked(c) && sortable} />
                            <span className="id mono">{c.id}</span>
                            <span className="nm clamp2">{nameOf(c)}</span>
                            {isBlocked(c) && sortable ? <span className="why">{whyOf(c)}</span> : null}
                          </a>
                        </div>
                      ))}
                    </div>
                  </section>
                );
              })}
            </div>
          </Section>
          <Section title={t("pj_what")}>
            <div className="card list plain">
              {mine.length ? mine.map((m) => (
                <div key={m.id} className="moveline"><span className="when">{m.time}</span><span>{m.text}</span></div>
              )) : <div className="empty">{t("no_moves")}</div>}
            </div>
          </Section>
        </div>
        <aside className="datos">
          <h2>{t("pj_data")}</h2>
          <dl>
            {p ? <><dt>{t("pj_status")}</dt><dd>{t(`ps_${p.status}` as Key)}</dd></> : null}
            {p?.priority ? <><dt>{t("pj_priority")}</dt><dd>{view.projects.indexOf(p) + 1 ? t("pj_nth", { n: view.projects.indexOf(p) + 1, total: view.projects.length }) : p.priority}</dd></> : null}
            {p?.repos?.length ? <><dt>{t("pj_repos")}</dt><dd>{p.repos.join(", ")}</dd></> : null}
            <dt>{t("pj_count")}</dt><dd>{t("n_tasks", { n: cards.length })}</dd>
          </dl>
        </aside>
      </div>
    </div>
  );
}
