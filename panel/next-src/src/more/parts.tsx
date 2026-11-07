// Small pieces the «More» screens share.
import type { ReactNode } from "react";
import { t } from "../lib";

export const Loading = () => <div className="card quiet">{t("loading")}</div>;

export function Failed({ retry }: { retry: () => void }) {
  return (
    <div className="card quiet failed">
      {t("retry_load")}{" "}
      <button type="button" className="link" onClick={retry}>
        {t("retry")}
      </button>
    </div>
  );
}

// «‹ Backlog / E1 / FO-197» on a wide screen, «‹ Backlog» (the last step but one) on the phone
export function Crumbs({ steps, narrow }: { steps: { label: string; href?: string }[]; narrow?: { label: string; href: string } }) {
  const back = narrow ?? (steps.length > 1 ? steps[steps.length - 2] : undefined);
  return (
    <div className="crumbs">
      <span className="crumb-wide">
        <span aria-hidden="true">‹ </span>
        {steps.map((s, i) => (
          <span key={i}>
            {i > 0 ? <span className="sep"> / </span> : null}
            {s.href && i < steps.length - 1 ? <a href={s.href}>{s.label}</a> : <span className="here">{s.label}</span>}
          </span>
        ))}
      </span>
      {back?.href ? (
        <a className="crumb-narrow" href={back.href}>
          ‹ {back.label}
        </a>
      ) : null}
    </div>
  );
}

export const Section = ({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) => (
  <section className="sec">
    <div className="sechead">
      <h2>{title}</h2>
      {aside}
    </div>
    {children}
  </section>
);

// a bar split in coloured parts: [{n, cls}]
export function Split({ parts, label }: { parts: { n: number; cls: string }[]; label?: string }) {
  const total = parts.reduce((a, p) => a + p.n, 0) || 1;
  return (
    <div className="split" role="img" aria-label={label}>
      {parts
        .filter((p) => p.n > 0)
        .map((p, i) => (
          <i key={i} className={p.cls} style={{ width: `${(100 * p.n) / total}%` }} />
        ))}
    </div>
  );
}
