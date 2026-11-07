// A document of the home (a plan, a report, a review) read in the panel: /api/doc, as Markdown. Opens over the
// screen it was linked from; «Volver» goes back.
import type { ReactNode } from "react";
import { t } from "../lib";
import { go, q, useApi } from "./api";
import { render, type Ctx } from "./md";
import { Failed, Loading } from "./parts";
import type { MoreProduct } from "./types";

export const docCtx = (wiki: Ctx["wiki"]): Ctx => ({
  wiki,
  doc: (path: string, label: ReactNode) => <a href={go("backlog", "doc", path)}>{label}</a>,
});
export const plainWiki: Ctx["wiki"] = (n, _a, label) => <a href={go("wiki", "page", n)}>{label}</a>;

export function Doc({ product, path }: { product: MoreProduct; path: string }) {
  const d = useApi<{ path: string; md: string }>(`/api/doc?p=${q(product.name)}&path=${q(path)}`);
  return (
    <div className="screen">
      <div className="crumbs">
        <button type="button" className="link back" onClick={() => history.back()}>
          {t("back")}
        </button>
      </div>
      {d.failed ? (
        <div className="card quiet">
          {t("doc_missing")} <Failed retry={d.reload} />
        </div>
      ) : !d.data ? (
        <Loading />
      ) : (
        <article className="md card">{render(d.data.md, docCtx(plainWiki)).nodes}</article>
      )}
    </div>
  );
}
