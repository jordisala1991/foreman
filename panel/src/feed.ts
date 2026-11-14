// The one place the panel reads its API from while a screen is open. Each path is a feed: one answer kept, one timer
// shared by everyone who watches it (the shell's counts, a screen and its sub-screens ask the same /api/state once per
// turn), and a new answer replaces the old one only when its text differs. An answer equal to the last one changes
// nothing, so nothing redraws; one that differs, or a failure, replaces the snapshot, and a refresh never empties it:
// what is on screen stays until the new answer is there. (This check is on the page's side, where the cost was: a new
// object per poll.)
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { withKey } from "./lib";

export interface Snap<T> {
  data: T | undefined;
  failed: boolean; // the last read failed (`data` is the last good answer, if any)
  since: Date | null; // when `data` was read, only while `failed`
}

interface Entry {
  snap: Snap<unknown>;
  text: string | undefined;
  lastOk: Date | null;
  readAt: number; // ms of the last good read
  inflight: number;
  seq: number;
  subs: Map<() => void, number>; // listener -> the period it asked (0: no polling)
  timer: ReturnType<typeof setInterval> | undefined;
  period: number;
}

const EMPTY: Snap<never> = { data: undefined, failed: false, since: null };
const entries = new Map<string, Entry>();

function entry(path: string): Entry {
  let e = entries.get(path);
  if (!e) {
    e = { snap: EMPTY, text: undefined, lastOk: null, readAt: 0, inflight: 0, seq: 0, subs: new Map(), timer: undefined, period: 0 };
    entries.set(path, e);
  }
  return e;
}

function publish(e: Entry, snap: Snap<unknown>): void {
  e.snap = snap;
  [...e.subs.keys()].forEach((f) => f());
}

// Reads `path` now; resolves with the answer. A newer read supersedes an older one still on its way.
export async function refresh<T = unknown>(path: string): Promise<T> {
  const e = entry(path);
  const mine = ++e.seq;
  e.inflight++;
  try {
    const r = await fetch(withKey(path), { cache: "no-cache" });
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    const text = await r.text();
    if (mine !== e.seq) return (e.snap.data ?? JSON.parse(text)) as T;
    e.lastOk = new Date();
    e.readAt = Date.now();
    if (text !== e.text || e.snap.failed) {
      e.text = text;
      publish(e, { data: JSON.parse(text), failed: false, since: null });
    }
    return e.snap.data as T;
  } catch (err) {
    if (mine === e.seq && !e.snap.failed) publish(e, { data: e.snap.data, failed: true, since: e.lastOk });
    throw err;
  } finally {
    e.inflight--;
  }
}

function retime(path: string, e: Entry): void {
  const periods = [...e.subs.values()].filter((p) => p > 0);
  const period = periods.length ? Math.min(...periods) : 0;
  if (period === e.period && (e.timer !== undefined) === (period > 0)) return;
  if (e.timer !== undefined) clearInterval(e.timer);
  e.period = period;
  e.timer =
    period > 0
      ? setInterval(() => {
          if (document.visibilityState !== "hidden") refresh(path).catch(() => undefined);
        }, period)
      : undefined;
}

function watch(paths: string[], every: number, on: () => void): () => void {
  const subscribed = paths.map((p): [string, Entry] => [p, entry(p)]);
  for (const [p, e] of subscribed) {
    e.subs.set(on, every);
    retime(p, e);
    // what is cached shows at once; the answer replaces it only if it differs. A read that is on its way, or one that
    // finished a second ago (the shell and its screen asking for the same path), is not asked again
    if (e.inflight === 0 && Date.now() - e.readAt > 1000) refresh(p).catch(() => undefined);
  }
  return () => {
    for (const [p, e] of subscribed) {
      e.subs.delete(on);
      retime(p, e);
    }
  };
}

// The snapshots of several paths (null: nothing to read); the array and each snapshot keep their identity until
// something in them changes. `every`: the period of the shared timer, 0 for reading once on mount.
export function useFeeds<T>(paths: (string | null)[], every = 0): Snap<T>[] {
  const key = paths.join("\n");
  const last = useRef<Snap<T>[]>([]);
  const live = paths.filter((p): p is string => p !== null);
  const subscribe = useCallback((on: () => void) => watch(live, every, on), [key, every]); // eslint-disable-line react-hooks/exhaustive-deps
  const snapshot = useCallback((): Snap<T>[] => {
    const now = paths.map((p) => (p === null ? EMPTY : entry(p).snap) as Snap<T>);
    const prev = last.current;
    if (prev.length === now.length && prev.every((s, i) => s === now[i])) return prev;
    last.current = now;
    return now;
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return useSyncExternalStore(subscribe, snapshot);
}

// One part of a feed's answer (what the shell needs of /api/projects, while a screen of the system reads the machine's
// load from the same answer): the part keeps its identity until it differs, so a change elsewhere in the answer
// does not redraw the one that asked.
export function useFeedPart<T, S>(path: string, every: number, select: (d: T) => S): S | undefined {
  const last = useRef<{ from: unknown; part: S | undefined; text: string }>({ from: undefined, part: undefined, text: "" });
  const subscribe = useCallback((on: () => void) => watch([path], every, on), [path, every]);
  const snapshot = useCallback((): S | undefined => {
    const from = entry(path).snap.data;
    const l = last.current;
    if (from === l.from) return l.part;
    const part = from === undefined ? undefined : select(from as T);
    const text = JSON.stringify(part) ?? "";
    last.current = { from, part: text === l.text ? l.part : part, text };
    return last.current.part;
  }, [path]); // eslint-disable-line react-hooks/exhaustive-deps
  return useSyncExternalStore(subscribe, snapshot);
}

export interface Feed<T> extends Snap<T> {
  reload: () => void;
}

export function useFeed<T>(path: string | null, every = 0): Feed<T> {
  const [snap] = useFeeds<T>([path], every);
  const reload = useCallback(() => {
    if (path !== null) refresh(path).catch(() => undefined);
  }, [path]);
  return { ...(snap ?? EMPTY), reload };
}

// Reads every path again now (after a write)
export function reloadAll(paths: string[]): void {
  paths.forEach((p) => refresh(p).catch(() => undefined));
}

// The time, in seconds, for the durations on screen: it moves on when the minute turns, and a tick inside the same
// minute keeps the same value, so nothing redraws.
export function useNow(): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  const shown = useRef(now);
  useEffect(() => {
    const id = setInterval(() => {
      const t = Date.now() / 1000;
      if (Math.floor(t / 60) === Math.floor(shown.current / 60)) return; // no setState: not even a render
      shown.current = t;
      setNow(t);
    }, 15_000);
    return () => clearInterval(id);
  }, []);
  return now;
}
