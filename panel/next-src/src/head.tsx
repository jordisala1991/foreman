// What every screen's header shows: its title, its controls, and on a wide screen the product's status pill (on the
// phone the pill sits by the picker, in the shell).
import type { ReactNode } from "react";
import { t, type Key, type Product } from "./lib";

// the product's status: running (green), paused or not answering (amber), resting (grey: no orchestrator open)
export type State = "running" | "paused" | "silent" | "resting";
export function productState(p: Product): State {
  if (p.paused) return "paused";
  const v = p.orchestrator_health?.verdict;
  if (v === "ok") return "running";
  if (v === "no_loop" || v === "silent") return "silent";
  return "resting";
}

export function StatusPill({ product, all }: { product: Product; all?: Product[] | undefined }) {
  if (all) {
    const running = all.filter((p) => productState(p) === "running").length;
    const s: State = running === all.length ? "running" : running ? "silent" : "resting";
    return (
      <span className={`pill ${s}`}>
        <span className="dot" />
        {t(running === all.length ? "all_running" : "some_running", { n: running, total: all.length })}
      </span>
    );
  }
  const s = productState(product);
  return (
    <span className={`pill ${s}`} title={t(`state_${s}_tip` as Key)}>
      <span className="dot" />
      {product.pill ?? t(`state_${s}` as Key)}
    </span>
  );
}

export function ScreenHead({ title, product, all, sub, kicker, children }: {
  title: string; product: Product; all?: Product[] | undefined; sub?: string; kicker?: string; children?: ReactNode;
}) {
  return (
    <div className="screenhead">
      <div className="titles">
        {kicker ? <div className="kicker">{kicker}</div> : null}
        <h1>{title}</h1>
        {sub !== undefined ? <div className="sub">{sub}</div> : null}
        {children ? <div className="controls">{children}</div> : null}
      </div>
      <span className="wide-only">
        <StatusPill product={product} all={all} />
      </span>
    </div>
  );
}

// Where a screen's content comes while it is on its way: a block of about the height it will have (no text, so
// nothing to swap), under the screen's own final header
export const Skel = ({ h }: { h: number }) => <div className="card skel" aria-busy="true" style={{ minHeight: h }} />;
