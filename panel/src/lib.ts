// What every screen shares: the texts, the panel's API with its key, the formats, and the shapes fm/panel.py answers.
import en from "./strings/en.json";
import es from "./strings/es.json";

export type Key = keyof typeof en;
const LANGS: Record<string, Record<Key, string>> = { es, en };

export const lang = (): string => document.documentElement.lang || "en";

// a text of strings/<language>.json, its {name} placeholders filled from `vars`
export function t(key: Key, vars?: Record<string, string | number>): string {
  const text = ((LANGS[lang()] ?? en)[key] as string | undefined) ?? String(key); // an unknown key shows as it is
  return text.replace(/\{(\w+)\}/g, (m, k: string) => (vars && k in vars ? String(vars[k]) : m));
}

// the key of the URL `fm panel` printed (the server also set it as a cookie)
const key = (): string => new URLSearchParams(location.search).get("key") ?? "";
export const withKey = (path: string): string =>
  key() ? path + (path.includes("?") ? "&" : "?") + "key=" + encodeURIComponent(key()) : path;

// a write of the panel: the answer's `error` is the message
export async function post<T = { ok: boolean }>(path: string, body?: string): Promise<T> {
  const init: RequestInit = { method: "POST" };
  if (body !== undefined) init.body = body;
  const r = await fetch(withKey(path), init);
  const j = ((await r.json().catch(() => null)) ?? {}) as { error?: string };
  if (!r.ok) throw new Error(j.error ?? String(r.status));
  return j as T;
}

export const enc = encodeURIComponent;
export const usd2 = (x: number): string => "$" + x.toFixed(2);

const pad = (n: number): string => String(n).padStart(2, "0");
const iso = (d: Date): string => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
// «hoy 15:20», «ayer 09:02» or «5 nov 12:00» of a stamp «YYYY-MM-DD HH:MM:SS»
export function when(s: string | null | undefined): string {
  if (!s) return "";
  const day = s.slice(0, 10);
  const y = new Date();
  y.setDate(y.getDate() - 1);
  const d = new Date(day + "T12:00:00");
  const word = day === iso(new Date()) ? t("today") : day === iso(y) ? t("yesterday")
    : `${d.getDate()} ${new Intl.DateTimeFormat(lang(), { month: "short" }).format(d).replace(/\.$/, "")}`;
  return s.length > 10 ? `${word} ${s.slice(11, 16)}` : word;
}

// ---------- the API (fm/panel.py) ----------

export interface Loop {
  time: string | null;
  alive: boolean;
}
export interface Project {
  name: string;
  paused?: string | null;
  loop?: Loop;
  needs?: number;
  running?: number;
  broken?: string;
}
export interface Projects {
  projects: Project[];
}
export interface Agent {
  name: string;
  short?: string;
  session?: string | null;
  kind?: "planning" | "responsible";
  model?: string;
  started?: string;
}
export interface Check {
  tests: string;
  files: number;
  lines: number;
  paths?: string[];
  risk: "low" | "medium" | "high";
  reasons: string[];
  at?: string;
}
export interface Task {
  id: string;
  title: string;
  status: string;
  detail?: string;
  priority?: string;
  blocked?: string;
  created?: string;
  closed?: string;
  acceptance?: { goal?: string; criteria?: string; verdict?: string };
  agent?: Agent;
  agent_state?: string | null; // working | done | stopped | gone
  description?: string;
  report?: { summary?: string; review?: string; confused?: string; at?: string };
  check?: Check;
  branch?: string;
  rework?: number;
  nudges?: number;
  result?: string;
  commit?: string;
  source?: string;
}
export interface Question {
  id: string;
  task: string;
  agent?: string;
  title: string;
  text: string;
  kind: "question" | "permission" | "rejection";
  action?: string;
  asked?: string;
  date?: string;
  answer?: string;
  allowed?: boolean;
}
export interface Event {
  time: string;
  task: string | null;
  text: string;
}
export interface State {
  name: string;
  paused: string | null;
  loop: Loop;
  tasks: Task[];
  questions: Question[];
  events: Event[];
}
export interface TaskAnswer {
  task: Task;
  events: Event[];
  questions: Question[];
  cost: { usd: number; minutes: number };
}
export interface Finding {
  key: string;
  title: string;
  detail: string;
}
export interface MetricRow {
  id: string;
  title: string;
  status: string;
  usd: number;
  to_review_min: number | null;
  to_done_min: number | null;
  rework: number;
  nudges: number;
  questions: number;
  guard_stops: number;
  risk: string | null;
  confused: string;
}
export interface SystemAnswer {
  name: string;
  paused: string | null;
  loop: Loop;
  findings: Finding[];
  metrics: MetricRow[];
  total: number;
  locks: LockStatus[];
}
export interface LockUser {
  who: string;
  task: string;
  what: string;
  since: string;
}
export interface LockStatus {
  name: string;
  slots: string[];
  held: Record<string, LockUser>;
  queue: LockUser[];
}

export const stateUrl = (p: string): string => `/api/state?p=${enc(p)}`;
export const blocked = (i: Task): boolean => !!i.blocked && !["done", "canceled", "duplicate"].includes(i.status);
