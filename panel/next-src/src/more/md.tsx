// Markdown as the panel shows it, built as React elements (nothing is injected as HTML): headings with anchors,
// nested lists, tables (in their own scroller), fenced code, quotes, rules, bold, italics, strike-through, code,
// [[page]] and [text](url) links. Not covered: raw HTML (shown as text), footnotes, images (their text), setext
// headings and indented code. `ctx` decides what a wiki page link, a document link and a code span that names a
// document become.
import type { ReactNode } from "react";

export interface Ctx {
  wiki: (name: string, anchor: string | undefined, label: ReactNode) => ReactNode;
  doc: (path: string, label: ReactNode) => ReactNode;
}
export interface Heading {
  n: number;
  id: string;
  text: string;
}

export const fold = (s: string): string => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
export const slug = (s: string): string =>
  fold(s)
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "") || "s";

const INLINE = new RegExp(
  [
    "(`+)([\\s\\S]*?[^`])\\1(?!`)", // 1,2 code
    "\\[\\[([^\\]|#]+)(?:#([^\\]|]+))?(?:\\|([^\\]]+))?\\]\\]", // 3,4,5 [[page#anchor|label]]
    "(!?)\\[([^\\]]+)\\]\\(([^)\\s]+)(?:\\s+\"[^\"]*\")?\\)", // 6 image mark, 7 text, 8 target
    "\\*\\*(?=\\S)([\\s\\S]*?\\S)\\*\\*", // 9 bold
    "(?<![\\w*])\\*(?=[^\\s*])([^*]*?[^\\s*])\\*(?![\\w*])", // 10 italic
    "(?<!\\w)_(?=\\S)([^_]*?\\S)_(?!\\w)", // 11 italic
    "~~(?=\\S)([\\s\\S]*?\\S)~~", // 12 strike
    "(https?:\\/\\/[^\\s<>)]+[^\\s<>).,;:])", // 13 bare url
  ].join("|"),
  "g",
);
const MD_FILE = /^[\w./-]+\.md$/;

export function inline(s: string, ctx: Ctx): ReactNode[] {
  const out: ReactNode[] = [];
  let at = 0;
  let k = 0;
  const key = () => `i${k++}`;
  for (const m of s.matchAll(INLINE)) {
    const i = m.index ?? 0;
    if (i > at) out.push(s.slice(at, i));
    at = i + m[0].length;
    if (m[2] !== undefined) {
      const c = m[2].trim();
      out.push(MD_FILE.test(c) ? <span key={key()}>{ctx.doc(c, <code>{c}</code>)}</span> : <code key={key()}>{c}</code>);
    } else if (m[3] !== undefined) {
      const name = m[3].trim();
      out.push(<span key={key()}>{ctx.wiki(name, m[4], m[5] ?? name)}</span>);
    } else if (m[8] !== undefined) {
      const text = m[7] ?? "";
      const url = m[8];
      if (m[6]) out.push(text);
      else if (/^https?:/.test(url))
        out.push(
          <a key={key()} href={url} target="_blank" rel="noopener noreferrer">
            {inline(text, ctx)}
          </a>,
        );
      else if (/\.md(#.*)?$/.test(url))
        out.push(<span key={key()}>{ctx.doc(url.replace(/#.*/, "").replace(/^(\.\.\/)+/, ""), inline(text, ctx))}</span>);
      else out.push(...inline(text, ctx));
    } else if (m[9] !== undefined) out.push(<strong key={key()}>{inline(m[9], ctx)}</strong>);
    else if (m[10] !== undefined || m[11] !== undefined) out.push(<em key={key()}>{inline(m[10] ?? m[11] ?? "", ctx)}</em>);
    else if (m[12] !== undefined) out.push(<s key={key()}>{inline(m[12], ctx)}</s>);
    else if (m[13] !== undefined)
      out.push(
        <a key={key()} href={m[13]} target="_blank" rel="noopener noreferrer">
          {m[13]}
        </a>,
      );
  }
  if (at < s.length) out.push(s.slice(at));
  return out;
}

const LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const HR = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const FENCE = /^\s*(```|~~~)/;
const HEAD = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;

// a table row's cells: split on | outside code spans, and not on \|
function cells(line: string): string[] {
  const t = line.trim().replace(/^\|/, "").replace(/(^|[^\\])\|$/, "$1");
  const out: string[] = [];
  let cur = "";
  let code = false;
  for (let i = 0; i < t.length; i++) {
    const ch = t[i];
    if (ch === "`") code = !code;
    if (ch === "\\" && t[i + 1] === "|") {
      cur += "|";
      i++;
    } else if (ch === "|" && !code) {
      out.push(cur.trim());
      cur = "";
    } else cur += ch;
  }
  out.push(cur.trim());
  return out;
}

export function render(text: string, ctx: Ctx): { nodes: ReactNode[]; toc: Heading[] } {
  const lines = text.replace(/\r/g, "").replace(/\t/g, "    ").split("\n");
  const toc: Heading[] = [];
  const seen: Record<string, number> = {};
  let k = 0;
  const key = () => `b${k++}`;

  function blocks(L: string[]): ReactNode[] {
    const out: ReactNode[] = [];
    let i = 0;
    const starts = (l: string) => HEAD.test(l) || FENCE.test(l) || /^\s*>/.test(l) || LIST.test(l) || HR.test(l) || /^\s*\|/.test(l);
    while (i < L.length) {
      const l = L[i] ?? "";
      if (!l.trim()) {
        i++;
        continue;
      }
      let m: RegExpMatchArray | null;
      if ((m = l.match(FENCE))) {
        const body: string[] = [];
        i++;
        while (i < L.length && !(L[i] ?? "").trim().startsWith(m[1] ?? "```")) body.push(L[i++] ?? "");
        i++;
        out.push(
          <pre key={key()}>
            <code>{body.join("\n")}</code>
          </pre>,
        );
      } else if ((m = l.match(HEAD))) {
        const n = (m[1] ?? "#").length;
        const raw = m[2] ?? "";
        let id = "h-" + slug(raw.replace(/[`*_[\]]/g, ""));
        const times = (seen[id] ?? 0) + 1;
        seen[id] = times;
        if (times > 1) id += "-" + times;
        if (n >= 2 && n <= 3) toc.push({ n, id, text: raw.replace(/[`*_]|\[\[|\]\]/g, "") });
        const Tag = `h${n}` as "h1";
        out.push(
          <Tag key={key()} id={id}>
            {inline(raw, ctx)}
          </Tag>,
        );
        i++;
      } else if (HR.test(l)) {
        out.push(<hr key={key()} />);
        i++;
      } else if (/^\s*\|/.test(l) && TABLE_SEP.test(L[i + 1] ?? "")) {
        const head = cells(l);
        const align = cells(L[i + 1] ?? "").map((c) => (/^:-+:$/.test(c) ? "center" : /-:$/.test(c) ? "right" : "left"));
        i += 2;
        const rows: string[][] = [];
        while (i < L.length && /^\s*\|/.test(L[i] ?? "")) rows.push(cells(L[i++] ?? ""));
        const at = (j: number) => ({ textAlign: align[j] ?? "left" }) as const;
        out.push(
          <div className="tablewrap" key={key()}>
            <table>
              <thead>
                <tr>
                  {head.map((c, j) => (
                    <th key={j} style={at(j)}>
                      {inline(c, ctx)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, a) => (
                  <tr key={a}>
                    {r.map((c, j) => (
                      <td key={j} style={at(j)}>
                        {inline(c, ctx)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>,
        );
      } else if (/^\s*>/.test(l)) {
        const q: string[] = [];
        while (i < L.length && /^\s*>/.test(L[i] ?? "")) q.push((L[i++] ?? "").replace(/^\s*> ?/, ""));
        out.push(<blockquote key={key()}>{blocks(q)}</blockquote>);
      } else if (LIST.test(l)) {
        const [node, next] = list(L, i);
        out.push(node);
        i = next;
      } else {
        const para: string[] = [];
        while (i < L.length && (L[i] ?? "").trim() && !(para.length && starts(L[i] ?? ""))) para.push((L[i++] ?? "").trim());
        out.push(<p key={key()}>{inline(para.join(" "), ctx)}</p>);
      }
    }
    return out;
  }

  // a list (nested by indentation) starting at line i -> [element, the line after it]
  function list(L: string[], from: number): [ReactNode, number] {
    const first = (L[from] ?? "").match(LIST);
    const base = (first?.[1] ?? "").length;
    const ordered = /\d/.test(first?.[2] ?? "");
    const items: ReactNode[] = [];
    let i = from;
    while (i < L.length) {
      const m = (L[i] ?? "").match(LIST);
      if (!m || (m[1] ?? "").length < base) break;
      if ((m[1] ?? "").length > base) {
        const [sub, next] = list(L, i);
        items.push(<li key={key()} className="nest">{sub}</li>);
        i = next;
        continue;
      }
      let text = m[3] ?? "";
      i++;
      while (i < L.length && (L[i] ?? "").trim() && !LIST.test(L[i] ?? "") && /^\s{2,}/.test(L[i] ?? "")) text += " " + (L[i++] ?? "").trim();
      items.push(<li key={key()}>{inline(text, ctx)}</li>);
    }
    const Tag = ordered ? "ol" : "ul";
    return [<Tag key={key()}>{items}</Tag>, i];
  }

  return { nodes: blocks(lines), toc };
}
