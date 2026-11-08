// «Needs you»: what waits for the owner. Permissions and questions (each tied to its task and the agent that asked;
// the answer goes back to that agent), the integration queue (accept or send back), triage («Go» or cancel) and the
// blocked tasks (unblock).
import type { ReactNode } from "react";
import { BlockedCard, LoadFailed, PermissionCard, QueueCard, QuestionCard, TriageCard } from "./cards";
import { useFeed } from "./feed";
import { ScreenHead } from "./head";
import { Small } from "./icons";
import { blocked, stateUrl, t, type Key, type Product, type State } from "./lib";

const Part = ({ title, n, children }: { title: Key; n: number; children: ReactNode }) =>
  n ? (
    <section aria-label={t(title)}>
      <h2>
        {t(title)}
        <span className="count">{n}</span>
      </h2>
      {children}
    </section>
  ) : null;

export function Needs({ product }: { product: Product }) {
  const s = useFeed<State>(stateUrl(product.name), 10_000);
  const d = s.data;
  const p = product.name;
  const perms = (d?.questions ?? []).filter((q) => q.kind === "permission");
  const questions = (d?.questions ?? []).filter((q) => q.kind !== "permission");
  const queue = (d?.issues ?? []).filter((i) => i.status === "in review");
  const triage = (d?.issues ?? []).filter((i) => i.status === "triage");
  const stuck = (d?.issues ?? []).filter((i) => blocked(i) && i.status !== "triage" && i.status !== "in review");
  const total = perms.length + questions.length + queue.length + triage.length + stuck.length;
  return (
    <div className="screen needs">
      <ScreenHead title={t("tab_needs")} product={product} sub={d && total ? t("n_waiting", { n: total }) : ""} />
      {!d && !s.failed ? <div className="card quiet">{t("loading")}</div> : null}
      {s.failed && !d ? <LoadFailed retry={s.reload} /> : null}
      {d && !total ? (
        <div className="card empty big">
          <span className="round">
            <Small name="check" size={24} />
          </span>
          <div className="title">{t("nothing_waits")}</div>
          <div className="sub">{t("nothing_waits_hint")}</div>
        </div>
      ) : null}
      <div className="needs-grid">
        <div className="col">
          <Part title="permissions" n={perms.length}>
            {perms.map((q) => <PermissionCard key={q.id} q={q} p={p} reload={s.reload} />)}
          </Part>
          <Part title="questions" n={questions.length}>
            {questions.map((q) => <QuestionCard key={q.id} q={q} p={p} reload={s.reload} />)}
          </Part>
          <Part title="g_blocked" n={stuck.length}>
            {stuck.map((i) => <BlockedCard key={i.id} i={i} p={p} reload={s.reload} />)}
          </Part>
        </div>
        <div className="col">
          <Part title="g_in_review" n={queue.length}>
            {queue.map((i) => <QueueCard key={i.id} i={i} p={p} reload={s.reload} />)}
          </Part>
          <Part title="g_triage" n={triage.length}>
            {triage.map((i) => <TriageCard key={i.id} i={i} p={p} reload={s.reload} />)}
          </Part>
        </div>
      </div>
    </div>
  );
}
