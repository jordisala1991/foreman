// Sistema in the new panel: a line that says whether something needs a look (and what to do about it), the Mac in four
// figures with a bar (colour only past the thresholds of the panel of today), the Claude quota, the recurring tasks,
// the latest lines of the timeline, the products and, folded, the technical details. No actions, as today.
import { type ReactNode } from "react";
import { productState, ScreenHead } from "../head";
import { t, type Key, type Product } from "../lib";
import { ago, q, useApi } from "./api";
import { Failed, Loading, Section } from "./parts";
import type { MoreProduct, StateAnswer } from "./types";

interface Mac {
  mem_free_pct: number | null;
  mem_total_gb: number | null;
  swap_gb: number | null;
  swap_total_gb: number | null;
  disk_free_gb: number;
  disk_total_gb: number;
  load: number;
  ncpu: number;
  top: { cpu: number; cmd: string }[];
}
interface Session {
  session: string;
  name?: string;
  project: string;
  version?: string;
  behind?: boolean;
}
interface Overview {
  projects: (Product & { broken?: boolean; error?: string })[];
  mac: Mac | null;
  quota: { kind: string; percent_used: number; resets_at: string | number | null }[];
  errors: { text: string; detail?: string }[];
  sessions?: Session[];
  foreman_installed?: { version: string; root: string } | null;
  foreman_version?: string;
  foreman_root?: string;
  foreman_follows?: boolean;
  foreman_warning?: string | null;
}

type Level = "ok" | "warn" | "bad";
const level = (v: number, warn: number, bad: number): Level => (v >= bad ? "bad" : v >= warn ? "warn" : "ok");
const num = (x: number): string => String(x).replace(".", lang() === "es" ? "," : ".");
const lang = (): string => document.documentElement.lang || "en";

function Gauge(props: { label: string; value: string; sub: string; frac: number; level: Level }) {
  return (
    <div className={`gauge ${props.level}`}>
      <div className="glabel">{props.label}</div>
      <div className="gvalue">{props.value}</div>
      <div className="gbar" role="img" aria-label={`${props.label} ${props.value}`}>
        <i style={{ width: `${Math.round(100 * Math.max(0, Math.min(1, props.frac)))}%` }} />
      </div>
      <div className="gsub">{props.sub}</div>
    </div>
  );
}

// what needs a look: [text, level]
function problems(o: Overview, product: MoreProduct, rec: StateAnswer["project"]["recurring"]): [string, Level][] {
  const out: [string, Level][] = [];
  const m = o.mac;
  if (m) {
    const used = m.mem_free_pct == null ? null : 100 - m.mem_free_pct;
    if (m.swap_gb != null && m.swap_total_gb && level(m.swap_gb, 4, 8) !== "ok")
      out.push([t("sys_swap_full", { used: num(m.swap_gb), total: num(m.swap_total_gb) }), level(m.swap_gb, 4, 8)]);
    if (used != null && level(used, 65, 80) !== "ok") out.push([t("sys_mem_high", { pct: used }), level(used, 65, 80)]);
    if (level(m.disk_total_gb - m.disk_free_gb, m.disk_total_gb - 100, m.disk_total_gb - 50) !== "ok")
      out.push([t("sys_disk_low", { gb: m.disk_free_gb }), level(m.disk_total_gb - m.disk_free_gb, m.disk_total_gb - 100, m.disk_total_gb - 50)]);
  }
  for (const x of o.quota) {
    const l = level(x.percent_used, 80, 95);
    if (l !== "ok") out.push([t("sys_quota_high", { kind: t(x.kind === "five_hour" ? "sys_quota_5" : "sys_quota_7").toLowerCase(), pct: Math.round(x.percent_used) }), l]);
  }
  const late = rec.filter((r) => r.late && !r.inactive).map((r) => r.name);
  if (late.length) out.push([t("sys_late", { names: late.join(", ") }), "warn"]);
  const behind = (o.sessions ?? []).filter((s) => s.behind);
  for (const s of behind)
    out.push([t("sys_old_session", { project: o.projects.find((p) => p.name === s.project)?.title ?? s.project, version: s.version ?? "?" }), "warn"]);
  for (const e of o.errors) out.push([e.text, "bad"]);
  if (o.foreman_warning) out.push([o.foreman_warning, "warn"]);
  void product;
  return out;
}

function QuotaBar({ x }: { x: Overview["quota"][number] }) {
  const l = level(x.percent_used, 80, 95);
  const resets = x.resets_at == null ? "" : typeof x.resets_at === "number" ? new Date(x.resets_at * 1000) : new Date(x.resets_at);
  const when = resets instanceof Date && !Number.isNaN(resets.getTime()) ? new Intl.DateTimeFormat(lang(), { weekday: "short", hour: "2-digit", minute: "2-digit" }).format(resets) : "";
  return (
    <div className={`quota ${l}`}>
      <div className="qhead">
        <span>{t(x.kind === "five_hour" ? "sys_quota_5" : "sys_quota_7")}</span>
        <b>{Math.round(x.percent_used)} %</b>
      </div>
      <div className="gbar"><i style={{ width: `${Math.min(100, x.percent_used)}%` }} /></div>
      {when ? <div className="gsub">{t("sys_quota_resets", { when })}</div> : null}
    </div>
  );
}

// the schedule words the config writes in English («every 60 min», «with each new model, and monthly») in Spanish
const EVERY_ES: [RegExp, string][] = [
  [/\bevery\b/g, "cada"], [/\bwith each new model\b/g, "con cada modelo nuevo"], [/\bmonthly\b/g, "cada mes"],
  [/\bweekly\b/g, "cada semana"], [/\bdaily\b/g, "cada día"], [/\bhourly\b/g, "cada hora"], [/\band\b/g, "y"],
  [/\bhours?\b/g, "h"], [/\bdays?\b/g, "días"], [/\bweeks?\b/g, "semanas"],
];
// «every 1 days» is «every day» (singular for 1, in English too)
const ONE_ES: Record<string, string> = { hour: "hora", day: "día", week: "semana" };
const every = (x: string): string => {
  const es = lang() === "es";
  const one = x.replace(/\bevery 1 (hour|day|week)s?\b/g, (_m, u: string) => (es ? "cada " + ONE_ES[u] : "every " + u));
  return es ? EVERY_ES.reduce((acc, [re, to]) => acc.replace(re, to), one) : one;
};
const short = (x: string, n = 160): string => (x.length > n ? x.slice(0, n - 1).trimEnd() + "…" : x);

const PRODUCT_STATE: Record<string, Key> = { running: "sys_p_running", paused: "sys_p_paused", silent: "sys_p_silent", resting: "sys_p_resting" };

export function System({ product }: { product: MoreProduct }) {
  const ov = useApi<Overview>("/api/projects", 30000);
  const st = useApi<StateAnswer>(`/api/state?p=${q(product.name)}`, 30000);
  const head = <ScreenHead title={t("sys_title")} product={product} />;
  // both answers before the first drawing: the overview is often kept from the shell's own read, and drawing it alone
  // makes the part that waits for the state push everything down when it comes
  if (!ov.data || (!st.data && !st.failed)) {
    return <div className="screen">{head}{ov.failed && !ov.data ? <Failed retry={ov.reload} /> : <Loading />}</div>;
  }
  const o = ov.data;
  const state = st.data?.project;
  const rec = state?.recurring ?? [];
  const list = problems(o, product, rec);
  const m = o.mac;
  const used = m && m.mem_free_pct != null ? 100 - m.mem_free_pct : null;
  const today = new Date().toLocaleDateString("sv");
  const feed = [
    ...(state?.notes ?? []).map((n) => ({ h: n.time, who: "orchestrator", text: n.text })),
    ...(state?.progress ?? []).filter((x) => String(x.time).slice(0, 10) === today).map((x) => ({ h: String(x.time).slice(11, 16), who: x.agent ?? "?", text: x.text })),
  ].sort((a, b) => b.h.localeCompare(a.h)).slice(0, 40);
  const missing: Key[] = [];
  if (!m) missing.push("sys_no_mac");
  if (!o.quota.length) missing.push("sys_no_quota");
  if (!state) missing.push("sys_no_state");
  const worst: Level = list.some(([, l]) => l === "bad") ? "bad" : "warn";
  const summary = (
    <div className={list.length ? `card summary ${worst}` : missing.length ? "card summary" : "card summary good"} role="status">
      <h2>{list.length ? (list.length === 1 ? t("sys_look_1") : t("sys_look_n", { n: list.length })) : missing.length ? t("sys_no_data") : t("sys_all_good")}</h2>
      {list.length ? (
        <ul>{list.map(([text, l], i) => <li key={i} className={l}>{text}</li>)}</ul>
      ) : (
        <p>{missing.length ? t("sys_no_data_text", { what: missing.map((k) => t(k)).join(", ") }) : t("sys_all_good_text")}</p>
      )}
    </div>
  );
  const mac: ReactNode = m ? (
    <Section title={t("sys_mac")}>
      <div className="card">
        <div className="gauges">
          <Gauge label={t("sys_mem")} value={used == null ? "?" : `${used} %`} sub={t("sys_mem_sub", { gb: m.mem_total_gb ?? "?" })} frac={(used ?? 0) / 100} level={used == null ? "ok" : level(used, 65, 80)} />
          <Gauge label={t("sys_disk")} value={`${m.disk_free_gb} GB`} sub={t("sys_disk_sub", { gb: m.disk_total_gb })} frac={1 - m.disk_free_gb / m.disk_total_gb} level={level(m.disk_total_gb - m.disk_free_gb, m.disk_total_gb - 100, m.disk_total_gb - 50)} />
          <Gauge label={t("sys_swap")} value={`${num(m.swap_gb ?? 0)} GB`} sub={t("sys_swap_sub", { gb: num(m.swap_total_gb ?? 0) })} frac={m.swap_total_gb ? (m.swap_gb ?? 0) / m.swap_total_gb : 0} level={level(m.swap_gb ?? 0, 4, 8)} />
          <Gauge label={t("sys_load")} value={num(m.load)} sub={t("sys_load_sub", { n: m.ncpu })} frac={m.load / m.ncpu} level={level(m.load / m.ncpu, 0.6, 0.9)} />
        </div>
        <h3>{t("sys_cpu")}</h3>
        {m.top.length ? (
          m.top.map((x, i) => (
            <div key={i} className={`cpu ${level(x.cpu, 100, 400)}`}>
              <span className="c">{x.cmd}</span>
              <span className="gbar"><i style={{ width: `${Math.min(100, x.cpu / m.ncpu)}%` }} /></span>
              <span className="p">{Math.round(x.cpu)} %</span>
            </div>
          ))
        ) : (
          <div className="quietline">{t("sys_cpu_idle")}</div>
        )}
      </div>
    </Section>
  ) : null;
  const sessions = o.sessions ?? [];
  return (
    <div className="screen sistema">
      {head}
      {summary}
      <div className="syscols">
        <div className="col">
          {mac}
          <Section title={t("sys_quota")}>
            <div className="card">
              {o.quota.length ? o.quota.map((x) => <QuotaBar key={x.kind} x={x} />) : <div className="quietline">{t("sys_quota_none")}</div>}
              <a className="link" href="#/how">{t("sys_quota_history")}</a>
            </div>
          </Section>
        </div>
        <div className="col">
          <Section title={t("sys_recurring")}>
            <div className="card list plain">
              {rec.length ? (
                rec.map((r) => {
                  const last = r.parts[0]?.last;
                  const cls = r.inactive ? "grey" : r.late ? "red" : "green";
                  return (
                    <div key={r.name} className="rec">
                      <span className={`dot ${cls}`} />
                      <span className="rname">{r.name}</span>
                      <span className="rmeta">
                        {r.every ? every(r.every) + " · " : ""}
                        {last ? t("sys_last", { when: ago(last) }) : t("sys_never")}
                        {r.late && !r.inactive ? <b className="red"> · {t("sys_late_tag")}</b> : r.inactive ? ` · ${t("sys_resting_tag")}` : ""}
                      </span>
                    </div>
                  );
                })
              ) : (
                <div className="empty">{t("sys_recurring_none")}</div>
              )}
            </div>
          </Section>
          <Section title={t("sys_last_hour")}>
            <div className="card list plain timeline">
              {feed.length ? (
                feed.map((x, i) => (
                  <div key={i} className="tl">
                    <span className="when">{x.h}</span>
                    <span className={x.who === "orchestrator" ? "who orch" : "who"}>{x.who === "orchestrator" ? t("who_orchestrator") : x.who}</span>
                    <span className="what">{short(x.text)}</span>
                  </div>
                ))
              ) : (
                <div className="empty">{t("sys_last_none")}</div>
              )}
            </div>
          </Section>
          <Section title={t("sys_products")}>
            <div className="card list plain">
              {o.projects.map((p) => {
                const s = p.broken ? null : productState(p);
                return (
                  <div key={p.name} className="rec">
                    <span className={`dot ${p.broken ? "red" : s === "running" ? "green" : s === "resting" ? "grey" : "amber"}`} />
                    <span className="rname">{p.title}</span>
                    <span className={`rmeta pst ${s ?? "broken"}`}>{p.broken ? t("sys_p_broken") : t(PRODUCT_STATE[s ?? "resting"] ?? "sys_p_resting")}</span>
                  </div>
                );
              })}
            </div>
          </Section>
          <details className="card tech">
            <summary>{t("sys_tech")}</summary>
            {o.foreman_version ? <p>{t("sys_foreman", { version: o.foreman_version })}{o.foreman_follows ? ` · ${t("sys_follows")}` : ""}</p> : null}
            {o.foreman_root ? <p className="mono path">{o.foreman_root}</p> : null}
            {sessions.length ? (
              <>
                <h3>{t("sys_sessions")}</h3>
                {sessions.map((s) => (
                  <div key={s.session} className="rec">
                    <span className={`dot ${s.behind ? "amber" : "green"}`} />
                    <span className="rname">{o.projects.find((p) => p.name === s.project)?.title ?? s.project} · {s.name ?? s.session.slice(0, 8)}</span>
                    <span className="rmeta">{s.behind ? t("sys_session_behind", { version: s.version ?? "?", installed: o.foreman_installed?.version ?? "?" }) : t("sys_session_current")}</span>
                  </div>
                ))}
              </>
            ) : null}
          </details>
        </div>
      </div>
      {st.failed && !st.data ? <Failed retry={st.reload} /> : null}
    </div>
  );
}
