// The rules the guard refused most in the period (G7's list): the band in words, then the rule as written.
import { t, type Key, type Metrics } from "../lib";

const BANDS = ["never", "builtin", "orchestrator", "turn", "file"];
const band = (b: string): string => (BANDS.includes(b) ? t(`band_${b}` as Key) : b);
// a pattern as a person reads it: the regex's leading «(^|\s)» and its backslashes go, and «file:state» is «state»
const plain = (rule: string): string =>
  rule.replace(/^\(\^\|\\s\)/, "").replace(/^file:/, "").replace(/\\s\+?/g, " ").replace(/\\/g, "");

export function Rules({ m }: { m: Metrics }) {
  if (!m.guard_rules.length) return null;
  return (
    <div className="rules">
      <div className="sub-title">{t("g7_rules")}</div>
      <ol>
        {m.guard_rules.map((r) => (
          <li key={`${r.band}:${r.rule}`}>
            {band(r.band)} · <code>{plain(r.rule)}</code> <span className="n">· {r.n}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
