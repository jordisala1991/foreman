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

export function StatusPill({ product }: { product: Product }) {
  const s = productState(product);
  return (
    <span className={`pill ${s}`} title={t(`state_${s}_tip` as Key)}>
      <span className="dot" />
      {product.pill ?? t(`state_${s}` as Key)}
    </span>
  );
}

export function ScreenHead({ title, product, children }: { title: string; product: Product; children?: ReactNode }) {
  return (
    <div className="screenhead">
      <div className="titles">
        <h1>{title}</h1>
        {children ? <div className="controls">{children}</div> : null}
      </div>
      <span className="wide-only">
        <StatusPill product={product} />
      </span>
    </div>
  );
}
