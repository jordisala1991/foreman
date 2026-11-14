// The shell, as in foreman's panel: on a wide screen a 248 px sidebar (the project picker and the three sections with
// their badges); on the phone the picker and the loop's pill on top and a bottom bar of three tabs. A task's page is
// #/task/ID. The theme follows the system (app.css).
import { useEffect, useState } from "react";
import { Boundary } from "./Boundary";
import { useFeedPart } from "./feed";
import { StatusPill } from "./head";
import { Chevron, Icon } from "./icons";
import { Task } from "./Task";
import { t, type Key, type Project, type Projects } from "./lib";
import { Needs } from "./Needs";
import { System } from "./System";
import { Tasks } from "./Tasks";

const TABS = ["tasks", "needs", "system"] as const;
type Tab = (typeof TABS)[number] | "task";

const route = (): { tab: Tab; id: string } => {
  const [r = "", id = ""] = location.hash.replace(/^#\/?/, "").split("/");
  return { tab: (TABS as readonly string[]).includes(r) || r === "task" ? (r as Tab) : "tasks", id: decodeURIComponent(id) };
};

function Picker({ projects, project, pick }: { projects: Project[]; project: Project; pick: (name: string) => void }) {
  return (
    <label className="picker">
      <span className="name">{project.name}</span>
      <Chevron />
      <select aria-label={t("project")} value={project.name} onChange={(e) => pick(e.target.value)}>
        {projects.map((p) => (
          <option key={p.name} value={p.name}>
            {p.name}
          </option>
        ))}
      </select>
    </label>
  );
}

const Badge = ({ n, red }: { n: number; red: boolean }) => (n ? <span className={red ? "badge red" : "badge"}>{n}</span> : null);

export function None() {
  return (
    <main className="none card">
      <h1>{t("none_title")}</h1>
      <p>{t("none_how")}</p>
      <pre>{"fm init --repo PATH --prefix AB"}</pre>
    </main>
  );
}

const KEPT = "fm-project";
const REFRESH_MS = 10_000;

export function Shell({ all: initial }: { all: Project[] }) {
  const every = useFeedPart<Projects, Project[]>("/api/projects", REFRESH_MS, (d) => d.projects) ?? initial;
  const projects = every.filter((p) => !p.broken);
  const [r, setRoute] = useState(route);
  const [name, setName] = useState<string | undefined>(() => localStorage.getItem(KEPT) ?? undefined);
  useEffect(() => {
    const go = () => {
      setRoute(route());
      window.scrollTo(0, 0);
    };
    addEventListener("hashchange", go);
    return () => removeEventListener("hashchange", go);
  }, []);
  const p = projects.find((x) => x.name === name) ?? projects[0];
  if (!p) return <None />;
  const pick = (n: string) => {
    localStorage.setItem(KEPT, n);
    setName(n);
    location.hash = "#/tasks";
  };
  const badge: Partial<Record<Tab, number>> = { needs: p.needs ?? 0, tasks: p.running ?? 0 };
  const current = (k: Tab) => (r.tab === k || (k === "tasks" && r.tab === "task") ? "page" : undefined);
  const screen =
    r.tab === "needs" ? <Needs project={p} />
    : r.tab === "system" ? <System project={p} />
    : r.tab === "task" && r.id ? <Task project={p} id={r.id} />
    : <Tasks project={p} />;
  return (
    <div className="shell">
      <aside className="side">
        <Picker projects={projects} project={p} pick={pick} />
        <nav aria-label={t("sections")} className="navs">
          {TABS.map((k) => (
            <a key={k} className="nav" href={`#/${k}`} aria-current={current(k)}>
              <Icon name={k} size={20} />
              <span className="label">{t(`tab_${k}` as Key)}</span>
              <Badge n={badge[k] ?? 0} red={k === "needs"} />
            </a>
          ))}
        </nav>
      </aside>
      <main>
        <div className="top">
          <Picker projects={projects} project={p} pick={pick} />
          <StatusPill project={p} />
        </div>
        <Boundary key={`${r.tab}/${r.id}/${p.name}`}>{screen}</Boundary>
      </main>
      <nav className="bar" aria-label={t("sections")}>
        {TABS.map((k) => (
          <a key={k} className="tab" href={`#/${k}`} aria-current={current(k)}>
            <span className="icon">
              <Icon name={k} />
              <Badge n={badge[k] ?? 0} red={k === "needs"} />
            </span>
            {t(`tab_${k}` as Key)}
          </a>
        ))}
      </nav>
    </div>
  );
}
