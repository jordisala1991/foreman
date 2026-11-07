// What the «More» screens share: loading an answer of the panel's API (again when asked, or every `every` ms), the
// writes (POST with the key), and the formats of ages.
import { useFeed } from "../feed";
import { t, withKey } from "../lib";

export interface Loaded<T> {
  data: T | undefined;
  failed: boolean;
  reload: () => void;
}

// `path` null: nothing to load. The answer is kept per path (feed.ts): a new path shows its own data, or none until it
// comes (never the old screen's); asking again every `every` ms replaces it only when the answer differs.
export function useApi<T>(path: string | null, every = 0): Loaded<T> {
  const f = useFeed<T>(path, every);
  return { data: f.data, failed: f.failed, reload: f.reload };
}

export async function post(path: string): Promise<{ ok: boolean; error: string }> {
  try {
    const r = await fetch(withKey(path), { method: "POST" });
    const j = (await r.json().catch(() => ({}))) as { error?: string };
    return { ok: r.ok, error: j.error ?? (r.ok ? "" : String(r.status)) };
  } catch {
    return { ok: false, error: "no connection" };
  }
}

export const q = encodeURIComponent;

// «hace 5 min», «hace 3 h», «ayer», «hace 4 días» from a "YYYY-MM-DD[ HH:MM]" (local time) or an epoch in seconds
export function ago(when: string | number | null | undefined, now = Date.now()): string {
  if (when == null || when === "") return t("sys_never");
  const ms = typeof when === "number" ? when * 1000 : new Date(when.length <= 10 ? when + "T12:00:00" : when.replace(" ", "T")).getTime();
  if (Number.isNaN(ms)) return String(when);
  const min = Math.max(0, Math.round((now - ms) / 60000));
  if (min < 1) return t("ago_now");
  if (min < 60) return t("ago_min", { n: min });
  if (min < 60 * 24 && typeof when !== "string") return t("ago_hour", { n: Math.round(min / 60) });
  const days = Math.floor((new Date(now).setHours(0, 0, 0, 0) - new Date(ms).setHours(0, 0, 0, 0)) / 86400000);
  if (days <= 0) return t("ago_hour", { n: Math.max(1, Math.round(min / 60)) });
  return days === 1 ? t("ago_yesterday") : t("ago_days", { n: days });
}

// the hash after the screen's name, as parts: "#/backlog/AB-1" -> ["AB-1"]
export function rest(): string[] {
  return location.hash
    .replace(/^#\/?/, "")
    .split("/")
    .slice(1)
    .filter(Boolean)
    .map((x) => decodeURIComponent(x));
}
export const go = (...parts: string[]): string => "#/" + parts.map(encodeURIComponent).join("/");
