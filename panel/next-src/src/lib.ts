// What every screen shares: the texts, the panel's API with its key, and the formats of dates and dollars.
import enBase from "./strings/en.json";
import esBase from "./strings/es.json";
import enMore from "./strings/more.en.json"; // Backlog, Wiki and Sistema have their own files
import esMore from "./strings/more.es.json";

const es = { ...esBase, ...esMore };
const en = { ...enBase, ...enMore };

export type Key = keyof typeof es;
const LANGS: Record<string, Record<Key, string>> = { es, en };

export const lang = (): string => document.documentElement.lang || "en";

// a text of strings/<language>.json, its {name} placeholders filled from `vars`
export function t(key: Key, vars?: Record<string, string | number>): string {
  const text = (LANGS[lang()] ?? en)[key];
  return text.replace(/\{(\w+)\}/g, (m, k: string) => (vars && k in vars ? String(vars[k]) : m));
}

const key = (): string => new URLSearchParams(location.search).get("t") ?? "";
export const withKey = (path: string): string =>
  path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(key());

export async function api<T>(path: string): Promise<T> {
  const r = await fetch(withKey(path), { cache: "no-cache" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return (await r.json()) as T;
}

// a POST with no body (an action the owner asks for); the server's own words when it says no
export async function post<T>(path: string): Promise<T> {
  const r = await fetch(withKey(path), { method: "POST" });
  const body = ((await r.json().catch(() => null)) ?? {}) as { error?: string };
  if (!r.ok) throw new Error(body.error ?? String(r.status));
  return body as T;
}

// the panel of today, at the same key (the screens not built yet send there)
export const oldPanel = (): string => withKey("/");

// whole dollars ($219), or with one decimal ($60.8) where a detail is read
export const usd = (x: number): string => "$" + Math.round(x).toString();
export const usd2 = (x: number): string => "$" + x.toFixed(2);
export const usd1 = (x: number): string => "$" + x.toFixed(1);

const noon = (iso: string): Date => new Date(iso + "T12:00:00");
export const dayLabel = (iso: string, opts: Intl.DateTimeFormatOptions): string =>
  new Intl.DateTimeFormat(lang(), opts).format(noon(iso)).replace(/\.$/, "").replace(/\. /, " ");
// «sáb 1»: the weekday, short, and the day
export const shortDay = (iso: string): string =>
  `${dayLabel(iso, { weekday: "short" })} ${noon(iso).getDate()}`;
// «1 nov»
export const dayMonth = (iso: string): string => `${noon(iso).getDate()} ${dayLabel(iso, { month: "short" })}`;

// what /api/projects says of a product (the fields the new panel reads)
export interface Product {
  name: string;
  title: string;
  next?: boolean;
  broken?: boolean;
  decisions?: number;
  unread?: number;
  agents?: number;
  paused?: string | null;
  pill?: string; // the status pill's own words (the «All» entry of the picker says how many are running)
  active?: boolean;
  orchestrator_health?: { verdict: string; last_pass?: string | null } | null;
}

export interface Projects {
  projects: Product[];
  language?: string;
}

// /api/metrics (lib/foreman/metrics.py `build`): the fields the charts built so far read
export type Family = "opus" | "sonnet" | "haiku" | "other";
export type DayCost = Record<Family | "usd", number>;
export interface Metrics {
  days: string[];
  cost: Record<string, DayCost>;
  total: number;
  since: { cost: string | null } & Record<string, string | null>; // the first day with data of each series
}

// /api/agents (lib/foreman/agents_screen.py): who is running, who finished today, when the next pass is due
export interface AgentCard {
  id: string;
  p: string; // the product
  name: string;
  task: string; // the issue's short name, else the agent's name
  issue: string | null;
  sub: string | null;
  type: string | null;
  family: Family | null;
  status: "working" | "holds a lock" | "waiting for a lock" | "waiting for its task" | "maybe stuck";
  since: number | null; // epoch seconds
  sign: number | null; // the last sign of life, epoch seconds
  msg: string | null;
  session: boolean; // its own Claude Code session: its conversation can be opened
}
export interface DoneRun {
  agent: string;
  p: string;
  task: string;
  issue: string;
  sub: string | null;
  type: string | null;
  outcome: "ok" | "partial" | "failed" | null;
  minutes: number | null;
  family: Family | null;
  usd: number;
  end: string; // «YYYY-MM-DD HH:MM», local
}
export interface AgentsData {
  projects: string[];
  live: AgentCard[];
  done: DoneRun[];
  next_pass: number | null; // epoch seconds
}

// «25 min», «1 h 5 min»
export const duration = (minutes: number): string => {
  const m = Math.max(0, Math.round(minutes));
  return m < 60 ? t("min", { n: m }) : t("hour_min", { h: Math.floor(m / 60), m: m % 60 });
};
