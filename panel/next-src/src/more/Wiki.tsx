// Wiki in the new panel: the front page (search always in view, what changed lately, the pages to start with, the
// sections with how their pages stand), a page (reading width, its index on the side or in a sticky button, its
// sources and links) and the search in the same tab. A product without a wiki says so and leads to one that has it.
import { useEffect, useMemo, useRef, useState } from "react";
import { ScreenHead } from "../head";
import { t, type Key } from "../lib";
import { Doc, docCtx } from "./Doc";
import { ago, go, q, rest, useApi } from "./api";
import { render, type Ctx } from "./md";
import { Failed, Loading, Section, Split } from "./parts";
import type { MoreProduct, WikiHit, WikiPage, WikiPageFull } from "./types";

const STATUSES = ["activo", "en-prueba", "referencia", "descartado"] as const;
const STATUS_CLS: Record<string, string> = { activo: "green", "en-prueba": "amber", referencia: "accent", descartado: "grey" };
const TYPE_ORDER = ["overview", "model", "engine", "concept", "comparison", "howto"];

const statusKey = (s: string | null | undefined): string => (s && (STATUSES as readonly string[]).includes(s) ? s : "other");
const statusLabel = (s: string | null | undefined): string => t(`wiki_s_${statusKey(s)}` as Key);
export const typeLabel = (type: string): string => {
  const k = `wiki_type_${type}` as Key;
  try {
    const v = t(k);
    return v === `{${k}}` || v === "" ? type : v;
  } catch {
    return type;
  }
};

function NoWiki({ product, products, pick }: { product: MoreProduct; products: MoreProduct[]; pick: (name: string) => void }) {
  const other = products.find((p) => p.wiki && p.name !== product.name);
  return (
    <div className="screen">
      <ScreenHead title={t("wiki_title")} product={product} />
      <div className="card nowiki">
        <h2>{t("wiki_none_title", { name: product.title })}</h2>
        <p>{t("wiki_none_what")}</p>
        {other ? (
          <button type="button" className="btn primary" onClick={() => pick(other.name)}>
            {t("wiki_see_other", { name: other.title })}
          </button>
        ) : null}
      </div>
    </div>
  );
}

function Search({ product, query }: { product: MoreProduct; query: string }) {
  const r = useApi<{ results: WikiHit[] }>(query.trim().length >= 2 ? `/api/wiki/search?p=${q(product.name)}&q=${q(query.trim())}` : null);
  if (query.trim().length < 2) return null;
  if (!r.data) return r.failed ? <Failed retry={r.reload} /> : <div className="card quiet">{t("wiki_searching")}</div>;
  const hits = r.data.results;
  const titles = hits.filter((h) => h.in_title > 0);
  const texts = hits.filter((h) => h.in_title === 0);
  if (!hits.length) return <div className="card quiet">{t("wiki_nothing")}</div>;
  return (
    <>
      {titles.length ? (
        <Section title={t("wiki_pages")}>
          <div className="card list plain">
            {titles.map((h) => (
              <a key={h.n} className="hit" href={go("wiki", "page", h.n)}>
                <span className="htitle">{h.title}</span>
                <span className="hmeta">{typeLabel(h.type)}{h.status ? ` · ${statusLabel(h.status)}` : ""}</span>
              </a>
            ))}
          </div>
        </Section>
      ) : null}
      {texts.length ? (
        <Section title={t("wiki_in_text")}>
          <div className="card list plain">
            {texts.map((h) => (
              <a key={h.n} className="hit" href={go("wiki", "page", h.n)}>
                <span className="htitle">{h.title}</span>
                <span className="hexcerpt">{marked(h.excerpt, query)}</span>
                <span className="hmeta">{typeLabel(h.type)} · {t(h.hits === 1 ? "wiki_time" : "wiki_times", { n: h.hits })}</span>
              </a>
            ))}
          </div>
        </Section>
      ) : null}
    </>
  );
}

// the excerpt with the searched words marked
function marked(text: string, query: string) {
  const words = query.trim().split(/\s+/).filter((w) => w.length > 1).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!words.length) return text;
  // accents and case are ignored by the search: the mark ignores case, and an accented letter matches its plain one
  const loose = words.map((w) => w.replace(/[aeiounc]/gi, (ch) => ({ a: "[aáàä]", e: "[eéèë]", i: "[iíìï]", o: "[oóòö]", u: "[uúùü]", n: "[nñ]", c: "[cç]" })[ch.toLowerCase()] ?? ch));
  return text.split(new RegExp(`(${loose.join("|")})`, "i")).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part));
}

function Front({ product, pages, query, setQuery }: { product: MoreProduct; pages: WikiPage[]; query: string; setQuery: (s: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [openSection, setOpenSection] = useState<string | null>(null);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        input.current?.focus();
      }
    };
    addEventListener("keydown", key);
    return () => removeEventListener("keydown", key);
  }, []);
  const recent = useMemo(() => [...pages].filter((p) => p.updated).sort((a, b) => (b.updated ?? "").localeCompare(a.updated ?? "")).slice(0, 5), [pages]);
  const first = product.wiki_first ?? [];
  const start = useMemo(() => {
    const by = new Map(pages.map((p) => [p.n, p]));
    const pinned = first.flatMap((n) => (by.has(n) ? [by.get(n) as WikiPage] : []));
    const general = pages.filter((p) => p.type === "overview" && !first.includes(p.n));
    return [...pinned, ...general];
  }, [pages, first]);
  const types = useMemo(() => {
    const seen = Array.from(new Set(pages.map((p) => p.type)));
    return seen.sort((a, b) => (TYPE_ORDER.indexOf(a) + 1 || 99) - (TYPE_ORDER.indexOf(b) + 1 || 99) || a.localeCompare(b));
  }, [pages]);
  const last = recent[0]?.updated;
  return (
    <div className="screen">
      <ScreenHead title={t("wiki_title")} product={product} />
      <p className="wmeta">{t("wiki_meta", { name: product.title, n: pages.length, when: last ? ago(last) : "—" })}</p>
      <input
        ref={input}
        type="search"
        className="search"
        value={query}
        placeholder={t("wiki_search_ph")}
        aria-label={t("wiki_search_label")}
        onChange={(e) => setQuery(e.target.value)}
      />
      {query.trim().length >= 2 ? (
        <Search product={product} query={query} />
      ) : (
        <>
          {recent.length ? (
            <Section title={t("wiki_recent")}>
              <div className="card list plain">
                {recent.map((p) => (
                  <a key={p.n} className="hit row2" href={go("wiki", "page", p.n)}>
                    <span className="htitle">{p.title}</span>
                    <span className="hmeta">{typeLabel(p.type)} · {ago(p.updated)}</span>
                  </a>
                ))}
              </div>
            </Section>
          ) : null}
          {start.length ? (
            <Section title={t("wiki_start")}>
              <div className="cards">
                {start.map((p) => (
                  <a key={p.n} className="card startcard" href={go("wiki", "page", p.n)}>
                    <span className="htitle">{p.title}</span>
                    <span className="hmeta">{statusKey(p.status) === "other" ? typeLabel(p.type) : statusLabel(p.status)}</span>
                  </a>
                ))}
              </div>
            </Section>
          ) : null}
          <Section title={t("wiki_sections")}>
            <div className="sections">
              {types.map((type) => {
                const mine = pages.filter((p) => p.type === type);
                const count = (s: string) => mine.filter((p) => statusKey(p.status) === s).length;
                const line = STATUSES.filter((s) => s !== "descartado")
                  .map((s) => [count(s), s] as const)
                  .filter(([n]) => n)
                  .map(([n, s]) => `${n} ${statusLabel(s)}`)
                  .join(" · ");
                const open = openSection === type;
                const discarded = mine.filter((p) => p.status === "descartado");
                return (
                  <div key={type} className="card section">
                    <button type="button" className="sechead2" aria-expanded={open} onClick={() => setOpenSection(open ? null : type)}>
                      <span className="stitle">{typeLabel(type)}</span>
                      <span className="gcount">{mine.length}</span>
                      <span className="car" aria-hidden="true">{open ? "▾" : "›"}</span>
                    </button>
                    <div className="sline">{line}</div>
                    <Split parts={[...STATUSES.filter((s) => s !== "descartado").map((s) => ({ n: count(s), cls: STATUS_CLS[s] ?? "grey" })), { n: count("other"), cls: "grey" }]} label={line} />
                    {open ? (
                      <div className="spages">
                        {STATUSES.filter((s) => s !== "descartado")
                          .concat(["other" as never])
                          .map((s) => {
                            const ps = mine.filter((p) => statusKey(p.status) === s);
                            return ps.length ? (
                              <div key={s} className="sgroup">
                                <div className="dlabel">{statusLabel(s)}</div>
                                {ps.map((p) => (
                                  <a key={p.n} className="hit slim" href={go("wiki", "page", p.n)}>{p.title}</a>
                                ))}
                              </div>
                            ) : null;
                          })}
                        {discarded.length ? (
                          <details className="sgroup">
                            <summary>{t("wiki_discarded", { n: discarded.length })}</summary>
                            {discarded.map((p) => (
                              <a key={p.n} className="hit slim" href={go("wiki", "page", p.n)}>{p.title}</a>
                            ))}
                          </details>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </Section>
        </>
      )}
    </div>
  );
}

function PageView({ product, name, pages }: { product: MoreProduct; name: string; pages: WikiPage[] }) {
  const r = useApi<WikiPageFull>(`/api/wiki/page?p=${q(product.name)}&n=${q(name)}`);
  const [current, setCurrent] = useState("");
  const has = useMemo(() => new Set(pages.map((p) => p.n)), [pages]);
  const title = (n: string) => pages.find((p) => p.n === n)?.title ?? n;
  const ctx: Ctx = useMemo(
    () => ({
      ...docCtx((n, _a, label) =>
        has.has(n) ? <a href={go("wiki", "page", n)}>{label === n ? title(n) : label}</a> : <span className="wbroken" title={t("wiki_no_page", { n })}>{label}</span>,
      ),
      doc: (path, label) => {
        const doc = r.data?.docs[path] ?? path;
        return <a href={go("backlog", "doc", doc)}>{label}</a>;
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [has, r.data],
  );
  const out = useMemo(() => (r.data ? render(r.data.md.replace(/^\s*# .*\n/, ""), ctx) : null), [r.data, ctx]);
  useEffect(() => {
    if (!out || typeof IntersectionObserver === "undefined") return;
    const seen = new Set<string>();
    const io = new IntersectionObserver((es) => {
      for (const e of es) (e.isIntersecting ? seen.add(e.target.id) : seen.delete(e.target.id));
      const first = out.toc.find((h) => seen.has(h.id));
      if (first) setCurrent(first.id);
    }, { rootMargin: "0px 0px -70% 0px" });
    out.toc.forEach((h) => {
      const el = document.getElementById(h.id);
      if (el) io.observe(el);
    });
    return () => io.disconnect();
  }, [out]);
  const jump = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
    setCurrent(id);
  };
  if (r.failed)
    return (
      <div className="screen">
        <div className="crumbs"><a href={go("wiki")}>{t("wiki_back")}</a></div>
        <div className="card quiet">{t("wiki_missing")}</div>
      </div>
    );
  if (!r.data || !out) return <div className="screen"><Loading /></div>;
  const p = r.data;
  const links = Array.from(new Set(Array.from(p.md.matchAll(/\[\[([^\]|#]+)/g), (m) => (m[1] ?? "").trim()))).filter((n) => has.has(n) && n !== p.n);
  const where = out.toc.find((h) => h.id === current)?.text ?? out.toc[0]?.text ?? "";
  const toc = (
    <ul className="toc">
      {out.toc.map((h) => (
        <li key={h.id} className={h.n === 3 ? "sub" : ""}>
          <button type="button" aria-current={h.id === current ? "true" : undefined} onClick={() => jump(h.id)}>{h.text}</button>
        </li>
      ))}
    </ul>
  );
  return (
    <div className="screen wikipage">
      <div className="crumbs">
        <span className="crumb-wide">
          <span aria-hidden="true">‹ </span><a href={go("wiki")}>{t("wiki_title")}</a>
          <span className="sep"> / </span><span>{typeLabel(p.type)}</span>
          <span className="sep"> / </span><span className="here">{p.n}</span>
        </span>
        <a className="crumb-narrow" href={go("wiki")}>{t("wiki_back")}</a>
      </div>
      {out.toc.length ? (
        <details className="tocbtn">
          <summary><span className="lbl">{t("wiki_on_page")}</span> <span className="where">{where}</span></summary>
          {toc}
        </details>
      ) : null}
      <div className="twocol">
        <article className="main reading">
          <header>
            <h1>{p.title}</h1>
            <div className="wprops">
              {p.status ? <span className={`wstatus ${STATUS_CLS[statusKey(p.status)] ?? "grey"}`}>{statusLabel(p.status)}</span> : null}
              <span>{typeLabel(p.type)}</span>
              {p.updated ? <span>{t("wiki_updated", { when: ago(p.updated) })}</span> : null}
            </div>
            {p.tags?.length ? <div className="tags2">{p.tags.map((x) => <span key={x}>{x}</span>)}</div> : null}
          </header>
          <div className="md">{out.nodes}</div>
        </article>
        <aside className="datos stickytoc">
          {out.toc.length ? (<><h2>{t("wiki_on_page")}</h2>{toc}</>) : null}
          {p.sources.length ? (
            <>
              <h2>{t("wiki_sources")}</h2>
              <ul className="plainlist">
                {p.sources.map((s) => <li key={s}>{p.docs[s] ? <a href={go("backlog", "doc", p.docs[s] ?? s)}>{s}</a> : <code>{s}</code>}</li>)}
              </ul>
            </>
          ) : null}
          {links.length ? (
            <>
              <h2>{t("wiki_links")}</h2>
              <ul className="plainlist">
                {links.map((n) => <li key={n}><a href={go("wiki", "page", n)}>{title(n)}</a></li>)}
              </ul>
            </>
          ) : null}
        </aside>
      </div>
    </div>
  );
}

export function Wiki({ product, products, pick }: { product: MoreProduct; products: MoreProduct[]; pick: (name: string) => void }) {
  const parts = rest();
  const [query, setQuery] = useState("");
  const list = useApi<{ pages: WikiPage[] }>(product.wiki ? `/api/wiki?p=${q(product.name)}` : null, 60000);
  if (parts[0] === "doc") return <Doc product={product} path={parts.slice(1).join("/")} />;
  if (!product.wiki) return <NoWiki product={product} products={products} pick={pick} />;
  if (!list.data)
    return (
      <div className="screen">
        <ScreenHead title={t("wiki_title")} product={product} />
        {list.failed ? <Failed retry={list.reload} /> : <Loading />}
      </div>
    );
  if (parts[0] === "page" && parts[1]) return <PageView key={parts[1]} product={product} name={parts[1]} pages={list.data.pages} />;
  return <Front product={product} pages={list.data.pages} query={query} setQuery={setQuery} />;
}
