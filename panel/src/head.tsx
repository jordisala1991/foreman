// What every screen's header shows: its title, its controls, and on a wide screen the project's status pill (on the
// phone the pill sits by the picker, in the shell). The pill is the loop's: running, paused, or not running.
import type { ReactNode } from "react";
import { t, type Key, type Project } from "./lib";

export type LoopState = "running" | "paused" | "silent";
export function loopState(p: Project): LoopState {
  if (p.paused) return "paused";
  return p.loop?.alive ? "running" : "silent";
}

export function StatusPill({ project }: { project: Project }) {
  const s = loopState(project);
  return (
    <span className={`pill ${s}`} title={t(`state_${s}_tip` as Key)}>
      <span className="dot" />
      {t(`state_${s}` as Key)}
    </span>
  );
}

export function ScreenHead({ title, project, sub, children }: {
  title: string; project: Project; sub?: string; children?: ReactNode;
}) {
  return (
    <div className="screenhead">
      <div className="titles">
        <h1>{title}</h1>
        {sub ? <div className="sub">{sub}</div> : null}
        {children ? <div className="controls">{children}</div> : null}
      </div>
      <span className="wide-only">
        <StatusPill project={project} />
      </span>
    </div>
  );
}
