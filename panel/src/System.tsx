// The system watching itself: the loop's heartbeat and the pause switch, the health findings (each new one is filed
// as a triage task), and per task what it cost and how it went (fm.metrics.per_task), with the total.
import { LoadFailed, Risk, useWrite } from "./cards";
import { reloadAll, useFeed } from "./feed";
import { ScreenHead } from "./head";
import { enc, t, usd2, when, type Key, type Project, type SystemAnswer } from "./lib";

const n0 = (x: number | null): string => (x === null ? "—" : String(Math.round(x)));

export function System({ project }: { project: Project }) {
  const path = `/api/system?p=${enc(project.name)}`;
  const s = useFeed<SystemAnswer>(path, 30_000);
  const w = useWrite(() => reloadAll([path, "/api/projects"]));
  const d = s.data;
  const misled = (d?.metrics ?? []).filter((r) => r.confused && !["none", "nothing", "-"].includes(r.confused.trim().toLowerCase()));
  return (
    <div className="screen system">
      <ScreenHead title={t("tab_system")} project={project} />
      {!d && !s.failed ? <div className="card quiet">{t("loading")}</div> : null}
      {s.failed && !d ? <LoadFailed retry={s.reload} /> : null}
      {d ? (
        <>
          <section className="sec">
            <h2 className="plain">{t("sys_loop")}</h2>
            <div className="card loopcard">
              <div>
                <div className="title">{t(d.loop.alive ? "loop_alive" : "loop_dead")}</div>
                <div className="sub">{d.loop.time ? t("loop_last", { when: when(d.loop.time) }) : t("loop_never")}</div>
                {d.paused ? <div className="sub warn">{t("paused_since", { when: when(d.paused) })}</div> : null}
              </div>
              <button type="button" className={d.paused ? "btn primary small-btn" : "btn small-btn"} disabled={w.busy}
                onClick={() => void w.run(`/api/pause?p=${enc(project.name)}&on=${d.paused ? "0" : "1"}`)}>
                {t(d.paused ? "resume" : "pause")}
              </button>
            </div>
            <p className="note">{t("pause_hint")}</p>
            {w.error ? <p className="error" role="alert">{w.error}</p> : null}
          </section>
          {d.locks.length ? (
            <section className="sec">
              <h2 className="plain">{t("sys_locks")}</h2>
              {d.locks.map((l) => (
                <article key={l.name} className="card">
                  <h3>
                    {l.name} <span className="count">{t(Object.keys(l.held).length ? "lock_busy" : "lock_free")}</span>
                  </h3>
                  {Object.entries(l.held).map(([slot, h]) => (
                    <p key={slot}>
                      {l.slots.length > 1 ? `${slot}: ` : ""}
                      <strong>{h.who}</strong> {h.task ? `(${h.task}) ` : ""}· {h.what} · {t("lock_since", { when: h.since.slice(11, 16) })}
                    </p>
                  ))}
                  {l.queue.map((q, n) => (
                    <p key={n} className="note">
                      {t("lock_waiting", { who: q.who, what: q.what })}
                    </p>
                  ))}
                </article>
              ))}
            </section>
          ) : null}
          <section className="sec">
            <h2 className="plain">{t("sys_health")}</h2>
            {d.findings.length ? (
              d.findings.map((f) => (
                <article key={f.key} className="card finding">
                  <h3>{f.title}</h3>
                  <p className="pre clamp3">{f.detail}</p>
                </article>
              ))
            ) : (
              <div className="card quiet">{t("health_ok")}</div>
            )}
          </section>
          <section className="sec">
            <h2 className="plain">
              {t("sys_metrics")} <span className="count">{t("total_cost", { usd: usd2(d.total) })}</span>
            </h2>
            <div className="card tablewrap">
              <table className="metrics">
                <thead>
                  <tr>
                    <th>{t("m_task")}</th><th>{t("m_status")}</th><th className="num">$</th><th className="num">{t("m_min")}</th>
                    <th className="num">{t("m_rework")}</th><th className="num">{t("m_nudges")}</th><th className="num">{t("m_asks")}</th>
                    <th className="num">{t("m_stops")}</th><th>{t("m_risk")}</th>
                  </tr>
                </thead>
                <tbody>
                  {d.metrics.map((r) => (
                    <tr key={r.id}>
                      <td><a href={`#/task/${r.id}`} className="mono">{r.id}</a> <span className="tt">{r.title}</span></td>
                      <td>{t(`st_${r.status.replace(" ", "_")}` as Key)}</td>
                      <td className="num">{usd2(r.usd)}</td>
                      <td className="num">{n0(r.to_done_min ?? r.to_review_min)}</td>
                      <td className="num">{r.rework}</td>
                      <td className="num">{r.nudges}</td>
                      <td className="num">{r.questions}</td>
                      <td className="num">{r.guard_stops}</td>
                      <td>{r.risk ? <Risk risk={r.risk} /> : null}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="note">{t("cost_note")}</p>
          </section>
          {misled.length ? (
            <section className="sec">
              <h2 className="plain">{t("sys_misled")}</h2>
              <div className="card journal">
                {misled.map((r) => (
                  <div key={r.id} className="entry">
                    <div className="who">{r.id} · {r.title}</div>
                    <p className="pre">{r.confused}</p>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
