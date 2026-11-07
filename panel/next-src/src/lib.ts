// What every screen shares: the texts, the panel's API with its key, and the formats of dates and dollars.
import en from "./strings/en.json";
import es from "./strings/es.json";

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

// a write of the panel (the same endpoints as today's panel): the answer's `error` is the message, `body` the text
export async function post(path: string, body?: string): Promise<void> {
  const init: RequestInit = { method: "POST" };
  if (body !== undefined) init.body = body;
  const r = await fetch(withKey(path), init);
  if (!r.ok) {
    const j = (await r.json().catch(() => ({}))) as { error?: string; why?: string };
    const why = `grant_why_${j.why}`; // a refused grant says why, in the panel's language
    throw new Error(j.why && why in es ? t(why as Key) : (j.error ?? String(r.status)));
  }
}

// the panel of today, at the same key (the screens not built yet send there)
export const oldPanel = (): string => withKey("/");

// whole dollars ($219), or with one decimal ($60.8) where a detail is read
export const usd = (x: number): string => "$" + Math.round(x).toString();
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
  permissions?: number; // requests to approve or deny (the «Needs you» badge counts them with the questions)
  paused?: string | null;
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
export interface Today {
  cost: Record<Family | "usd", number> & { avg_before: number; spark: number[] };
  integrated: { integrated: number; measured: number; yesterday: number; spark: number[] };
  reviews: { back: number; total: number; yesterday_back: number; yesterday_total: number };
}
export interface Quota {
  kind: string; // five_hour | seven_day
  percent_used: number;
  resets_at?: string | null;
}
export interface Metrics {
  days: string[];
  cost: Record<string, DayCost>;
  total: number;
  reviews?: Record<string, { clean: number; back: number }>;
  today?: Today;
  quota?: Quota[];
  since: { cost: string | null } & Record<string, string | null>; // the first day with data of each series
}

// what /api/state says of a product that the home and «Needs you» screens read
export interface Decision {
  id: string;
  title: string;
  date?: string;
  text?: string;
  options: string[];
  recommended?: string;
  details?: Record<string, { effect?: string; cost?: string; undo?: string }>;
  page?: string;
  answer: string | null;
  free?: boolean;
  sent?: string;
  blocks?: string[];
}
export interface Grant {
  group: string;
  pending: boolean;
  orphan?: string;
  command?: string;
  part?: string;
  summary?: string;
  say?: { kind: string; to?: string; refs?: string[]; what?: string } | null;
  orchestrator?: string;
  at?: string;
  expires?: string;
  minutes?: number;
  notified?: string;
}
export interface Message {
  id: number;
  time: string;
  from: string;
  text: string;
  read?: boolean;
}
export interface Agent {
  id: string;
  name: string;
  type?: string | null;
  issue?: string | null;
  sub?: string | null;
  end?: string | null;
  status: string;
  last?: string | null;
  start_ts?: number | null;
  activity_ts?: number | null;
  link?: string | null;
}
export interface Progress {
  time: string;
  agent?: string;
  text: string;
}
export interface State {
  decisions: Decision[];
  grants: Grant[];
  messages: Message[];
  agents: Agent[];
  progress: Progress[];
}

// «2026-11-07 12:28» or «2026-11-07T12:28:00» (local) as a Date
export const localDate = (s: string): Date => new Date(s.replace(" ", "T"));
export const hhmm = (s: string): string => s.slice(11, 16);
export const todayIso = (): string => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
// «hoy», «ayer» or «5 nov» of a date «YYYY-MM-DD…»
export function dayWord(s: string): string {
  const day = s.slice(0, 10);
  const y = new Date();
  y.setDate(y.getDate() - 1);
  const yesterday = `${y.getFullYear()}-${String(y.getMonth() + 1).padStart(2, "0")}-${String(y.getDate()).padStart(2, "0")}`;
  return day === todayIso() ? t("today") : day === yesterday ? t("yesterday") : dayMonth(day);
}
// whole minutes from `ts` (epoch seconds) to now, or null
export const minutesSince = (ts: number | null | undefined): number | null =>
  ts == null ? null : Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
