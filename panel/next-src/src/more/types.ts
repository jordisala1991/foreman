// What /api/projects, /api/backlog, /api/issue, /api/state and the wiki's answers say, as far as the «More» screens
// read them (the server's cards are lib/foreman/panel_cards.py; the state, panel.py `project_state`).
import type { Product } from "../lib";

export interface MoreProduct extends Product {
  wiki?: boolean;
  wiki_first?: string[];
  mode?: string;
}

export interface Sub {
  id: string;
  title?: string;
  status?: string;
  commit?: string;
  agent?: string;
  branch?: string;
  report?: string;
  ref?: string;
  doc?: string | null;
  note?: string;
}
export interface DocRef {
  kind: "plan" | "report" | "review" | "decision" | "cited";
  doc: string;
  sub?: string;
  round?: number;
  open?: number;
  id?: string;
  note?: string;
  title?: string;
}
export interface Card {
  id: string;
  project: string;
  title: string;
  short_name?: string;
  status: string;
  blocked?: string;
  why?: string;
  blocked_rel?: string[];
  detail?: string;
  subs: Sub[];
  plan: string[];
  docs?: DocRef[];
  outcome?: { claim?: string; criterion?: string; verdict?: string };
  integrated?: { n: number; of: number; commits: string[] };
  priority?: string;
  repo?: string;
  closed?: string;
  result?: string;
  wanted?: string;
  archived?: boolean;
  short?: boolean;
  stub?: boolean;
}
export interface ProjectCard {
  id: string;
  title: string;
  status: string;
  status_derived?: boolean;
  priority?: string;
  repos?: string[];
  issues: Card[];
}
export interface BacklogAnswer {
  project: {
    name: string;
    backlog: { projects: ProjectCard[]; issues: Card[]; archive: Card[]; counts: Record<string, number>; missing?: boolean };
    backlog_error?: string | null;
  };
}
export interface Live {
  agent?: string;
  agent_status?: string;
  branch?: string;
  production?: boolean;
  status?: string;
  no_agent?: boolean;
}
export interface Move {
  id: number;
  time: string;
  text: string;
  move?: string;
}
export interface ProjectState {
  issue_live: Record<string, Live>;
  targets: { production_slots?: number | null; ready_target?: number | null };
  activity: Move[];
  notes: { time: string; text: string }[];
  progress: { time: string; agent?: string; text: string; p?: string }[];
  recurring: Recurring[];
}
export interface StateAnswer {
  project: ProjectState;
}
export interface Recurring {
  name: string;
  every: string;
  late: boolean;
  inactive?: boolean;
  parts: { label?: string | null; last: string | null; late: boolean }[];
}
export interface IssueAnswer {
  issue: Card;
  cost: { usd: number; runs: number; tokens: number; cheap_share: number } | null;
}

export interface WikiPage {
  n: string;
  title: string;
  section: string;
  type: string;
  status?: string | null;
  tags?: string[];
  updated?: string | null;
}
export interface WikiPageFull extends WikiPage {
  md: string;
  sources: string[];
  docs: Record<string, string>;
}
export interface WikiHit {
  n: string;
  title: string;
  type: string;
  status?: string | null;
  updated?: string | null;
  in_title: number;
  hits: number;
  excerpt: string;
}
