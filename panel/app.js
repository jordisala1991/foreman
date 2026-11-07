// foreman panel (served by lib/foreman/panel.py): an earlier in-house panel (2026-10-30), ported to foreman's
// API. Same layout, tabs and components; what is new is the product picker in the header (one product of the registry,
// or all of them together) and the texts, which come from strings.<language>.json (/api/strings). The page polls
// /api/state every 5 s while visible (with its ETag: an unchanged state is a 304 and nothing is repainted but the ages,
// which the state carries as times; the machine's gauges come with /api/projects every 30 s) and paints
// six tabs, each answering one question (nothing is shown twice): Ahora, what is going on and whether the owner is
// needed; Tú, what he decides; Trabajo, what is being worked on; Backlog, what comes next; Sistema, whether everything
// is healthy; Wiki, what we know (a product's wiki folder, read-only, loaded only when opened; only products with
// [panel] wiki). Every list is made of the same components (one-line rows, collapsible groups, property blocks; see
// app.css) and tapping a row opens its detail in the modal, a stack of views with «back».
'use strict';
const T = new URLSearchParams(location.search).get('t') || '';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const lsGet = (k, def) => { try { return localStorage.getItem(k) ?? def; } catch(e) { return def; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch(e) {} };
// the viewer's saved choices under their old (Spanish) keys and values, moved once to the English ones
(() => { try {
  const OLD = {plegados: 'folded', epica: 'project_filter', vista: 'view'}, VAL = {historias: 'issues', epicas: 'projects',
    ahora: 'now', tu: 'you', trabajo: 'work', sistema: 'system'};
  for(const [a, b] of Object.entries(OLD)){ const v = localStorage.getItem(a); if(v != null){ if(localStorage.getItem(b) == null) localStorage.setItem(b, VAL[v] || v); localStorage.removeItem(a); } }
  const tb = localStorage.getItem('tab'); if(VAL[tb]) localStorage.setItem('tab', VAL[tb]);
} catch(e) {} })();
const api = path => `${path}${path.includes('?') ? '&' : '?'}t=${encodeURIComponent(T)}`;

// ---- Texts (strings.<language>.json): t('key', {vars}); tn() picks key_1 or key_n by the number n
let S = {}, LANG = '';
// the few texts an error needs before /api/strings has ever loaded (offline on opening): never a raw key on screen
const FALLBACK = {
  en: {no_connection: 'No connection', loading: 'Loading…', error: 'Error: {text}', wrong_key: 'Wrong key in the link',
    dur_s: '{n} s', dur_min: '{n} min', dur_h: '{h} h {m} min', dur_d: '{n} d',
    offline_stale: 'No connection with the panel: the data may be {dur} old. Retrying…', offline_none: 'No connection with the panel. Retrying…',
    panel_error_stale: 'The panel answers with an error ({text}): the data may be {dur} old. Retrying…', panel_error_none: 'The panel answers with an error ({text}). Retrying…',
    wiki_failed: 'Could not load the wiki: {text}. Retrying on its own.'},
  es: {no_connection: 'Sin conexión', loading: 'Cargando…', error: 'Error: {text}', wrong_key: 'Clave incorrecta en el enlace',
    dur_s: '{n} s', dur_min: '{n} min', dur_h: '{h} h {m} min', dur_d: '{n} d',
    offline_stale: 'Sin conexión con el panel: los datos pueden ser de hace {dur}. Reintentando…', offline_none: 'Sin conexión con el panel. Reintentando…',
    panel_error_stale: 'El panel responde con un error ({text}): los datos pueden ser de hace {dur}. Reintentando…', panel_error_none: 'El panel responde con un error ({text}). Reintentando…',
    wiki_failed: 'No se pudo cargar la wiki: {text}. Se reintenta sola.'}};
const fallbackLang = () => { const l = lsGet('lang', '') || (typeof navigator !== 'undefined' && navigator.language) || 'en'; return /^es/i.test(l) ? 'es' : 'en'; };
function t(key, vars){
  let s = key.split('.').reduce((o, k) => (o || {})[k], S);
  if(s == null) s = FALLBACK[fallbackLang()][key];
  if(s == null) s = key;
  return String(s).replace(/\{(\w+)\}/g, (m, k) => vars && vars[k] != null ? vars[k] : m);
}
const tn = (key, n, vars) => t(`${key}_${n===1 ? '1' : 'n'}`, {n, ...vars});
let SKEY = '';
async function loadStrings(lang){
  if(lang === SKEY) return;
  try{ S = await (await fetch(api(`/api/strings?lang=${encodeURIComponent(lang)}`))).json(); }catch(e){ return; }
  SKEY = lang; LANG = lang; lsSet('lang', lang); document.documentElement.lang = lang; document.title = t('title');
  document.querySelectorAll('[data-s]').forEach(el => el.textContent = t(el.dataset.s));
  document.querySelectorAll('[data-sp]').forEach(el => el.placeholder = t(el.dataset.sp));
  document.querySelectorAll('[data-sa]').forEach(el => el.setAttribute('aria-label', t(el.dataset.sa)));
  lastSignature = null;
}
const dur = s => s == null ? '' : s < 60 ? t('dur_s', {n: s}) : s < 3600 ? t('dur_min', {n: Math.floor(s/60)}) : s < 172800 ? t('dur_h', {h: Math.floor(s/3600), m: Math.floor(s%3600/60)}) : t('dur_d', {n: Math.round(s/86400)});
const ago = h => h==null ? t('never') : h < 1 ? t('ago_min', {n: Math.round(h*60)}) : h < 48 ? t('ago_h', {n: Math.round(h)}) : t('ago_d', {n: Math.round(h/24)});
const agoS = s => s==null ? '' : ago(s/3600);
// ---- Ages: the state carries times in epoch seconds (activity_ts, start_ts, since_ts, committed_ts, report.ts,
// last_ts), never ages, so an unchanged state keeps its ETag. The page counts them with the server's clock (the Date
// header of the last response: a phone with a wrong clock does not matter) and, on every poll, also after a 304,
// repaints only those labels (the elements with data-at, paintAges)
let SKEW = 0;   // the server's clock minus this one, in ms
const sinceS = ts => ts == null ? null : Math.max(0, Math.round((Date.now() + SKEW) / 1000 - ts));
const AGE_FMT = {dur: s => dur(s), ago: s => agoS(s), ago_dur: s => t('ago_dur', {dur: dur(s)}), idle: s => t('idle_for', {dur: dur(s)}), waits: s => t('waits_dur', {dur: dur(s)})};
const ageText = (ts, f) => ts == null ? '' : AGE_FMT[f](sinceS(ts));
const ageEl = (ts, f='dur') => ts == null ? '' : `<span data-at="${+ts}" data-f="${f}">${esc(ageText(ts, f))}</span>`;
// a mark that changes once a time passes (a lock held over 3 h turns red, an agent running over 2 h gets its warning):
// painted as it is now, with data-turn (epoch s) while that time is still ahead; paintAges turns it then (data-bg: its
// new background; a hidden one shows)
const passed = ts => (Date.now() + SKEW) / 1000 > ts;
function paintAges(){
  document.querySelectorAll('[data-at]').forEach(el => { const s = ageText(+el.dataset.at, el.dataset.f); if(el.textContent !== s) el.textContent = s; });
  document.querySelectorAll('[data-turn]').forEach(el => { if(!passed(+el.dataset.turn)) return;
    if(el.dataset.bg) el.style.background = el.dataset.bg;
    if(el.dataset.cls) el.classList.add(el.dataset.cls);
    el.hidden = false; el.removeAttribute('data-turn'); });
}
// the orchestrator's report older than REPORT_STALE_S: its age turns amber and says it may be out of date
const REPORT_STALE_S = 6 * 3600;
function reportAge(ts){
  const turn = ts + REPORT_STALE_S, late = passed(turn);
  return `<span class="${late ? 'warn ' : ''}stale-age" ${late ? '' : `data-turn="${+turn}" data-cls="warn"`}>${ageEl(ts, 'ago_dur')}</span>`
    + `<span class="warn stale-note" ${late ? '' : `data-turn="${+turn}" hidden`}> · ${esc(t('report_stale'))}</span>`;
}
function setSkew(r){ const ms = Date.parse(r.headers.get('Date') || ''); if(!isNaN(ms)) SKEW = ms + 500 - Date.now(); }   // Date has whole seconds
const hhmm = h => { const s = String(h || ''); return s.length > 5 ? s.slice(11, 16) : s; };
// who orchestrates a product (foreman session ID --name NAME): its SendMessage name and since when (hh:mm today)
const orchLine = (o, plain) => { if(!o) return t('no_orchestrator');
  const e = plain ? (x => x) : esc;
  const s = String(o.since || ''), today = new Date().toLocaleDateString('sv');
  return t('orchestrated_by', {name: plain ? (o.name || t('orch_session', {id: String(o.session).slice(0, 8)})) : `<b>${esc(o.name || t('orch_session', {id: String(o.session).slice(0, 8)}))}</b>`, since: e(s.slice(0, 10)===today ? hhmm(s) : s.replace('T', ' ').slice(5, 16))}); };
// the marked orchestrator's health (orchestrator_health): red when its session is not alive, amber when it gives
// no loop passes (alive without passes, or silent: its process cannot be checked); null when all is well
const HEALTH_CLS = {gone: 'bad', no_loop: 'warn', silent: 'warn'};
const healthBad = h => h && HEALTH_CLS[h.verdict] ? h : null;
const whenShort = s => { s = String(s || ''); return s.slice(0, 10)===new Date().toLocaleDateString('sv') ? hhmm(s) : s.replace('T', ' ').slice(5, 16); };
const orchHealthText = h => h.verdict==='gone' ? t('orch_gone') : h.last_pass ? t('orch_no_pass', {when: whenShort(h.last_pass)}) : t('orch_no_pass_seen');
const healthTag = h => healthBad(h) ? `<span class="orchx ${HEALTH_CLS[h.verdict]}" title="${esc(t('orch_' + h.verdict + '_tip'))}">${esc(orchHealthText(h))}</span>` : '';
// the foreman each live session runs (/api/projects sessions and foreman_installed): «0.16.0», «≈0.16.0» when
// estimated, «?»; a session behind the installed version is only ever amber, never red (it still works, on old code)
const vLabel = e => e && e.version ? (e.estimated ? '≈' : '') + e.version : '?';
const vInstalled = () => ((OVER || {}).foreman_installed || {}).version || '?';
const behindOf = name => ((OVER || {}).sessions || []).filter(e => e.behind && e.project === name);
const vTip = e => t('fsess_tip', {mine: vLabel(e), installed: vInstalled()}) + (e.estimated ? '. ' + t('fsess_estimated_tip') : '');
const behindTag = name => { const n = behindOf(name).length;
  return n ? `<span class="ro warn vbx" title="${esc(t('fsess_behind_tip'))}">${esc(tn('fsess_behind', n))}</span>` : ''; };
const orchVersionTag = name => { const e = behindOf(name).find(x => x.orchestrator);
  return e ? `<span class="ro warn vbx" title="${esc(t('fsess_orch_tip', {installed: vInstalled()}) + (e.estimated ? '. ' + t('fsess_estimated_tip') : ''))}">${esc(t('fsess_orch', {version: vLabel(e)}))}</span>` : ''; };
const ktok = n => n==null ? '' : n >= 1000 ? `${Math.round(n/1000)}k` : `${n}`;

// ---- Markdown: a light one for short texts (md) and a fuller one for documents (mdFull, with tables and code)
function inline(s){
  return esc(s).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>')
    .replace(/\[\[([^\]]+)\]\]/g,'$1').replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g,'<a href="$2">$1</a>');
}
function md(text){
  if(!text) return '<span class="muted">—</span>';
  let html='', inList=false;
  for(const raw of text.split('\n')){
    const l = raw.trimEnd(); const m = l.match(/^\s*(?:[-*]|\d+\.)\s+(.*)/);
    if(m){ if(!inList){html+='<ul>';inList=true} html+=`<li>${inline(m[1])}</li>`; }
    else { if(inList){html+='</ul>';inList=false} if(l.trim()) html+=`<p>${inline(l)}</p>`; }
  }
  return html + (inList?'</ul>':'');
}
function inlineF(s){ return mdInline(s, DOC_OPTS); }
// ---- Markdown for documents and wiki pages (mdDoc): headings with anchors, nested lists, tables (in their own scroller),
// fenced code, quotes (shown as callouts), rules, bold, italics, strike-through, code, [[page]] and [text](url) links.
// Not covered: HTML (it is shown escaped), footnotes, setext headings, indented code without fences.
// opts.link(target, textHtml) and opts.code(text) decide what links and code become (documents, wiki pages);
// opts.img(target, alt), what an image ![alt](target) becomes (only a document's: docOpts); without it, as before.
const fold = t => String(t).normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase();
const slug = t => fold(t.replace(/<[^>]+>/g, '')).replace(/&[a-z#0-9]+;/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 's';
const wikiTitle = n => (WIKI.list && WIKI.list.find(p => p.n===n) || {}).title || n;
function wikiLink(n, anchor, label){
  if(WIKI.list && !WIKI.list.some(p => p.n===n)) return `<span class="wbroken" title="${esc(t('wiki_no_page', {n}))}">${label}</span>`;
  return `<a class="wl" data-wiki="${esc(n)}"${anchor ? ` data-anchor="${esc(slug(anchor))}"` : ''}>${label}</a>`;
}
const DOC_OPTS = {
  // documents: a bare `….md` opens in the viewer; [[page]] opens the wiki page
  code: c => /^[\w./-]+\.md$/.test(c) ? `<a class="dl" data-doc="${esc(c)}">${esc(c)}</a>` : `<code>${esc(c)}</code>`,
  link: (u, t) => /^https?:/.test(u) ? `<a href="${esc(u)}" target="_blank" rel="noopener">${t}</a>`
    : /\.md(#.*)?$/.test(u) ? `<a class="dl" data-doc="${esc(u.replace(/#.*/, '').replace(/^(\.\.\/)+/, ''))}">${t}</a>` : `${t}`,
};
function mdInline(s, o){
  const keep = [], put = h => `\u0000${keep.push(h) - 1}\u0000`;
  s = s.replace(/(`+)([\s\S]*?[^`])\1(?!`)/g, (m, f, c) => put(o.code(c.trim())));
  // [[page]] shows the page title, as in Notion (the name if the list is not loaded yet)
  s = s.replace(/\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]/g, (m, n, a, l) => put(wikiLink(n.trim(), a, esc(l || wikiTitle(n.trim())))));
  if(o.img) s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (m, a, u) => put(o.img(u, a)));
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (m, t, u) => put(o.link(u, mdInline(t, o))));
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<>)]+[^\s<>).,;:])/g, (m, b, u) => b + put(`<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a>`));
  s = esc(s)
    .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^\w*])\*(?=[^\s*])([^*]*?[^\s*])\*(?![\w*])/g, '$1<i>$2</i>')
    .replace(/(^|[^\w])_(?=\S)([^_]*?\S)_(?!\w)/g, '$1<i>$2</i>')
    .replace(/~~(?=\S)([\s\S]*?\S)~~/g, '<s>$1</s>');
  for(let n = 0; n < 4 && s.includes('\u0000'); n++) s = s.replace(/\u0000(\d+)\u0000/g, (m, i) => keep[+i]);
  return s;
}
// splits a table row on | outside code spans (and not \|)
function rowCells(l){
  const t = l.trim().replace(/^\|/, '').replace(/(^|[^\\])\|$/, '$1'), out = []; let cur = '', code = false;
  for(let i = 0; i < t.length; i++){
    const ch = t[i];
    if(ch === '`') code = !code;
    if(ch === '\\' && t[i+1] === '|'){ cur += '|'; i++; continue; }
    if(ch === '|' && !code){ out.push(cur.trim()); cur = ''; continue; }
    cur += ch;
  }
  out.push(cur.trim()); return out;
}
const LIST_RE = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/, TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const HR_RE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/, FENCE = /^\s*(```|~~~)/;
function mdDoc(text, o=DOC_OPTS){
  const L = String(text || '').replace(/\r/g, '').replace(/\t/g, '    ').split('\n'), toc = [], ids = {};
  let h = '', i = 0;
  const inl = t => mdInline(t, o);
  const isBlockStart = l => /^\s{0,3}#{1,6}\s/.test(l) || FENCE.test(l) || /^\s*>/.test(l) || LIST_RE.test(l) || HR_RE.test(l) || /^\s*\|/.test(l);
  while(i < L.length){
    const l = L[i];
    if(!l.trim()){ i++; continue; }
    let m;
    if((m = l.match(FENCE))){
      const b = []; i++;
      while(i < L.length && !L[i].trim().startsWith(m[1])) b.push(L[i++]);
      i++; h += `<pre><code>${esc(b.join('\n'))}</code></pre>`; continue;
    }
    if((m = l.match(/^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/))){
      const n = m[1].length, inner = inl(m[2]); let id = 'h-' + slug(m[2]);
      if(ids[id]) id += '-' + (++ids[id]); else ids[id] = 1;
      if(n >= 2 && n <= 3) toc.push({n, id, t: inner.replace(/<[^>]+>/g, '')});
      h += `<h${n} id="${id}">${inner}${n > 1 ? `<a class="anc" data-anchor="${id}" aria-label="${esc(t('anchor'))}">#</a>` : ''}</h${n}>`; i++; continue;
    }
    if(HR_RE.test(l)){ h += '<hr>'; i++; continue; }
    if(/^\s*\|/.test(l) && i+1 < L.length && TABLE_SEP.test(L[i+1])){
      const al = rowCells(L[i+1]).map(c => /^:-+:$/.test(c) ? 'center' : /-:$/.test(c) ? 'right' : '');
      const td = (tag, c, k) => `<${tag}${al[k] ? ` style="text-align:${al[k]}"` : ''}>${inl(c)}</${tag}>`;
      h += '<div class="tw"><table><thead><tr>' + rowCells(l).map((c, k) => td('th', c, k)).join('') + '</tr></thead><tbody>'; i += 2;
      while(i < L.length && /^\s*\|/.test(L[i])) h += '<tr>' + rowCells(L[i++]).map((c, k) => td('td', c, k)).join('') + '</tr>';
      h += '</tbody></table></div>'; continue;
    }
    if(/^\s*>/.test(l)){
      const b = [];
      while(i < L.length && /^\s*>/.test(L[i])) b.push(L[i++].replace(/^\s*>\s?/, ''));
      h += `<blockquote class="callout">${mdDoc(b.join('\n'), o).html}</blockquote>`; continue;
    }
    if(LIST_RE.test(l)){
      const st = [];   // open lists: {ind, t}
      while(i < L.length){
        const x = L[i];
        if(!x.trim()){   // a blank line ends the list unless more items (or indented text) follow
          let j = i + 1; while(j < L.length && !L[j].trim()) j++;
          if(j < L.length && (LIST_RE.test(L[j]) || /^\s{2,}\S/.test(L[j]))){ i = j; continue; }
          break;
        }
        const lm = x.match(LIST_RE);
        if(lm){
          const ind = lm[1].length, ord = /\d/.test(lm[2]), t = ord ? 'ol' : 'ul';
          while(st.length && ind < st[st.length-1].ind) h += `</li></${st.pop().t}>`;
          const top = st[st.length-1];
          if(!top || ind > top.ind){
            const start = ord && parseInt(lm[2]) !== 1 ? ` start="${parseInt(lm[2])}"` : '';
            h += `<${t}${start}>`; st.push({ind, t});
          } else {
            h += '</li>';
            if(top.t !== t){ h += `</${top.t}><${t}>`; top.t = t; }
          }
          const task = lm[3].match(/^\[([ xX])\]\s+(.*)/);
          h += '<li>' + (task ? `<span class="task${task[1]===' ' ? '' : ' on'}"></span>${inl(task[2])}` : inl(lm[3]));
        } else if(FENCE.test(x) || (/^\S/.test(x) && isBlockStart(x))){
          break;
        } else {
          h += ' ' + inl(x.trim());   // continuation of the item
        }
        i++;
      }
      while(st.length) h += `</li></${st.pop().t}>`;
      continue;
    }
    const p = [];
    while(i < L.length && L[i].trim() && !(p.length && isBlockStart(L[i]))) p.push(L[i++].trim());
    h += `<p>${inl(p.join(' '))}</p>`;
  }
  return {html: h, toc};
}
// a document of product p in folder base (its path's folder, relative to the home or absolute): its images come from
// /api/file, the path joined to that folder; an image from elsewhere (http, data…) is never fetched: a link to it, or
// its text. Tapping an image opens it whole
const dirOf = path => String(path || '').includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
function joinPath(base, u){
  const out = [];
  for(const x of (u.startsWith('/') || !base ? u : `${base}/${u}`).split('/')){
    if(x === '..'){ if(!out.length || out[out.length-1] === '') return ''; out.pop(); }
    else if(x !== '.' && (x || !out.length)) out.push(x);
  }
  return out.join('/');
}
function docOpts(p, base){
  return {...DOC_OPTS, img: (u, alt) => {
    if(/^https?:/i.test(u)) return `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(alt || u)}</a>`;
    const path = /^[a-z][\w+.-]*:/i.test(u) ? '' : joinPath(base, u.replace(/[?#].*/, ''));
    if(!path) return esc(alt);
    const src = esc(api(`/api/file?p=${encodeURIComponent(p)}&path=${encodeURIComponent(path)}`));
    return `<a class="dimg" href="${src}" target="_blank" rel="noopener"><img loading="lazy" src="${src}" alt="${esc(alt)}"></a>`;
  }};
}
// a whole document; with its product and path, its images too
function mdFull(text, p, path){ return `<div class="md wdoc">${mdDoc(text, p && path ? docOpts(p, dirOf(path)) : DOC_OPTS).html}</div>`; }

// ---- Shared components (markup for the classes in app.css)
const NONE = '<span class="muted">—</span>';
// section title with an optional count and something on the right
const sect = (title, inner, n, aside) => `<h2 class="sh">${title}${n!=null ? `<span class="n">${n}</span>` : ''}${aside ? `<span class="sa">${aside}</span>` : ''}</h2>${inner}`;
const card = inner => `<div class="card">${inner}</div>`;
const list = (rows, attrs='') => `<div class="list"${attrs}>${rows}</div>`;
const empty = t => `<div class="empty">${t}</div>`;
// one-line row: k/key open the detail view k (see VIEWS); title and meta are HTML, the caller escapes them
function lrow({k, key, lead='', icon='', id='', name='', title='', meta='', cls='', attrs=''}){
  const open = k ? ` data-k="${k}" data-key="${esc(key)}"` : '';
  return `<div class="lrow ${cls}"${open}${attrs}>${lead}${icon}${id ? `<span class="id">${esc(id)}</span>` : ''}${name}<span class="tt">${title}</span>${meta ? `<span class="meta">${meta}</span>` : ''}</div>`;
}
// collapsible group (folding is remembered per scope and key; Archive and «In main today» start folded)
const FOLD = (() => { try { return JSON.parse(lsGet('folded', '{}')) || {}; } catch(e) { return {}; } })();
const FOLDED_BY_DEFAULT = new Set(['archive', 'canceled', 'duplicate', 'triage', 'done', 'main']);
const isFolded = (scope, key) => FOLD[scope+':'+key] ?? FOLDED_BY_DEFAULT.has(key);
function toggleGroup(scope, key, el){
  FOLD[scope+':'+key] = !isFolded(scope, key); lsSet('folded', JSON.stringify(FOLD));
  el.closest('.grp').classList.toggle('fold', FOLD[scope+':'+key]);
}
const group = (scope, key, head, rows, emptyText) =>
  `<div class="grp${isFolded(scope, key) ? ' fold' : ''}"><div class="ghead" onclick="toggleGroup('${esc(scope)}','${esc(key)}',this)"><span class="car">▾</span>${head}</div>`
  + `<div class="rows" data-lane="${esc(key)}">${rows || empty(emptyText)}</div></div>`;
// property block; pairs with an empty label are skipped, empty values show a dash
const props = (pairs, cls='') => `<div class="props ${cls}">${pairs.filter(p => p && p[0]).map(([k, v]) => `<div class="prow"><span class="k">${k}</span><span class="v">${v || NONE}</span></div>`).join('')}</div>`;
// detail header: kind or ID on top, title below
const dhead = (kind, title, icon='', cls='') => `<div class="kind">${kind}</div><h1 class="stt ${cls}">${icon}${title}</h1>`;
const dot = (color, title='') => `<span class="dot" style="background:${color}"${title ? ` title="${esc(title)}"` : ''}></span>`;
const chip = (cls, txt, to) => `<span class="chip ${cls}" onclick="tab('${to}')">${txt}</span>`;
// the product a row belongs to, only in the joint view
// a product is shown by its [panel] title (the registry name stays its id in keys, URLs and the API)
const pname = n => ((OVER && OVER.projects.find(o => o.name===n)) || {}).title || n;
// a project key of the joint view, «product·E1», shown with the product's title
const ekey = k => { const i = String(k).indexOf('·'); return i < 0 ? k : pname(k.slice(0, i)) + k.slice(i); };
// the product's language as a small tag (its code; the tooltip says what it is)
const langTag = o => o.language ? `<span class="ro lang" title="${esc(t('language_of', {lang: o.language}))}">${esc(o.language.toUpperCase())}</span>` : '';
const ptag = p => lastData && lastData.joint && p ? `<span class="ptag">${esc(pname(p))}</span>` : '';

// ---- Status icons and colours
// agent states with fixed colours (as in agent panels): working green, waiting yellow, stuck red, finished grey
const STATE_COLORS = {'working':'var(--ok)','holds a lock':'var(--ok)','waiting for a lock':'var(--warn)','waiting for its task':'var(--warn)','maybe stuck':'var(--bad)','finished':'var(--muted)'};
const scol = st => STATE_COLORS[st] || 'var(--muted)';
const sname = st => st ? t('agent_status.' + st) : '';
const sdot = a => dot(scol(a.status), sname(a.status));
// one colour per agent, the same in every view
const hue = n => { let h = 0; for(const ch of String(n)) h = (h*31 + ch.charCodeAt(0)) % 360; return h; };
const acol = n => n==='orchestrator' ? 'var(--muted)' : `hsl(${hue(n)} 65% 50%)`;
const aname = n => `<span class="nm" style="color:${acol(n)}">${esc(n==='orchestrator' ? t('orchestrator_lc') : n)}</span>`;
// one colour per project of the backlog (E1, E2… by number; any other id by its hash)
const ecol = e => { const m = String(e).match(/^(?:.*·)?[A-Z]+(\d+)$/); return `hsl(${((m ? parseInt(m[1]) : hue(e)) * 47 + 200) % 360} 55% 48%)`; };
const BLOCK_ICON = () => `<svg class="bico" width="14" height="14" viewBox="0 0 14 14" aria-label="${esc(t('blocked_lc'))}"><path d="M4.5 1h5L13 4.5v5L9.5 13h-5L1 9.5v-5z" fill="var(--bad)"/><path d="M4 7h6" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/></svg>`;
const PLAN_ICON = () => `<svg class="pico" width="13" height="13" viewBox="0 0 14 14" aria-label="${esc(t('with_plan'))}"><title>${esc(t('with_plan'))}</title><path d="M3.5 1.5h5l3 3v8h-8z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M5.5 7.5h4M5.5 10h4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>`;
// Linear-style urgency, at the left of the id: a warn square with «!» for urgent, three rising bars (filled up to
// the level) for the others, and nothing (an empty slot) for 'none', a missing field or an unknown value; always 14 px wide, so
// the ids of the list stay aligned in a column
const PRIO_ICON = p => {
  if(!['urgent', 'high', 'medium', 'low'].includes(p)) p = 'none';
  const lbl = esc(`${t('priority_lbl')}: ${t('priorities.' + p)}`);
  const body = p==='none' ? ''
    : p==='urgent'
    ? '<rect x="1" y="1" width="12" height="12" rx="3" fill="var(--warn)"/><path d="M7 3.5v4.2" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/><circle cx="7" cy="10.2" r="1" fill="#fff"/>'
    : [[1.5, 8], [5.5, 5], [9.5, 2]].map(([x, y], n) =>
        `<rect x="${x}" y="${y}" width="3" height="${12 - y}" rx="1" fill="currentColor"${n < {high: 3, medium: 2, low: 1}[p] ? '' : ' opacity=".3"'}/>`).join('');
  return `<svg class="prio" width="14" height="14" viewBox="0 0 14 14" aria-label="${lbl}"><title>${lbl}</title>${body}</svg>`;
};
const ICONS = {   // small 14 px line icons for the non-story rows
  branch: '<path d="M4 2.5v9M4 11.5a1.5 1.5 0 1 0 0 .01M10 2.5a1.5 1.5 0 1 0 0 .01M10 4c0 3-6 2.5-6 6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
  merged: '<circle cx="7" cy="7" r="6" fill="var(--acc)"/><path d="M4.3 7.2l1.8 1.8 3.6-3.8" fill="none" stroke="#fff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
  cpu: '<rect x="3" y="3" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5.5 1v2M8.5 1v2M5.5 11v2M8.5 11v2M1 5.5h2M1 8.5h2M11 5.5h2M11 8.5h2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>',
  check: '<circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M4.6 7.1l1.6 1.6 3.2-3.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>',
  clock: '<circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M7 4v3.2l2 1.3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
  doc: '<path d="M3.5 1.5h5l3 3v8h-8z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M5.5 7.5h4M5.5 10h4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
  pr: '<circle cx="4" cy="3" r="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="4" cy="11" r="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="10" cy="11" r="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M4 4.5v5M10 9.5V6a2 2 0 0 0-2-2H6.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
  in: '<path d="M2 3.5h10v6H6l-3 2.5V9.5H2z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
  out: '<path d="M12 3.5H2v6h6l3 2.5V9.5h1z" fill="currentColor" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
};
const ico = (name, color='var(--muted)') => `<svg class="sico" width="14" height="14" viewBox="0 0 14 14" style="color:${color}" aria-hidden="true">${ICONS[name]}</svg>`;
// lane icons, like Linear's issue states
function sico(l){
  const c = {'backlog':'var(--muted)','ready':'var(--fg)','refining':'var(--acc)','in progress':'var(--prog)','done':'var(--acc)','canceled':'var(--muted)','duplicate':'var(--muted)','archive':'var(--muted)','triage':'var(--muted)'}[l];
  const base = `<svg class="sico" width="14" height="14" viewBox="0 0 14 14">`;
  if(l==='refining') return base+`<circle cx="7" cy="7" r="5.5" fill="none" stroke="${c}" stroke-width="1.5" stroke-dasharray="6 2"/><circle cx="7" cy="7" r="2" fill="${c}"/></svg>`;
  if(l==='triage') return base+`<circle cx="7" cy="7" r="5.5" fill="none" stroke="${c}" stroke-width="1" stroke-dasharray="1 2.5"/></svg>`;
  if(l==='backlog') return base+`<circle cx="7" cy="7" r="5.5" fill="none" stroke="${c}" stroke-width="1.5" stroke-dasharray="2 2"/></svg>`;
  if(l==='ready') return base+`<circle cx="7" cy="7" r="5.5" fill="none" stroke="${c}" stroke-width="1.5"/></svg>`;
  if(l==='in progress') return base+`<circle cx="7" cy="7" r="5.5" fill="none" stroke="${c}" stroke-width="1.5"/><path d="M7 3.5 A3.5 3.5 0 0 1 7 10.5 Z" fill="${c}"/></svg>`;
  if(l==='done') return base+`<circle cx="7" cy="7" r="6" fill="${c}"/><path d="M4.3 7.2l1.8 1.8 3.6-3.8" fill="none" stroke="#fff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  return base+`<circle cx="7" cy="7" r="6" fill="${c || 'var(--muted)'}"/><path d="M4.8 4.8l4.4 4.4M9.2 4.8l-4.4 4.4" stroke="var(--card)" stroke-width="1.4" stroke-linecap="round"/></svg>`;
}

// ---- State
let lastSignature = null, lastData = null, OVER = null;
// the page's own version, from the moment it loads: the server writes it into index.html (<html data-v>) and into the
// asset URLs (app.js?v=), so a page whose code came from an older version (a restored tab, the browser's cache) sees
// the difference in its first answer, even when that answer is what makes the old code fail
let PAGE_VERSION = pageVersion(document.documentElement.dataset.v, document.currentScript && document.currentScript.src);
function pageVersion(attr, src){
  const v = Number(attr);
  if(Number.isFinite(v) && v > 0) return v;
  try{ return Number(new URL(src).searchParams.get('v')) || 0; }catch(e){ return 0; }
}
// reload when the server serves another version than the page's; once per server version (sessionStorage), so a
// server that keeps answering another version (or a page that cannot store it) never loops. location.reload keeps
// the URL, and with it the key (?t=)
function reloadDecision(page, server, done){
  if(!page || !server || server === page) return false;
  return String(done) !== String(server);
}
function versionCheck(server){
  if(!PAGE_VERSION){ PAGE_VERSION = server || 0; return false; }
  let done = null;
  try{ done = sessionStorage.getItem('reloaded_for'); }catch(e){ return false; }
  if(!reloadDecision(PAGE_VERSION, server, done)) return false;
  try{ sessionStorage.setItem('reloaded_for', String(server)); }catch(e){ return false; }
  location.reload();
  return true;
}
let BL = [];
let P = lsGet('product', '');   // the product shown: a project name of the registry, or '*' for all of them
let epic = lsGet('project_filter', '');
let blView = ['issues', 'projects'].includes(lsGet('view', '')) ? lsGet('view', '') : 'issues';   // a view no longer offered falls back to the issues
const SNAP = {};   // rows of lists without a stable key (messages, the last hour): their objects, by row index
const proj = name => (lastData && lastData.ps.find(p => p.name===name)) || {};
const canWrite = name => !!proj(name).writable;

async function post(path, p){
  let err = '';
  try{
    const r = await fetch(api(`${path}${path.includes('?')?'&':'?'}p=${encodeURIComponent(p || '')}`), {method:'POST'});
    if(!r.ok) err = (await r.json().catch(() => ({}))).error || `${r.status}`;
  }catch(e){ err = t('no_connection'); }
  lastSignature = null; lastEtag = null; lastBlEtag = null; lastBlSig = null; await load();
  if(err) $('err').textContent = t('error', {text: err});
}

// ---- The data of the products shown (one, or all for the joint view), as one set of lists; each item keeps its
// product in .p (writes go to it). The backlog comes apart (/api/backlog, b: only when its files change) and the
// state brings what is live about its issues (issue_live: agent, branch, production slot, «in progress», no agent),
// laid over the open cards here; the cached cards are never changed. Closed cards come short (c.short: what their row
// shows); the detail loads the whole card (fullCard). The page asks for the light load (closed=recent): only the most
// recent closed issues of each product come short, the older ones as stubs (c.stub: id, status, aliases),
// which count and link like any card and show behind «show the N older» (showOlder)
function withLive(bl, live){
  const lay = c => { const x = live[c.id]; if(!x) return c;
    // a stale status is not overridden: the backlog may already say the issue moved on
    const {status, ...rest} = x;
    return {...c, ...rest, ...(status && ['backlog', 'ready'].includes(c.status) ? {status} : {})}; };
  return {...bl, projects: (bl.projects || []).map(e => ({...e, issues: e.issues.map(lay)})), issues: (bl.issues || []).map(lay)};
}
// a registry problem (Python's text: «name: error» when a product does not load, «… (ignored)» for a setting that
// was not understood) as a plain sentence, with that text kept as the detail
function problemOf(e){
  const m = /^([\w.-]+): /.exec(e);
  return {text: /\(ignored\)/.test(e) ? t('problem_ignored') : m && m[1] !== 'registry' ? t('problem_load', {name: m[1]}) : t('problem_config'), detail: e};
}
function normalize(d, b){
  const bls = Object.fromEntries(((b && (b.projects || [b.project])) || []).filter(Boolean).map(x => [x.name, x.backlog]));
  const ps = (d.projects || [d.project]).map(p => ({...p, backlog: withLive(bls[p.name] || {projects: []}, p.issue_live || {})})), joint = P==='*';
  const tag = (p, xs) => (xs || []).map(x => ({...x, p: p.name}));
  const all = k => ps.flatMap(p => tag(p, p[k]));
  const byTime = (a, b) => String(a.time || '').localeCompare(String(b.time || ''));
  const cards = ps.flatMap(p => {
    const titles = Object.fromEntries(p.backlog.projects.map(e => [e.id, e.title]));
    // an issue without project (Linear's team issues) has eid '' and epic '' in the single view, «product·» in the joint one
    // a stub gets what a short card has (no title, plan or sub-issues), so every reader of BL works; its detail loads
    const mk = (c, eid) => ({...(c.stub ? {title: '', plan: [], subs: [], short: true} : {}), ...c, p: p.name, eid, epic: joint ? `${p.name}·${eid}` : eid, epic_t: titles[eid] || '', lane: c.status});
    return p.backlog.projects.flatMap(e => e.issues.map(c => mk(c, e.id))).concat((p.backlog.issues || []).map(c => mk(c, '')))
      .concat((p.backlog.archive || []).map(c => mk(c, c.project || '')));
  });
  // the projects with their own fields (status, priority, repos, products, dependencies), by their key
  const projects = {};
  ps.forEach(p => {
    p.backlog.projects.forEach(e => { projects[joint ? `${p.name}·${e.id}` : e.id] = {...e, p: p.name, issues: undefined}; });
  });
  const names = new Set(ps.map(p => p.name));
  return {ps, joint, version: d.version,
    locks: d.locks.filter(l => l.projects.some(x => names.has(x))),
    agents: all('agents'), decisions: all('decisions'), grants: all('grants'), messages: all('messages').sort(byTime),
    moves: all('moves').sort(byTime).reverse().slice(0, 5), activity: all('activity'),
    progress: all('progress'), notes: all('notes'), recurring: all('recurring'), repos: all('repos'), backlog: cards,
    projects,
    errors: ps.filter(p => p.backlog_error).map(p => ({text: t('backlog_unreadable', {name: p.title || p.name}), detail: p.backlog_error}))
      .concat(((OVER || {}).errors || []).map(problemOf))};
}

// What a product asks of the owner, from its facts {broken, mode, questions, grants, orch, health, paused, late}:
// the level (bad: its orchestrator is down; acc: waits for him; warn: stopped or overdue; ok; muted: inactive) and why.
// The product picker and the rows of «All products» in Now use it, so a product without orchestrator is never green.
const NEED_RANK = {bad: 0, acc: 1, warn: 2, ok: 3, muted: 4};
function productNeeds(f){
  if(f.broken) return {level: 'bad', reasons: [t('config_broken_tag')]};
  const hb = healthBad(f.health), why = [];
  let level = 'ok';
  const raise = l => { if(NEED_RANK[l] < NEED_RANK[level]) level = l; };
  if(f.questions){ why.push(tn('need_questions', f.questions)); raise('acc'); }
  if(f.grants){ why.push(tn('need_grants', f.grants)); raise('acc'); }
  if(hb){ why.push(orchHealthText(hb)); raise(HEALTH_CLS[hb.verdict]==='bad' ? 'bad' : 'warn'); }
  else if(!f.orch && f.mode!=='inactive'){ why.push(t('need_no_orch')); raise('warn'); }
  if(f.paused){ why.push(t('need_paused')); raise('warn'); }
  if(f.late){ why.push(tn('late_count', f.late)); raise('warn'); }
  if(!why.length) why.push(f.mode==='inactive' ? t('need_inactive') : t('need_none'));
  if(level==='ok' && f.mode==='inactive') level = 'muted';
  return {level, reasons: why};
}
const NEED_COLOR = {bad: 'var(--bad)', acc: 'var(--acc)', warn: 'var(--warn)', ok: 'var(--ok)', muted: 'var(--muted)'};
// «All products» in Now: one line per product, the ones that need him first; a tap opens that product
function renderProducts(d){
  if(!d.joint){ $('products').innerHTML = ''; return; }
  const rows = ((OVER || {}).projects || []).map(o => {
    const mine = o.broken ? null : d.ps.find(p => p.name===o.name);
    const of = xs => xs.filter(x => x.p===o.name);
    const n = productNeeds(o.broken ? {broken: true} : {mode: o.mode, orch: o.orchestrator, health: o.orchestrator_health,
      paused: mine ? mine.paused : o.paused, questions: of(d.decisions).filter(x => !x.answer).length,
      grants: of(d.grants).filter(x => x.pending).length, late: of(d.recurring).filter(r => r.late).length});
    return {n, name: o.name, html: lrow({icon: dot(NEED_COLOR[n.level]), title: esc(o.title || o.name), meta: esc(n.reasons.join(' · ')),
      cls: 'prow wrap', attrs: ` onclick="pick('${esc(o.name)}')" style="cursor:pointer"`})};
  });
  rows.sort((a, b) => NEED_RANK[a.n.level] - NEED_RANK[b.n.level]);
  $('products').innerHTML = sect(t('home_title'), list(rows.map(r => r.html).join('')), rows.length);
}
// ---- Ahora: status headline, chips, what needs attention, the locks and the orchestrator's report
// why a product is stopped: its orchestrator does not answer (down), the loop is paused, nobody orchestrates it
// (none) or it is inactive on purpose; null when it runs. The headline and «Te necesita» both read it.
const up1 = x => x.charAt(0).toUpperCase() + x.slice(1);
const stopOf = p => p.broken ? null : healthBad(p.orchestrator_health) ? 'down' : p.paused ? 'paused'
  : !p.orchestrator ? (p.mode==='inactive' ? 'inactive' : 'none') : null;
function attention(d){
  const att = [];   // [level, text, action]
  d.ps.forEach(p => { const k = stopOf(p);
    if(k==='down') att.push(['bad', `${ptag(p.name)}${esc(up1(orchHealthText(healthBad(p.orchestrator_health))))}. ${t('stopped_down_do')}`, '']);
    else if(k==='none') att.push(['warn', `${ptag(p.name)}${t('stopped_none_text')}`, '']); });
  // a product that loads with config warnings (unknown keys, a newer schema): it works, but `foreman check` fails
  d.ps.forEach(p => (p.warnings || []).forEach(w => att.push(['warn', `${ptag(p.name)}${t('config_warning', {text: esc(w)})}`, ''])));
  d.ps.filter(p => p.paused).forEach(p => att.push(['warn', `${ptag(p.name)}${t('paused', {text: esc(p.paused)})}`, p.writable ? `<button class="sm" onclick="post('/api/pause?on=0','${esc(p.name)}')">${t('resume')}</button>` : '']));
  d.agents.filter(a => a.status==='maybe stuck').forEach(a => att.push(['bad', `${ptag(a.p)}${t('stuck', {name: esc(a.name), dur: ageEl(a.activity_ts)})}`, `<button class="sec sm" data-k="ag" data-key="${esc(a.id)}">${t('see')}</button>`]));
  const unanswered = d.decisions.filter(x => !x.answer).length;
  if(unanswered) att.push(['acc', tn('decisions_wait', unanswered), `<button class="sm" onclick="tab('you')">${t('answer')}</button>`]);
  const asks = d.grants.filter(x => x.pending), say = asks.length === 1 ? grantSay(asks[0]) : null;
  if(asks.length) att.push(['acc', say ? t('grant_wait_say', {say}) : tn('grants_wait', asks.length), `<button class="sm" onclick="seeGrants()">${t('see')}</button>`]);
  d.recurring.filter(r => r.late).forEach(r => att.push(['warn', `${ptag(r.p)}${t('late', {name: esc(r.name)})}`, `<button class="sec sm" data-k="rec" data-key="${esc(recKey(r))}">${t('see')}</button>`]));
  return att;
}
const lockName = n => n.length <= 3 ? n.toUpperCase() : n;
const holder = l => l.holders.length ? l.holders.map(h => h.who).join(', ') : null;
// the chips under the headline; memory and disk come from the machine's stats (/api/projects, every 30 s)
function renderChips(d){
  const alive = d.agents.filter(a => !a.end);
  const mm = (OVER || {}).mac || {}, memU = mm.mem_free_pct==null ? null : 100 - mm.mem_free_pct;
  const prs = d.repos.filter(r => r.github && r.prs);
  $('chips').innerHTML = [
    ...d.locks.map(l => chip('', `${esc(lockName(l.name))} <b>${esc(holder(l) || t('lock_free_lc'))}</b>`, 'now')),
    chip(alive.some(a => a.status==='maybe stuck') ? 'bad' : '', `${t('chip_agents')} <b>${alive.length}</b>`, 'work'),
    prs.some(r => r.prs.length) ? chip(prs.some(r => r.prs.some(x => x.checks.fail)) ? 'bad' : '', `${t('chip_prs')} <b>${prs.reduce((n, r) => n + r.prs.length, 0)}</b>`, 'work') : '',
    ...((OVER || {}).quota || []).map(q => chip(q.percent_used>=95?'bad':q.percent_used>=80?'warn':'', `${t(q.kind==='five_hour'?'chip_quota5':'chip_quota7')} <b>${Math.round(q.percent_used)} %</b>`, 'system')),
    chip(memU>=80?'bad':memU>=65?'warn':'', `${t('chip_memory')} <b>${memU ?? '?'} %</b>`, 'system'),
    chip(mm.disk_free_gb<50?'bad':mm.disk_free_gb<100?'warn':'', `${t('chip_disk')} <b>${mm.disk_free_gb ?? '?'} GB</b>`, 'system'),
  ].join('');
}
function renderNow(d){
  const att = attention(d);
  const bads = att.filter(a => a[0]==='bad').length, mine = att.filter(a => a[0]==='acc').length;
  const stops = d.ps.map(p => [stopOf(p), p]).filter(x => x[0]), live = stops.filter(x => x[0]!=='inactive');
  const stopped = !stops.length ? null : live.length ? live : stops.length===d.ps.length ? stops : null;
  const [mark, color, headline] = stopped ? ['!', stopped.some(x => x[0]==='down') ? 'var(--bad)' : stopped[0][0]==='inactive' ? 'var(--muted)' : 'var(--warn)',
      stopped.length > 1 ? tn('stopped', stopped.length) : t('stopped_' + stopped[0][0])]
    : bads ? ['!', 'var(--bad)', tn('problems', bads)]
    : mine ? ['?', 'var(--acc)', t('needs_you')] : att.length ? ['!', 'var(--warn)', t('with_notices')] : ['✓', 'var(--ok)', t('all_fine')];
  // the agents themselves are listed in Trabajo; here only who is running
  const alive = d.agents.filter(a => !a.end);
  const who = stopped ? (stopped.length > 1 ? stopped.map(x => esc(x[1].title || x[1].name)).join(', ')
      : (d.joint ? `${esc(stopped[0][1].title || stopped[0][1].name)}: ` : '') + t('stopped_' + stopped[0][0] + '_do'))
    : alive.length ? t('agents_running', {names: alive.map(a => esc(a.name)).join(', ')}) : t('no_agents');
  $('headline').innerHTML = `<div class="headline"><div class="ico" style="background:${color}">${mark}</div><div style="min-width:0"><div class="t">${headline}</div><div class="s">${who}</div></div></div>`;
  renderProducts(d);
  renderChips(d);
  const lv = {bad: 'var(--bad)', warn: 'var(--warn)', acc: 'var(--acc)'};
  $('notices').innerHTML = att.length ? sect(t('needs_attention'), list(att.map(([n, s, b]) => lrow({icon: dot(lv[n]), title: s, meta: b, cls: 'wrap att'})).join('')), att.length) : '';
  $('locks').innerHTML = renderLocks(d);

  // the report of each product shown, with its loop
  $('report').innerHTML = d.ps.map(p => {
    const loopPart = ((p.recurring || [])[0] || {parts: [{}]}).parts[0] || {};
    const hb = healthBad(p.orchestrator_health);
    // an inactive product (set up, its orchestrator not started) has no loop to pause
    const foot = p.mode==='inactive' ? `<div class="foot">${dot('var(--muted)')}<span>${t('inactive')}</span></div>`
      : `<div class="foot">${dot(hb ? `var(--${HEALTH_CLS[hb.verdict]})` : p.paused ? 'var(--warn)' : 'var(--ok)')}<span>${orchLine(p.orchestrator)}${orchVersionTag(p.name)} · ${hb ? healthTag(hb) : p.paused ? t('loop_paused') : t('loop_active')} · ${t('last_pass', {when: loopPart.last_ts != null ? ageEl(loopPart.last_ts, 'ago') : t('never')})}</span>${p.paused || !p.writable ? '' : `<button class="sec sm" onclick="post('/api/pause?on=1','${esc(p.name)}')">${t('pause')}</button>`}</div>`;
    return sect(d.joint ? `${t('report')} · ${esc(p.title)}` : t('report'), card((p.report ? `${mdFull(p.report.text)}` : `<span class="muted">${t('no_report')}</span>`) + foot),
      null, p.report ? reportAge(p.report.ts) : '');
  }).join('');
}
// ---- Resources: one row per lock (dot, name, who has it, queue) with the one action its state allows; the row opens
// the detail. The owner's own lock: «+30 min» and «Soltar» with the end time; someone else's: «Ponerme en cola».
const lockWhat = w => String(w || '').replace(/\s*\(\d{4}-\d\d-\d\d [\d:]+(, [^)]*)?\)\s*$/, '');
const hhmmIso = s => (String(s || '').match(/T(\d\d:\d\d)/) || [])[1] || '';
// how long it has been held: since_ts is the time it was taken (the owner file, else the meta)
const heldS = h => sinceS(h.since_ts);
const lockMine = l => l.owner_state==='holds' || l.holders.some(h => h.who===l.me);
const lockQueued = l => l.queue.length + (l.next && !l.queue.some(q => l.next.startsWith(q.who + ':')) ? 1 : 0);
function lockSince(h){
  const at = hhmmIso(h.since);
  return at ? esc(t('since_t', {t: at})) : ageEl(h.since_ts, 'ago_dur');
}
const LOCK_LONG_S = 10800;   // someone else's lock held this long shows red
function lockColor(l){
  if(!l.holders.length) return 'var(--ok)';
  if(lockMine(l)) return 'var(--acc)';
  return (heldS(l.holders[0]) || 0) > LOCK_LONG_S ? 'var(--bad)' : 'var(--warn)';
}
// its dot; amber turns red when the 3 h pass, without a new state (paintAges)
function lockDot(l){
  const c = lockColor(l), ts = (l.holders[0] || {}).since_ts;
  return c==='var(--warn)' && ts != null ? `<span class="dot" style="background:${c}" data-turn="${+ts + LOCK_LONG_S}" data-bg="var(--bad)"></span>` : dot(c);
}
// who has it, in one line: "free", "you · until 18:30", "ag1 · e2e login · since 16:03"
function lockWho(l){
  if(!l.holders.length) return `<span class="muted">${t('lock_free_lc')}</span>`;
  if(lockMine(l)) return `<b>${t('you_lc')}</b> <span class="muted">· ${l.owner_expires ? t('until_t', {t: esc(l.owner_expires)}) : t('no_limit')}</span>`;
  return l.holders.map(h => `<b>${esc(h.who)}</b><span class="muted">${[esc(lockWhat(h.what) + (h.slot ? ` [${h.slot}]` : '')), lockSince(h)].filter(Boolean).map(x => ' · ' + x).join('')}</span>`).join(', ');
}
// takeable here: some active product that declares it is shown (in an inactive product's view the row only says who
// has it; a resource shared with an active product gets its action in that product and in «all products»)
const lockHere = l => !!(l.takeable && lastData && lastData.ps.some(p => (l.takers || []).includes(p.name)));
// the buttons for the lock's state (none if the panel cannot take it here)
function lockActs(l){
  const r = encodeURIComponent(l.name), b = (cls, path, text, q='') => `<button class="sm ${cls}" onclick="post('/api/lock/${path}?r=${r}${q}')">${text}</button>`;
  if(!lockHere(l)) return '';
  if(l.owner_state==='holds') return (l.owner_expires ? b('sec', 'extend', t('plus_30'), '&min=30') : '') + b('bad', 'release', t('release'));
  if(l.owner_state==='waiting') return b('sec', 'release', t('cancel'));
  return l.holders.length ? b('sec', 'take', t('queue_me'), '&min=60') : b('', 'take', t('use'), '&min=60');
}
function lockRow(l){
  const n = lockQueued(l);
  // two fixed columns (app.css .rq, .ract), even when empty, so every row lines up
  const side = `<span class="rq">${l.owner_state==='waiting' ? `<span class="warn">${t('you_next')}</span>` : n ? t('n_queued', {n}) : ''}</span>`
    + `<span class="ract">${lockActs(l)}</span>`;
  return lrow({k: 'lock', key: l.name, icon: lockDot(l), name: `<span class="nm">${esc(lockName(l.name))}</span>`,
    title: lockWho(l), meta: side, cls: 'res'});
}
// what the resource's `busy` command prints (its users outside the lock), as plain text: under the row only when it
// says something (at most BUSY_LINES lines, the rest in the detail); a timeout or a failure says so
const BUSY_LINES = 6;
function busyNote(b){
  if(!b) return '';
  if(b.state==='pending') return `<span class="muted">${t('busy_pending')}</span>`;
  if(b.state==='timeout') return `<span class="warn">${t('busy_timeout')}</span>`;
  if(b.state==='error') return `<span class="bad">${t('busy_error')}</span>`;
  return b.output ? '' : `<span class="muted">${t('busy_none')}</span>`;
}
function busyText(b, max){
  const lines = String(b.output || '').split('\n'), more = max && lines.length > max ? lines.length - max : 0;
  return `<pre class="busyout">${esc((more ? lines.slice(0, max) : lines).join('\n'))}</pre>`
    + (more ? `<div class="muted busymore">${t('busy_more', {n: more})}</div>` : '')
    + (b.truncated && !more ? `<div class="muted busymore">${t('busy_cut')}</div>` : '');
}
function lockBusy(l){
  const b = l.busy;
  if(!b || b.state==='pending' || (b.state==='ok' && !b.output)) return '';
  return `<div class="busy" data-k="lock" data-key="${esc(l.name)}"><div class="busyh">${t('busy_title')}${busyNote(b) ? ` · ${busyNote(b)}` : ''}${b.since ? ` <span class="muted">· ${t('busy_since', {t: esc(whenShort(b.since))})}</span>` : ''}</div>`
    + (b.output ? busyText(b, BUSY_LINES) : '') + '</div>';
}
const renderLocks = d => d.locks.length ? sect(t('resources'), list(d.locks.map(l => lockRow(l) + lockBusy(l)).join(''))) : '';

// ---- Tú: pending decisions (answer in place) and messages with the orchestrator
const decKey = x => `${x.p}|${x.title}`;
const DEC_SEEN = new Map();   // decKey → the order a decision was first shown in
function renderYou(d){
  const unanswered = d.decisions.filter(x => !x.answer).length, asks = d.grants.filter(x => x.pending).length;
  $('badge').hidden = !(unanswered + asks); $('badge').textContent = unanswered + asks;
  renderGrants(d);
  // each decision is a card (decCardRich): title (opens the detail), the first sentence of its text, one button per
  // option and what it holds; folded once answered
  // in the order first seen: one just answered (the state lists it after the pending ones) stays in its place
  d.decisions.forEach(x => { if(!DEC_SEEN.has(decKey(x))) DEC_SEEN.set(decKey(x), DEC_SEEN.size); });
  const decs = [...d.decisions].sort((a, b) => DEC_SEEN.get(decKey(a)) - DEC_SEEN.get(decKey(b)));
  const dec = sect(t('decisions'), decs.length ? decs.map(x => card(decCardRich(x))).join('')
    : list(empty(t('nothing_pending'))), unanswered || null);
  // painted only when it changed, so an «other answer» being typed keeps its focus while the rest polls
  if($('dec').dataset.sig !== dec){ $('dec').innerHTML = dec; $('dec').dataset.sig = dec; fillFree($('dec')); }
  const msgs = SNAP.msg = [...d.messages].reverse();
  $('msgs').innerHTML = sect(t('messages'), list(msgs.map((x, i) => lrow({k: 'msg', key: i,
      icon: x.from==='owner' ? ico('out', 'var(--acc)') : ico('in'), name: `${ptag(x.p)}<span class="nm"${x.from==='owner' ? ' style="color:var(--acc)"' : ''}>${x.from==='owner' ? t('you') : t('orchestrator')}</span>`,
      title: esc(x.text), meta: `${x.from==='owner' && !x.read ? dot('var(--acc)', t('unread')) : ''}${esc(hhmm(x.time))}`})).join('') || empty(t('no_messages'))), msgs.length || null);
  // the composer writes to the product shown (not in the joint view, nor for a read-only one)
  const target = !d.joint && d.ps[0].writable;
  $('composer').hidden = !target;
}
// What a request will do, in plain words: the orchestrator's own `summary` when it gave one, else read from the
// command (`say`: a push, a release, a deploy; null when it is none of those, the title then names the group).
function grantSay(x){
  if(x.summary) return esc(x.summary);
  const k = x.say;   // read from the command by grants.say (conservative: null for anything unusual)
  if(!k) return null;
  if(k.kind === 'push') return t('grant_say_push', {what: k.refs.map(esc).join(t('grant_and')), to: esc(k.to)});
  if(k.kind === 'push_all') return t('grant_say_push_all', {to: esc(k.to)});
  if(k.kind === 'release') return t('grant_say_release', {what: esc(k.what)});
  return k.kind === 'deploy' ? t('grant_say_deploy') : null;
}
// A local ISO time in words with its day: «hoy a las 02:31», «mañana a las 02:31», else the date
function dayAt(iso){
  const m = String(iso || '').match(/^(\d{4}-\d\d-\d\d)T(\d\d:\d\d)/);
  if(!m) return '';
  const day = n => { const d = new Date(); d.setDate(d.getDate() + n); return d.toLocaleDateString('sv'); };
  const k = m[1] === day(0) ? 'day_today' : m[1] === day(-1) ? 'day_yesterday' : m[1] === day(1) ? 'day_tomorrow' : 'day_other';
  return esc(t(k, {d: m[1], h: m[2]}));
}
// the notice of a request that did not arrive («notified» as hooks_mod records it: sent:*, no:*, error, unknown)
const grantNotice = x => /^(no:|error)/.test(x.notified || '') ? t('grant_not_told') : x.notified === 'unknown' ? t('grant_told_unknown') : '';
const grantWho = x => esc(x.orchestrator || t('orchestrator'));
// «See» on the permission notice: the You tab, at the requests
function seeGrants(){ tab('you'); const e = $('grantreq'); if(e && e.scrollIntoView) e.scrollIntoView(); }
// requests to run something only the owner allows (a deploy…) that the question in the orchestrator's chat left
// unanswered: what it will do in plain words, «Yes» (the minutes it says) and «No», the command folded below; then
// what is allowed now. Nothing when the products shown have none.
function renderGrants(d){
  const asks = d.grants.filter(x => x.pending || x.orphan), live = d.grants.filter(x => !x.pending && !x.orphan);
  const btns = x => !canWrite(x.p) ? '' : x.orphan ? `<button class="sec" data-g="${esc(x.group)}" data-p="${esc(x.p)}" onclick="grantDeny(this)">${t('grant_discard')}</button>`
    : `<button data-g="${esc(x.group)}" data-p="${esc(x.p)}" data-min="${x.minutes}" onclick="grantApprove(this)">${t('grant_approve', {n: x.minutes})}</button>`
    + `<button class="sec" data-g="${esc(x.group)}" data-p="${esc(x.p)}" onclick="grantDeny(this)">${t('grant_no')}</button>`;
  $('grantreq').innerHTML = d.grants.length ? sect(t(asks.length ? 'grant_requests' : 'grants_allowed'), asks.map(x => { const say = grantSay(x); return card(
      `<div class="ctitle"><span>${ptag(x.p)}${say ? t('grant_req_say', {who: grantWho(x), say}) : t('grant_req_title', {group: esc(x.group), who: grantWho(x)})}</span></div>`
      + `<div class="csub">${x.orphan ? t('grant_orphan') + ' · ' : ''}${t('grant_req_when', {at: dayAt(x.at), until: dayAt(x.expires)})}${grantNotice(x) ? ` · <span class="warn">${grantNotice(x)}</span>` : ''}</div>`
      + `<div class="btns">${btns(x)}</div>`
      + `<details class="gcmd"><summary>${t('grant_see_cmd')}</summary><div class="mono">${esc(x.part || x.command)}</div></details>`); }).join('')
    + (live.length ? list(live.map(x => lrow({icon: dot('var(--ok)'), name: ptag(x.p), title: t('grant_live', {group: esc(x.group), until: esc(hhmmIso(x.until))}),
        meta: esc(t(x.via === 'panel' ? 'grant_via_panel' : 'grant_via_chat'))})).join('')) : ''), asks.length || null) : '';
}
async function grantDeny(b){
  let err = '';
  try{
    const r = await fetch(api(`/api/grants/deny?g=${encodeURIComponent(b.dataset.g)}&p=${encodeURIComponent(b.dataset.p)}`), {method:'POST'});
    if(!r.ok){ const j = await r.json().catch(() => ({})); err = j.why ? t(`grant_why_${j.why}`) : (j.error || `${r.status}`); }
  }catch(e){ err = t('no_connection'); }
  lastSignature = null; lastEtag = null; await load();
  if(err) $('err').textContent = t('error', {text: err});
}
async function grantApprove(b){
  if(!confirm(t('confirm_grant', {group: b.dataset.g, n: b.dataset.min}))) return;
  let err = '';
  try{
    const r = await fetch(api(`/api/grants/approve?g=${encodeURIComponent(b.dataset.g)}&p=${encodeURIComponent(b.dataset.p)}`), {method:'POST'});
    if(!r.ok){ const j = await r.json().catch(() => ({})); err = j.why ? t(`grant_why_${j.why}`) : (j.error || `${r.status}`); }
  }catch(e){ err = t('no_connection'); }
  lastSignature = null; lastEtag = null; await load();
  if(err) $('err').textContent = t('error', {text: err});
}
const decisionSent = x => x.sent ? `<div class="warn answered">${t('sent_not_closed', {text: esc(x.sent)})}</div>` : '';
const otherButton = x => canWrite(x.p) && !FREE_OPEN.has(freeKey(x)) ? `<button class="sec other" data-fk="${esc(freeKey(x))}" onclick="freeToggle(this)">${t('other_answer')}</button>` : '';
const freeBox = x => canWrite(x.p) && FREE_OPEN.has(freeKey(x)) ? `<div class="free"><textarea maxlength="2000" data-fk="${esc(freeKey(x))}" placeholder="${esc(t('other_answer_ph'))}" oninput="FREE_DRAFT.set(this.dataset.fk, this.value)"></textarea>`
      + `<div class="btns"><button data-id="${esc(x.id || '')}" data-d="${esc(x.title)}" data-p="${esc(x.p)}" data-fk="${esc(freeKey(x))}"${x.answer ? ' data-ch="1"' : ''} onclick="decideFree(this)">${t('send_answer')}</button>`
      + `<button class="sec" data-fk="${esc(freeKey(x))}" onclick="freeToggle(this)">${t('cancel')}</button></div></div>` : '';
const UNDO_CLS = {yes: 'ok', hard: 'warn', no: 'bad'};


// A question that says what each option changes is told in order: the question, its situation (the text), what is
// at stake (what happens without an answer, the reminder, the tasks it holds), then each option as a card with its
// own button, in the order written (never reordered: «A» means the same here, in the chat and in the reminder), the
// same size and style, nothing chosen beforehand, the recommended one marked with its «why»; «Other answer» after
// them and the page folded at the end. Only an option that cannot be undone asks for a confirmation.
// optLetter: the letter an option starts with («A: …» → «A»), or '' when it has none
const optLetter = o => (/^\s*([A-Za-z0-9]{1,2})\s*[:.)]\s/.exec(o) || [])[1] || '';
const UNDO_ICO = {yes: '↺', hard: '⚠︎', no: '⊘'};
const undoChip = u => UNDO_CLS[u] ? `<span class="ochip ${UNDO_CLS[u]}"><span aria-hidden="true">${UNDO_ICO[u]}</span> ${t('chip_undo_' + u)}</span>` : '';
const undoOf = (x, o) => ((x.details || {})[o] || {}).undo || '';
// an option's data for its button: data-ch when it changes an answer the orchestrator has not read yet
const optData = (x, o) => `data-id="${esc(x.id || '')}" data-d="${esc(x.title)}" data-op="${esc(o)}" data-p="${esc(x.p)}" data-undo="${esc(undoOf(x, o))}"${x.answer ? ` data-ch="1" data-fk="${esc(freeKey(x))}"` : ''}`;
const optButton = (x, o, cls='') => `<button class="${cls}" ${optData(x, o)} onclick="decideOpt(this)">${optLetter(o) ? t('dec_choose', {l: esc(optLetter(o))}) : t('dec_choose_it')}</button>`;
// «7 nov» / «Nov 7» from «2026-11-07» (the text as it is when it is not such a date)
const dayMonth = s => { const m = /^\d{4}-(\d\d)-(\d\d)$/.exec(s || ''); return m ? t('dec_day_month', {d: +m[2], m: t('months_short').split(' ')[m[1] - 1]}) : esc(s || ''); };
// a title that starts with its code («DEC J5, ¿…?», «AP-7 J2: …») → {code: 'DEC J5', text: '¿…?'}; the code is shown
// small and grey at the end of the row, not in front of the title. A title without one (or that is only a code) stays
const titleParts = x => { const m = /^((?:[A-Z]{1,6}-\d+|[A-Z]{2,6})\s+J\d+|J\d+)\s*[,:]\s*(\S.*)$/s.exec(x.title || ''); return m ? {code: m[1], text: m[2]} : {code: '', text: x.title || ''}; };
const codeTag = x => { const c = titleParts(x).code; return c ? `<span class="dcode">${esc(c)}</span>` : ''; };
// the answers being changed (their options shown again), by product and decision: kept across repaints
const CHANGE_OPEN = new Set();
function changeToggle(b){
  const k = b.dataset.fk;
  if(CHANGE_OPEN.has(k)) CHANGE_OPEN.delete(k); else CHANGE_OPEN.add(k);
  if(lastData) renderYou(lastData);
  if(MV.length) renderModal(true);
}
const changeButton = (x, key) => `<button class="sec" data-fk="${esc(freeKey(x))}" onclick="changeToggle(this)">${t(key)}</button>`;
// what he chose: «✓ You chose A: …» (or his own words)
const chosenLine = x => `<span class="ok" aria-hidden="true">✓</span> ${x.free ? t('dec_chose_free', {text: esc(x.answer)}) : t('dec_chose', {text: `<b>${esc(x.answer)}</b>`})}`;
// in the list: the title (it opens the detail) and one situation line; each option one full-width button with its
// letter and name, «Recommended» written at the end of the recommended one and ⊘ before the one that cannot be undone (which asks to
// confirm); what each changes, its cost and undo stay in the detail; a grey line with what it holds and the
// reminder's day. Answered (shown until the orchestrator reads it): folded to «✓ You chose A: …» with «Change», which
// brings the options back
function decCardRich(x){
  const w = canWrite(x.p), hs = x.holds || [], changing = x.answer && CHANGE_OPEN.has(freeKey(x));
  const head = `<div class="ctitle" data-k="dec" data-key="${esc(decKey(x))}"><span>${ptag(x.p)}${esc(titleParts(x).text)}</span>${codeTag(x)}<span class="chev">›</span></div>`;
  if(x.answer && !changing) return head + `<div class="ochosen"><span class="otxt">${chosenLine(x)}</span>${w ? `<button class="sm sec" data-fk="${esc(freeKey(x))}" onclick="changeToggle(this)">${t('dec_change')}</button>` : ''}</div>`;
  const meta = x.answer ? '' : [hs.length && tn('dec_holds', hs.length).toLowerCase(), x.unanswered && x.unanswered.by && t('dec_reminder', {date: dayMonth(x.unanswered.by)})].filter(Boolean).join(' · ');
  const line = o => { const no = undoOf(x, o)==='no', rec = o===x.recommended, cur = x.answer===o && !x.free;
    const inner = (cur ? '<span class="ok" aria-hidden="true">✓</span> ' : '') + (no ? `<span class="bad" title="${esc(t('dec_irreversible'))}">${UNDO_ICO.no}</span> ` : '')
      + `<span class="olt">${esc(o)}</span>` + (rec ? ` <span class="orec">${t('dec_rec_badge')}</span>` : '');
    return w ? `<button class="sec oline" ${optData(x, o)} onclick="decideOpt(this)">${inner}</button>` : `<div class="oline">${inner}</div>`; };
  return head + (x.lead ? `<div class="clead olead">${esc(x.lead)}</div>` : '')
    + `<div class="olines">${x.options.map(line).join('')}</div>`
    + (changing ? `<div class="btns">${changeButton(x, 'dec_keep')}</div>` : '')
    + (meta ? `<div class="csub">${meta}</div>` : '')
    + decisionSent(x);
}
// one option as a card. Answered (`done`): the chosen one with «✓ Your choice», highlighted; the others grey, the
// recommended one with only its label (no «why», no border); no buttons
function decOptCard(x, o, write, done){
  const d = (x.details || {})[o] || {}, rec = o===x.recommended, mine = !!x.answer && !x.free && x.answer===o;
  return `<div class="ocard${rec && !done ? ' rec' : ''}${done ? (mine ? ' chosen' : ' dim') : ''}">`
    + `<div class="ohead"><b>${esc(o)}</b>${mine ? `<span class="obadge ok"><span aria-hidden="true">✓</span> ${t('dec_your_choice')}</span>` : ''}${rec ? `<span class="obadge">${t('dec_rec_badge')}</span>` : ''}</div>`
    // always its five parts, empty ones too: side by side, each part lines up with the same part of the others
    + `<div class="ochips">${undoChip(d.undo)}${d.cost ? `<span class="ochip">${esc(d.cost)}</span>` : ''}</div>`
    + `<div class="oeff">${d.effect ? esc(d.effect) : ''}</div>`
    + `<div class="owhy">${rec && !done && x.recommendation ? `<b>${t('dec_why')}</b> ${esc(x.recommendation)}` : ''}</div>`
    + `<div class="ogo">${write ? optButton(x, o) : ''}</div>`
    + '</div>';
}
// the detail: pending, told in order (situation, what is at stake, the options, «Other answer», the page). Answered,
// while the orchestrator has not read it: what it was about stays, the chosen card stands out and «Change answer»
// brings the options back (with «Keep my answer»)
function decDetailRich(x){
  const u = x.unanswered || {}, hs = x.holds || [], changing = !!x.answer && CHANGE_OPEN.has(freeKey(x)), done = !!x.answer && !changing;
  const stakes = [u.waits && t('dec_if_none', {text: esc(u.waits)}), u.by && t('dec_remind_on', {date: dayMonth(u.by)})].filter(Boolean).join(' ');
  const holds = hs.length ? `<details class="dholds"><summary>${tn('dec_holds', hs.length)}</summary><ul>${hs.map(h => `<li><span class="mono">${esc(h.ref)}</span> ${esc(h.title)}</li>`).join('')}</ul></details>` : '';
  const w = canWrite(x.p), write = w && !done;
  return `<div class="md dsit">${md(x.text)}</div>`
    + (!x.answer && (stakes || holds) ? `<div class="dstake">${stakes ? `<div>${stakes}</div>` : ''}${holds}</div>` : '')
    + (x.answer ? (x.free ? `<div class="ochosen big">${chosenLine(x)}</div>` : '') : decisionSent(x))
    + `<div class="ocards n${x.options.length}">${x.options.map(o => decOptCard(x, o, write, done)).join('')}</div>`
    + (done ? (w ? `<div class="btns">${changeButton(x, 'dec_change_answer')}</div>` : '')
      : write ? `<div class="btns">${otherButton(x)}${changing ? changeButton(x, 'dec_keep') : ''}</div>${freeBox(x)}` : '')
    + (x.page ? `<details class="sect dpage" id="dpage"><summary>${t('dec_more')}</summary><div id="mpage" class="md"><span class="muted">${t('loading_lc')}</span></div></details>` : '');
}
// an option chosen: on one tap, unless it cannot be undone: then a sheet repeats what it changes and its cost, with
// «Back» (the default, focused) and «Choose C»
function decideOpt(b){
  if(b.dataset.undo !== 'no') return answerOpt(b);
  const x = lastData && lastData.decisions.find(y => y.p===b.dataset.p && y.title===b.dataset.d), o = b.dataset.op;
  const d = (x && x.details && x.details[o]) || {}, l = optLetter(o) || o;
  closeConfirm();
  document.body.insertAdjacentHTML('beforeend', `<div class="csheet" id="csheet" onclick="if(event.target===this) closeConfirm()">`
    + `<div class="cpanel" role="alertdialog" aria-modal="true" aria-labelledby="csht" aria-describedby="cshd">`
    + `<h3 id="csht"><span class="bad" aria-hidden="true">${UNDO_ICO.no}</span> ${t('dec_no_back', {l: esc(l)})}</h3>`
    + `<div id="cshd"><div><b>${esc(o)}</b></div>${d.effect ? `<p>${esc(d.effect)}</p>` : ''}${d.cost ? `<p class="muted">${esc(t('dec_cost', {text: d.cost}))}</p>` : ''}</div>`
    + `<div class="btns"><button id="csback" onclick="closeConfirm()">${t('dec_back')}</button><button id="csgo" class="sec bad">${t('dec_choose', {l: esc(l)})}</button></div></div></div>`);
  $('csgo').onclick = () => { closeConfirm(); answerOpt(b); };
  $('csback').focus();
}
function closeConfirm(){ const s = $('csheet'); if(s) s.remove(); }   // not closeSheet: that is the backlog filter's
document.addEventListener('keydown', e => { if(e.key==='Escape' && $('csheet')){ e.stopImmediatePropagation(); closeConfirm(); } }, true);
const answerOpt = b => { if(b.dataset.ch) CHANGE_OPEN.delete(b.dataset.fk);
  return post(`/api/decide?id=${encodeURIComponent(b.dataset.id)}&op=${encodeURIComponent(b.dataset.op)}${b.dataset.ch ? '&change=1' : ''}`, b.dataset.p); };
// the «other answer» boxes open and what is typed in them, by product and decision: kept across repaints
const FREE_OPEN = new Set(), FREE_DRAFT = new Map();
const freeKey = x => `${x.p}|${x.id || x.title}`;
const fillFree = root => root.querySelectorAll('.free textarea').forEach(a => { a.value = FREE_DRAFT.get(a.dataset.fk) || ''; });
function freeToggle(b){
  const k = b.dataset.fk, open = !FREE_OPEN.has(k);
  if(open) FREE_OPEN.add(k); else FREE_OPEN.delete(k);
  if(lastData) renderYou(lastData);
  if(MV.length) renderModal(true);
  if(open){ const a = [...document.querySelectorAll('.free textarea')].find(x => x.dataset.fk===k && x.offsetParent); if(a) a.focus(); }
}

// ---- Trabajo: agents, branches by column and the pull requests
const BRANCH_COLS = [['working','col_working'],['pr','col_pr'],['ready','col_ready'],['main','col_today']];
// the first line of what an agent last said, as plain text (no markdown marks), cut short
const plainLine = s => { const l = String(s || '').split('\n').map(x => x.trim()).find(Boolean) || '';
  const p = l.replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/^[#>*\-\s]+/, '').replace(/[`*_]/g, '').trim();
  return p.length > 140 ? p.slice(0, 139) + '…' : p; };
// what an agent does, in words: the title of its sub-issue (else of its issue), never the code; one without a task says
// what kind of agent it is
function agentWhat(a){
  const c = a.issue && BL.find(x => x.id===a.issue && x.p===a.p);
  const sb = c && a.sub && (c.subs || []).find(x => x.id===a.sub);
  const title = (sb && sb.title) || (c && c.title);
  if(title) return {text: title, tip: c.title !== title ? `${c.title} — ${title}` : title};
  if(a.issue) return {text: a.issue, tip: a.issue};
  const ty0 = String(a.type || '').replace(/^[\w-]+:/, ''), k = t('agent_types.' + ty0);
  const ty = ty0 && k !== 'agent_types.' + ty0 ? k : ty0 ? t('agent_type_other', {type: ty0}) : t('agent_type_none');
  return {text: ty, tip: ty};
}
// its state in words (in the dot's colour), how long ago, and the last thing it said
function agentState(a){
  const when = a.end ? '' : a.status==='maybe stuck' ? ageEl(a.activity_ts, 'idle') : ageEl(a.activity_ts, 'ago_dur');
  const last = plainLine(a.last);
  return `<span style="color:${scol(a.status)}">${esc(sname(a.status))}</span>` + [when, last && esc(last)].filter(Boolean).map(x => ` · ${x}`).join('');
}
// the progress of the last hour, from the agents and the orchestrator's notes (Trabajo and Sistema show it)
function feedThread(d){
  const since = new Date(Date.now() - 3600e3), sinceHM = `${String(since.getHours()).padStart(2,'0')}:${String(since.getMinutes()).padStart(2,'0')}`;
  const today = new Date().toLocaleDateString('sv');
  return SNAP.feed = [...d.notes.map(n => ({h: n.time, who: 'orchestrator', t: n.text, kind: 'note', p: n.p})),
      ...d.progress.filter(x => hhmm(x.time) >= sinceHM && String(x.time).slice(0,10) === today).map(x => ({h: hhmm(x.time), who: x.agent || '?', t: x.text, kind: 'progress', p: x.p}))]
    .sort((a,b) => b.h.localeCompare(a.h));
}
const lastHourHtml = thread => sect(t('last_hour'), list(thread.map((x, i) => lrow({k: 'feed', key: i, id: x.h, name: ptag(x.p) + aname(x.who), title: inline(x.t)})).join('') || empty(t('nothing_last_hour'))), thread.length || null);
// «See conversation» of a session agent (its own Claude Code session): the server turns Remote Control on for it (seconds;
// it pushes the owner's phone, so only this click does it) and answers its link, which opens in the Claude app. The
// tab is opened by the click itself (a browser blocks one opened after the wait) on the waiting page of the server.
const WATCHING = new Set();
const watchButton = a => a.session && canWrite(a.p)
  ? `<button class="sec sm" data-id="${esc(a.id)}" data-p="${esc(a.p)}" data-name="${esc(agentWhat(a).text)}" title="${esc(t('watch_tip'))}"${WATCHING.has(a.id) ? ' disabled' : ''} onclick="watchAgent(this)">${t(WATCHING.has(a.id) ? 'watch_busy' : 'watch_btn')}</button>` : '';
async function watchAgent(b){
  const id = b.dataset.id, p = b.dataset.p, name = b.dataset.name || ''; if(WATCHING.has(id)) return;
  const q = `id=${encodeURIComponent(id)}&p=${encodeURIComponent(p)}`;
  // the tab opens on the server's waiting page, which turns Remote Control on, goes to the link and says why if it fails
  if(window.open(api(`/agent/wait?${q}&name=${encodeURIComponent(name)}`), '_blank')) return;
  // a browser that blocks the tab: ask from here and show the link
  WATCHING.add(id); b.disabled = true; b.textContent = t('watch_busy'); $('err').textContent = '';
  let link = '', err = '';
  try{
    const r = await fetch(api(`/api/agent/watch?${q}`), {method:'POST'}), j = await r.json().catch(() => ({}));
    if(r.ok) link = j.link; else err = j.error || `${r.status}`;
  }catch(e){ err = t('no_connection'); }
  WATCHING.delete(id);
  lastSignature = null; lastEtag = null; await load();   // (load clears the message: it comes after)
  if(link) $('err').innerHTML = `${esc(t('watch_blocked'))}: <a href="${esc(link)}" target="_blank" rel="noopener">${esc(link)}</a>`;
  else $('err').textContent = t('error', {text: err});
}
function renderWork(d){
  const alive = d.agents.filter(a => !a.end).length;
  $('agents').innerHTML = sect(t('agents'), list(d.agents.map(a => { const w = agentWhat(a);
    return lrow({k: 'ag', key: a.id, icon: sdot(a), cls: 'two', attrs: ` title="${esc(`${a.name} · ${w.tip}`)}"`,
      title: `<span class="what">${ptag(a.p)}${esc(w.text)}</span><span class="state muted">${agentState(a)}</span>`, meta: watchButton(a)}); }).join('') || empty(t('none_m'))),
    alive ? t('n_running', {n: alive}) : null);
  $('worklast').innerHTML = lastHourHtml(feedThread(d));
  // a repository shows only what it has: branches as a kanban (with an agent, with a PR, ready, in main today) and its
  // open PRs; the ones with nothing open are one line
  const idle = [];
  $('branches').innerHTML = d.repos.map(r => {
    if(r.missing) return sect(t('branches'), list(empty(t('repo_missing', {key: esc(r.key)}))), null, esc(r.key));
    if(!r.branches.length && !r.today.length && !(r.prs && r.prs.length)){ idle.push(`${ptag(r.p)}${esc(r.key)}`); return ''; }
    const rows = c => c==='main' ? r.today.map(x => lrow({k: 'commit', key: `${r.p}|${r.key}|${x.commit}`, name: `<span class="nm mono">${esc(x.commit)}</span>`, title: `<span class="muted">${esc(x.subject.replace(/^Merge \S+\s*/, ''))}</span>`, meta: esc(x.time)}))
      : r.branches.filter(b => b.col===c).map(b => lrow({k: 'branch', key: b.branch, name: `<span class="nm mono">${esc(b.branch)}</span>`, title: `<span class="muted">${esc(b.subject)}</span>`, meta: ageEl(b.committed_ts, 'ago')}));
    const groups = BRANCH_COLS.map(([c, name]) => [c, name, rows(c)]).filter(g => g[2].length);
    return groups.length ? sect(t('branches'), list(groups.map(([c, name, rs]) =>
      group('branches', c, `${c==='main' ? ico('merged') : ico('branch', c==='working' ? 'var(--prog)' : 'var(--muted)')}<span>${t(name)}</span><span class="n">${rs.length}</span>`, rs.join(''))).join('')),
      r.branches.length || null, `${ptag(r.p)}${esc(r.key)}`) : '';
  }).join('') + (idle.length ? `<div class="empty idle">${idle.join(', ')}: ${t('nothing_open')}</div>` : '');
  $('prs').innerHTML = d.repos.filter(r => r.github && (r.prs == null || r.prs.length)).map(r => sect(t('prs'), list(r.prs == null ? empty(t('loading')) : r.prs.map(x => lrow({k: 'pr', key: `${r.p}|${r.key}|${x.number}`,
      icon: x.checks.fail ? dot('var(--bad)', t('checks', x.checks)) : ico(x.checks.pending ? 'clock' : 'check', x.checks.pending ? 'var(--warn)' : 'var(--ok)'),
      id: '#' + x.number, title: `${esc(x.title)}${x.draft ? ` <span class="ro">${t('draft')}</span>` : ''}`, meta: `<span class="mono">${esc(x.branch)}</span>`})).join('')),
    r.prs && r.prs.length || null, `${ptag(r.p)}${esc(r.github)}`)).join('');
}
// the issue an agent works on, «ID · title»
function issueLine(a){
  if(!a.issue) return '';
  const c = BL.find(x => x.id===a.issue && x.p===a.p);
  return c ? `${c.id} · ${c.title}` : a.issue;
}

// ---- Sistema: the Mac, recurring tasks, the last hour and problems of the registry
const recKey = r => `${r.p}|${r.name}`;
// the Mac's gauges and top processes: /api/projects brings them every 30 s (not /api/state, so its ETag holds)
function renderMac(){
  const m = (OVER || {}).mac;
  if(!m){ $('mac').innerHTML = ''; return; }
  const col = (v, w, b) => v >= b ? 'var(--bad)' : v >= w ? 'var(--warn)' : 'var(--ok)';
  const ring = (frac, color) => { const r=20, cc=2*Math.PI*r, f=Math.max(0,Math.min(1,frac||0));
    return `<svg width="52" height="52" viewBox="0 0 52 52"><circle cx="26" cy="26" r="${r}" fill="none" stroke="var(--chip)" stroke-width="6"/>
      <circle cx="26" cy="26" r="${r}" fill="none" stroke="${color}" stroke-width="6" stroke-linecap="round"
        stroke-dasharray="${(f*cc).toFixed(1)} ${cc.toFixed(1)}" transform="rotate(-90 26 26)"/></svg>`; };
  const g = (frac, color, v, l) => `<div class="gauge">${ring(frac,color)}<div><div class="v">${v}</div><div class="l">${l}</div></div></div>`;
  const memUsed = m.mem_free_pct==null ? null : 100 - m.mem_free_pct, loadFrac = m.load / m.ncpu;
  $('mac').innerHTML = sect(t('mac'), card(`<div class="gauges">
      ${g(memUsed/100, col(memUsed,65,80), `${memUsed ?? '?'} %`, t('mem_used', {gb: m.mem_total_gb ?? '?'}))}
      ${g(1 - m.disk_free_gb / m.disk_total_gb, col(m.disk_total_gb-m.disk_free_gb, m.disk_total_gb-100, m.disk_total_gb-50), `${m.disk_free_gb} GB`, t('disk_free', {gb: m.disk_total_gb}))}
      ${g(m.swap_total_gb ? m.swap_gb / m.swap_total_gb : 0, col(m.swap_gb,4,8), `${m.swap_gb ?? '?'} GB`, t('swap_used'))}
      ${g(loadFrac, col(loadFrac,.6,.9), `${m.load}`, t('load_cores', {n: m.ncpu}))}
    </div><div>${(m.top || []).length ? m.top.map(x => `<div class="cpu"><span class="c">${esc(x.cmd)}</span><span class="bar"><i style="width:${Math.min(100,x.cpu/m.ncpu)}%;background:${col(x.cpu,100,400)}"></i></span><span class="p">${Math.round(x.cpu)} %</span></div>`).join('')
      : `<div class="cpu muted">${t('cpu_idle')}</div>`}</div>`));
}
function renderSystem(d){
  renderMac();
  const late = d.recurring.filter(r => r.late).length;
  // an inactive product (set up, its orchestrator not started) has nothing late: its tasks show grey
  $('recurring').innerHTML = sect(t('recurring'), list(d.recurring.map(r => lrow({k: 'rec', key: recKey(r), icon: dot(r.inactive ? 'var(--muted)' : r.late ? 'var(--bad)' : 'var(--ok)'),
      name: ptag(r.p), title: esc(r.name), meta: `<span class="${r.late ? 'bad' : ''}">${r.inactive ? `${t('inactive_product')} · ` : ''}${r.parts[0].last_ts != null ? ageEl(r.parts[0].last_ts, 'ago') : esc(t('never'))}</span>`})).join('') || empty(t('none_f'))),
    late ? `<span class="bad">${tn('late_count', late)}</span>` : null);
  $('lasthour').innerHTML = lastHourHtml(feedThread(d));
  $('errs').innerHTML = d.errors.length ? sect(t('errors'), list(d.errors.map(e => lrow({cls: 'wrap bad', title: `${esc(e.text)}<div class="muted detail">${esc(t('detail_text', {text: e.detail}))}</div>`})).join(''))) : '';
  renderAbout();
}
// the foreman this panel runs (and, with --follow-installed, a new installed version it could not move to); it comes
// with the registry (/api/projects), so it is painted again whenever that is fetched
function renderAbout(){
  const fv = OVER || {};
  // every product of the registry: its title, its registry name (the id) when they differ, its orchestrator's health
  // when it is not well (also its dot), its language and mode
  $('prods').innerHTML = (fv.projects || []).length ? sect(t('products'), list(fv.projects.map(o => lrow({
    icon: dot(o.broken ? 'var(--bad)' : healthBad(o.orchestrator_health) ? `var(--${HEALTH_CLS[o.orchestrator_health.verdict]})` : 'var(--muted)'),
    title: `${esc(o.title || o.name)}${o.title && o.title !== o.name ? ` <span class="muted mono">${esc(o.name)}</span>` : ''}`,
    meta: `${healthTag(o.orchestrator_health)}${behindTag(o.name)}${langTag(o)}${esc(o.mode || '')}`})).join(''))) : '';
  // the live sessions that run an older foreman than the installed one, then how many run the installed one
  const live = fv.sessions || [], late = live.filter(e => e.behind), cur = live.length - late.length;
  $('fsess').innerHTML = live.length ? sect(t('fsess_title'), list(late.map(e => lrow({cls: 'fsess', icon: dot('var(--warn)'),
      name: `<span class="nm">${esc(pname(e.project))}</span>`, title: `<span class="mono">${esc(e.name || String(e.session).slice(0, 8))}</span>`,
      meta: `<span class="warn" title="${esc(vTip(e))}">${esc(vLabel(e))} → ${esc(vInstalled())}</span><span>· ${t('fsess_reload')}</span>`})).join('')
    + (cur && fv.foreman_installed ? empty(esc(tn('fsess_current', cur, {version: fv.foreman_installed.version}))) : '')), late.length || null) : '';
  $('fwarn').innerHTML = fv.foreman_warning ? sect(t('foreman_update'), list(lrow({cls: 'wrap bad', title: esc(fv.foreman_warning)}))) : '';
  $('about').innerHTML = fv.foreman_version ? `<div class="about muted">${t('foreman_running', {version: esc(fv.foreman_version)})}${fv.foreman_follows ? ' · ' + t('foreman_follows') : ''}<div class="path">${esc(fv.foreman_root || '')}</div></div>` : '';
}

// ---- Backlog (like Linear: project E1… > issue AB-15… > sub-issue T0, T1…; in the code a project of the
// backlog is an «epic»). Two views: «Issues» is the issue list, grouped by status in collapsible groups, one line per
// issue, in file order (the priority); «Projects» is the projects list in priority order, reordered by dragging.
// Tapping a project opens its page (a view in the modal stack) with its issues grouped by status; the active ones are
// reordered by dragging. An issue with sub-issues carries a done/total chip that folds them out under its row. The
// server moves it in the backlog file and leaves the move in the inbox.
const LANES = ['in progress','ready','refining','backlog','triage','done','canceled','duplicate','archive'];
const laneName = l => t('lanes.' + l);
const isActive = c => !['done','canceled','duplicate','archive','triage'].includes(c.lane);
// WIP targets of the product ([agents] production_slots and ready_target); none in the joint view
const wipOf = l => { if(!lastData || lastData.joint) return null; const tg = lastData.ps[0].targets || {};
  return {'in progress': tg.production_slots, 'ready': tg.ready_target}[l] ?? null; };
// issue ids, and aliases (an old id that an issue carries in its hypothesis)
const storyLinks = s => inlineF(s).replace(/\b([A-Z]{1,5}-\d+|[A-Z]\d+)\b/g, (m, id) => { const c = BL.find(x => x.id===id || (x.outcome?.aliases || []).includes(id)); return c ? `<a class="slink" data-k="h" data-key="${esc(c.id)}">${id}</a>` : m; });
// blocked: a free-text reason, or an open issue that blocks it through a relation (blocked_rel, from the server)
const isBlocked = c => !!(c.blocked || (c.blocked_rel || []).length);
const blocks = c => BL.filter(x => x.blocked && x.p===c.p && new RegExp(`\\b${c.id}\\b`).test(x.blocked));
// short epic label for one-line rows (like Linear's project label): colour dot, id and first word
const elabel = (e, ti, cls='') => `<span class="el ${cls}"><i style="background:${ecol(e)}"></i>${esc(ekey(e))} ${esc(ti.split(/[ (]/)[0].replace(/[,;:.]$/, ''))}</span>`;
function setView(v){ blView = v; lsSet('view', v); if(lastData) renderBacklog(lastData); window.scrollTo(0,0); }
// the projects in file order (also those without issues), then any other project an issue names; issues without
// project are not a project
const epicList = () => { const m = new Map(Object.entries((lastData && lastData.projects) || {}).map(([k, e]) => [k, e.title]));
  BL.forEach(c => { if(c.eid && !m.has(c.epic)) m.set(c.epic, c.epic_t); }); return [...m.entries()]; };
const epicOf = e => BL.find(c => c.epic===e) || (x => x.p ? {p: x.p, eid: x.id} : {})(projOf(e));
const projOf = e => (lastData && lastData.projects[e]) || {};
function epicStats(e){
  const all = BL.filter(c => c.epic===e && !['canceled','duplicate','archive','triage'].includes(c.lane)), done = all.filter(c => c.lane==='done').length;
  return {total: all.length, done, started: all.filter(c => c.lane==='in progress').length,
          ready: all.filter(c => c.lane==='ready' && !isBlocked(c)).length, blocked: all.filter(c => isActive(c) && isBlocked(c)).length};
}
const statsText = s => [s.started && t('n_in_progress', {n: s.started}), s.ready && t('n_ready', {n: s.ready}), s.blocked && `<span class="bad">${t('n_blocked', {n: s.blocked})}</span>`].filter(Boolean).join(' · ');
// a project's status (Linear's project statuses), or the one its issues give it (*)
const pstatus = e => e.status ? `${esc(t('project_status.' + e.status))}${e.status_derived ? '*' : ''}` : '';
const epicLink = e => { const ti = (epicList().find(([k]) => k===e) || [])[1]; return ti == null ? esc(e) : `<a class="elink" data-k="ep" data-key="${esc(e)}">${dot(ecol(e))}${esc(ekey(e))} · ${esc(ti)}</a>`; };
// one row of the projects list (Projects view): colour, name, progress, status
function epicRow(e, ti, drag){
  const s = epicStats(e), pr = projOf(e);
  return `<div class="erow" data-k="ep" data-key="${esc(e)}"${drag ? ' data-drag' : ''}>${drag ? GRIP() : ''}${dot(ecol(e))}
    <div class="main"><div class="nm">${esc(ekey(e))} · ${esc(ti)}</div><div class="sub">${progBar(s, ecol(e), 56)}<span>${s.done}/${s.total}</span>${statsText(s) ? `<span class="cnt">${statsText(s)}</span>` : ''}${pr.status && !pr.status_derived ? `<span>${pstatus(pr)}</span>` : ''}</div></div><span class="chev">›</span></div>`;
}
const progBar = (s, color, w) => `<span class="prog" style="width:${w}px"><i style="width:${s.total ? 100*s.done/s.total : 0}%;background:${color}"></i></span>`;
// goals: the WIP targets are global, so an epic page shows plain counts
function laneCount(l, cs, goals){
  const goal = wipOf(l);
  const wip = n => goals && goal != null ? `<b style="color:${n<goal?'var(--bad)':'inherit'}">${n}/${goal}</b>` : n;
  // production WIP = agents with their own branch; agents without one (plans, tools) and issues without an agent are
  // shown apart
  if(l==='in progress'){ const n = cs.filter(c => c.production).length, side = cs.filter(c => c.agent && !c.production).length, none = cs.filter(c => !c.agent).length;
    return wip(n) + (side ? ` <span class="xtra">${t('plus_no_branch', {n: side})}</span>` : '') + (none ? ` <span class="xtra">${t('plus_no_agent', {n: none})}</span>` : ''); }
  if(l==='ready'){ const n = cs.filter(c => !isBlocked(c)).length; return wip(n) + (cs.length>n ? ` <span class="xtra">${t('plus_blocked', {n: cs.length-n})}</span>` : ''); }
  return cs.length;
}
const GRIP = () => `<span class="grip" aria-label="${esc(t('drag'))}">⋮⋮</span>`;
// ---- Sub-issues (✓ done, ▶ running, nothing = to do, ✗ dropped, → moved to another issue, ? unconfirmed). Which
// issues are unfolded is remembered per viewer.
const SUB_OPEN = (() => { try { return new Set(JSON.parse(lsGet('subs', '[]'))); } catch(e) { return new Set(); } })();
const SUB_STATE = {'todo': 'todo', 'in progress': 'doing', 'done': 'done', 'canceled': 'dropped', 'moved': 'moved', 'unknown': 'unknown'};
const subSt = s => SUB_STATE[s.status] || 'unknown';
const subName = st => t('sub_status.' + st);
function subIcon(st){
  const base = `<svg class="sico" width="14" height="14" viewBox="0 0 14 14" aria-label="${esc(subName(st))}">`;
  if(st==='moved') return base+`<circle cx="7" cy="7" r="5.5" fill="none" stroke="var(--muted)" stroke-width="1.3"/><path d="M4.5 7h5M7.5 4.8 9.7 7 7.5 9.2" fill="none" stroke="var(--muted)" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  if(st==='unknown') return base+`<circle cx="7" cy="7" r="5.5" fill="none" stroke="var(--warn)" stroke-width="1.3" stroke-dasharray="2 2"/><text x="7" y="9.6" text-anchor="middle" font-size="7.5" font-weight="700" fill="var(--warn)">?</text></svg>`;
  return sico({done:'done', doing:'in progress', todo:'ready', dropped:'canceled'}[st] || 'backlog');
}
const subLive = c => (c.subs || []).filter(s => !['dropped', 'moved'].includes(subSt(s)));
const subCount = c => `${subLive(c).filter(s => subSt(s)==='done').length}/${subLive(c).length}`;
function subRef(s){
  const r = s.ref || '';
  let h = !r ? '' : s.doc ? `<a class="dl" data-doc="${esc(s.doc)}">${esc(r.replace(/\.md$/, ''))}</a>`
    : /^[0-9a-f]{7,12}$/.test(r) ? `<code>${esc(r)}</code>`
    : /^[A-Z]{1,5}-\d+/.test(r) ? storyLinks(r)
    : (lastData.agents || []).some(a => a.name===r) ? agentLink(r)
    : lastData.repos.some(x => x.branches.some(b => b.branch===r)) || BL.some(c => c.branch===r) ? branchLink(r) : esc(r);
  return h + (s.note ? ` <span class="muted">${storyLinks(s.note)}</span>` : '');
}
const subRows = c => `<div class="subs" data-of="${esc(c.id)}">` + c.subs.map(s => `<div class="srow st-${subSt(s)}">${subIcon(subSt(s))}`
  + `<span class="id">${esc(s.id)}</span><span class="tt">${esc(s.title || '') || `<span class="muted">${subName(subSt(s))}</span>`}</span>`
  + `<span class="meta">${subRef(s)}</span></div>`).join('') + '</div>';
const subToggle = c => (c.subs || []).length ? `<button class="subt${SUB_OPEN.has(c.id) ? ' on' : ''}" aria-label="${esc(t('subs'))}" aria-expanded="${SUB_OPEN.has(c.id)}" onclick="toggleSubs('${esc(c.id)}')"><span class="car">▾</span>${subCount(c)}</button>` : '';
function toggleSubs(id){
  SUB_OPEN.has(id) ? SUB_OPEN.delete(id) : SUB_OPEN.add(id); lsSet('subs', JSON.stringify([...SUB_OPEN]));
  if(lastData){ renderBacklog(lastData); $('mbody').dataset.sig = ''; renderModal(true); }
}
// «I want to do it»: the owner asks the orchestrator to take a triage issue in (/api/want); «I no longer want it» undoes
// it while the issue is open; «I don't want it» cancels a triage issue, after a confirm (/api/reject). Only where the
// panel may write and the product's orchestrator runs
const isOpen = c => !['done','canceled','duplicate','archive'].includes(c.lane);
function wantButton(c){
  if(!canWrite(c.p) || proj(c.p).mode!=='active') return '';
  const on = !c.wanted && c.lane==='triage', off = c.wanted && isOpen(c);
  if(!on && !off) return '';
  const rej = c.lane==='triage' ? `<button class="sec" data-id="${esc(c.id)}" data-p="${esc(c.p)}" onclick="reject(this)">${t('reject_btn')}</button>` : '';
  return `<div class="btns"><button class="${on ? '' : 'sec'}" data-id="${esc(c.id)}" data-p="${esc(c.p)}" data-on="${on ? 1 : 0}" onclick="want(this)">${t(on ? 'want_btn' : 'unwant_btn')}</button>${rej}</div>`
    + (on ? `<div class="whint">${t('want_hint')}</div>` : '');
}
// the issue's detail does not follow each poll (it keeps its scroll and open plan): after the write it repaints
async function want(b){
  b.disabled = true;
  await post(`/api/want?id=${encodeURIComponent(b.dataset.id)}&on=${b.dataset.on}`, b.dataset.p);
  $('mbody').dataset.sig = ''; renderModal(true);
}
async function reject(b){
  if(!confirm(t('confirm_reject', {id: b.dataset.id}))) return;
  b.disabled = true;
  await post(`/api/reject?id=${encodeURIComponent(b.dataset.id)}`, b.dataset.p);
  $('mbody').dataset.sig = ''; renderModal(true);
}
// in the triage group, the issues the owner asked for carry a small tag
const WANT_TAG = c => c.wanted && c.lane==='triage' ? `<span class="wtag">${t('wanted_tag')}</span>` : '';
const storyRow = (c, drag, withEpic) => lrow({k: 'h', key: c.id, lead: (drag ? GRIP() : '') + PRIO_ICON(c.priority), icon: isBlocked(c) ? BLOCK_ICON() : sico(c.lane), id: c.id,
  title: esc(c.title), attrs: drag ? ' data-drag' : '', cls: 'story' + (drag ? ' drag' : ''),
  meta: subToggle(c) + WANT_TAG(c) + (withEpic && c.eid ? elabel(c.epic, c.epic_t) : '') + (c.agent ? dot(acol(c.agent), c.agent) : '') + (c.plan.length ? PLAN_ICON() : '')})
  + ((c.subs || []).length && SUB_OPEN.has(c.id) ? subRows(c) : '');
// status groups; scope 'all' (global list, project label on each row, no dragging) or the project id (its page)
function laneGroups(cards, scope){
  const page = scope!=='all';
  return list(LANES.map(l => {
    const cs = cards.filter(c => c.lane===l), older = cs.filter(c => c.stub).length;
    if(!cs.length && wipOf(l) == null) return '';
    // the header counts every card; the older closed ones (stubs) are a button at the end that loads them all
    return group(scope, l, `${sico(l)}<span>${laneName(l)}</span><span class="n">${laneCount(l, cs, !page)}</span>`,
      cs.filter(c => !c.stub).map(c => storyRow(c, page && isActive(c) && cs.length > 1 && canWrite(c.p), !page)).join('')
      + (older ? lrow({icon: ico('clock'), title: esc(tn('show_older', older)), cls: 'older', attrs: ' role="button" onclick="showOlder(this)"'}) : ''), t('nothing_in_lane'));
  }).join(''));
}
// a backlog.json that cannot be read is not an empty backlog: its error, in plain words, with the file's own text apart
const blBroken = d => d.ps.filter(p => p.backlog_error).map(p => card(`<div class="headline"><div class="ico" style="background:var(--bad)">!</div><div style="min-width:0"><div class="t">${esc(t('backlog_unreadable', {name: esc(p.title || p.name)}))}</div><div class="s">${esc(t('backlog_unreadable_hint'))}</div></div></div><div class="wrap muted brokenerr">${esc(t('detail_text', {text: p.backlog_error}))}</div>`)).join('');
function renderBacklog(d){
  BL = d.backlog;
  document.querySelectorAll('#blseg button').forEach(b => b.classList.toggle('on', b.dataset.v===blView));
  const epics = epicList(), et = epics.find(([e]) => e===epic);
  if(blView==='projects'){
    $('filter').innerHTML = '';
    $('board').innerHTML = blBroken(d) + list(epics.map(([e, ti]) => epicRow(e, ti, canWrite(epicOf(e).p) && !d.joint)).join('') || (d.ps.some(p => p.backlog_error) ? '' : empty(t('no_backlog'))), ' id="elist"')
      + (d.ps.some(p => p.writable) && !d.joint ? `<div class="hint">${t('hint_projects')}</div>` : '');
  } else {
    // the filter: a project, or «no project»
    const fe = epic, loose = fe==='-';
    const chipTxt = loose ? esc(t('no_project')) : et ? `${esc(ekey(fe))} · ${esc(et[1].split(/[ (]/).slice(0,3).join(' '))}` : '';
    $('filter').innerHTML = `<button class="sec sm" onclick="openSheet()">${t('project_filter', {name: chipTxt ? '' : t('all')})} ▾</button>${chipTxt ? `<span class="el wide" onclick="showEpic('')"><i style="background:${loose ? 'var(--muted)' : ecol(fe)}"></i>${chipTxt} ✕</span>` : ''}`;
    const keep = c => !epic || (loose ? !c.eid : c.epic===fe);
    $('board').innerHTML = blBroken(d) + (BL.length ? laneGroups(BL.filter(keep), 'all') : d.ps.some(p => p.backlog_error) ? '' : list(empty(t('no_backlog'))));
  }
  // a move opens the issue or project it moved
  $('moves').innerHTML = (d.moves||[]).length ? sect(t('recent_moves'), list(d.moves.map(m => lrow({k: BL.some(c => c.id===m.move) ? 'h' : 'ep', key: d.joint && !BL.some(c => c.id===m.move) ? `${m.p}·${m.move}` : m.move,
      icon: ico('clock'), title: ptag(m.p) + moveText(m, true), meta: `${esc(String(m.time || '').slice(11))} · ${m.read ? t('seen') : t('not_seen')}`})).join(''))) : '';
  // the filter sheet: every project, and «no project» when there are such issues
  const fsrow = (key, icon, title) => lrow({icon, title, cls: key===epic ? 'on' : '',
    attrs: ` onclick="showEpic('${esc(key)}');closeSheet()" style="cursor:pointer"`});
  $('sheetlist').innerHTML = list([['', t('all_projects')]].concat(epics).map(([e, ti]) => {
      const s = e ? epicStats(e) : {total: BL.length, done: BL.filter(c => !isActive(c)).length};
      return lrow({icon: dot(e ? ecol(e) : 'var(--muted)'), title: `${e ? esc(ekey(e))+' · ' : ''}${esc(ti)}`, cls: e===epic ? 'on' : '',
        meta: `${progBar(s, e ? ecol(e) : 'var(--acc)', 44)}${s.done}/${s.total}`, attrs: ` onclick="showEpic('${esc(e)}');closeSheet()" style="cursor:pointer"`}); }).join('')
    + (BL.some(c => !c.eid) ? fsrow('-', dot('var(--muted)'), esc(t('no_project'))) : ''));
}
function openSheet(){ $('sheet').hidden = false; }
function closeSheet(){ $('sheet').hidden = true; }
function showEpic(e){ epic = e; lsSet('project_filter', e); if(lastData) renderBacklog(lastData); }

// ---- Detail modal: one view per kind of row, in a stack with history.pushState (back = go back). Each view returns
// {title, html} or null when its object is gone; the live ones repaint with the data (keeping the scroll).
const MV = [];   // views: {k, key, obj?, p?}
const DOCS = {};
// a mark of «I want to do it» (want: on|off) or a discard (want: reject) is said with the panel's own words; other
// moves with their sentence
const moveText = (m, withId) => m.want ? `${withId ? `${esc(m.move)}: ` : ''}${t({on: 'want_on_act', off: 'want_off_act', reject: 'reject_act'}[m.want])}` : esc(m.text);
const activityHtml = acts => acts.length ? acts.map(m => `<div class="tline"><span class="muted">${esc(String(m.time || '').slice(5, 16).replace('-', '/'))}</span> ${moveText(m)} <span class="muted">· ${m.read ? t('seen') : t('not_seen')}</span></div>`).join('')
  : `<div class="muted">${t('no_moves')}</div>`;
const storyLink = id => `<a class="slink" data-k="h" data-key="${esc(id)}">${esc(id)}</a>`;
const agentLink = n => { const a = lastData.agents.find(x => x.name===n); return a ? `<a class="slink" style="color:${acol(n)}" data-k="ag" data-key="${esc(a.id)}">${esc(n)}</a>` : `<b style="color:${acol(n)}">${esc(n)}</b>`; };
// a row of an issue's «Documents»: what it is (plan, report of T1, review of T1 round 2 with its open findings…) and its file
const docRow = d => `<a data-doc="${esc(d.doc)}"><span class="muted">${esc(t('doc_' + d.kind, {sub: d.sub || '', id: d.id || '', round: d.round || '', open: d.open ?? ''}))}${d.kind==='review' && d.open!=null ? ' · ' + esc(t('doc_open', {n: d.open})) : ''}</span> ${esc(d.doc.split('/').pop())}</a>`;
const repDoc = r => typeof r === 'string' ? r : r.doc;
const VIEWS = {
  ep(key){
    const ti = (epicList().find(([e]) => e===key) || [])[1]; if(ti==null) return null;
    const s = epicStats(key), e0 = epicOf(key), acts = (lastData.activity || []).filter(m => m.p===e0.p && (m.move===e0.eid || BL.some(c => c.id===m.move && c.epic===key)));
    const drag = canWrite(e0.p), pr = projOf(key);
    return {p: e0.p, title: `${ekey(key)} · ${ti}`, html: dhead(t('project'), `${dot(ecol(key))}${esc(ekey(key))} · ${esc(ti)}`)
      + `<div class="esum">${progBar(s, ecol(key), 90)}<span>${t('n_done', {n: s.done, total: s.total})}</span>${statsText(s) ? `<span>· ${statsText(s)}</span>` : ''}</div>`
      + props([
        [t('status'), pstatus(pr) + (pr.status_derived ? ` <span class="muted">${t('derived')}</span>` : '')],
        pr.priority && pr.priority!=='none' && [t('priority_lbl'), esc(t('priorities.' + pr.priority))],
        (pr.repos || []).length && [t('teams'), esc(pr.repos.join(', '))],
      ], 'tight')
      + laneGroups(BL.filter(c => c.epic===key), key)
      + (drag ? `<div class="hint">${t('hint_issues')}</div>` : '')
      + sect(t('activity'), activityHtml(acts.slice(0, 10)))};
  },
  h(key){
    let c = BL.find(x => x.id===key); if(!c) return null;
    if(c.short){   // a closed issue comes short in the backlog: its whole card loads once, with a line while it does
      const x = fullCard(c);
      if(!x || x.err) return {p: c.p, title: c.title ? `${c.id} · ${c.title}` : c.id, html: `<div class="sid">${ptag(c.p)}${esc(c.id)}</div><h1 class="stt">${esc(c.title)}</h1>`
        + props([[t('status'), `${sico(c.lane)} ${esc(laneName(c.lane))}`]]) + (x ? `<div class="bad">${esc(x.err)}</div>` : `<div class="muted">${t('loading_lc')}</div>`)};
      c = {...c, ...x.issue, short: false};
    }
    const docs = [...c.plan, ...(c.reports || []).map(repDoc)];
    const det = md(c.detail).replace(/<code>([\w./-]+\.md)<\/code>/g, (m,n) => { const r = docs.find(x => x && x.endsWith('/'+n.split('/').pop())); return r ? `<a class="dl" data-doc="${esc(r)}">${n}</a>` : m; });
    const sib = BL.filter(x => x.epic===c.epic && isActive(x)), k = sib.findIndex(x => x.id===c.id);
    const by = blocks(c), oc = c.outcome, plan = c.plan[0], rel = c.relations || {};
    const relLink = (id, icon) => { const x = BL.find(y => y.id===id && y.p===c.p); return `<div>${icon ? BLOCK_ICON() + ' ' : ''}${storyLink(id)} ${x ? esc(x.title) : ''}</div>`; };
    return {p: c.p, title: `${c.id} · ${c.title}`, html: `<div class="sid">${ptag(c.p)}${esc(c.id)}</div><h1 class="stt">${esc(c.title)}</h1>` + props([
        [t('status'), `${sico(c.lane)} ${esc(laneName(c.lane))}`],
        [t('project'), c.eid ? `<a class="elink" data-k="ep" data-key="${esc(c.epic)}">${dot(ecol(c.epic))}${esc(ekey(c.epic))} · ${esc(c.epic_t)}</a>` : `<span class="muted">${t('no_project')}</span>`],
        [t('priority'), isActive(c) ? t('rank', {k: k+1, n: sib.length, epic: esc(ekey(c.epic))}) : `<span class="muted">${c.lane==='done' ? t('done_lc') : c.lane==='archive' ? t('archived_lc') : esc(laneName(c.lane).toLowerCase())}</span>`],
        [t('agent'), c.agent ? `${dot(scol(c.agent_status))}${agentLink(c.agent)}${c.agent_status ? ` · ${esc(sname(c.agent_status))}` : ''}` : c.no_agent ? `<span class="bad">${t('no_agent_nor_branch')}</span>` : ''],
        [t('branch'), c.branch ? branchLink(c.branch) : ''],
        [t('cost'), c.cost ? esc(c.cost) : ''],
        [t('blocked_by'), c.blocked ? `${BLOCK_ICON()} ${storyLinks(c.blocked)}` : ''],
        [t('blocks'), by.concat((rel.blocks || []).map(id => BL.find(x => x.id===id && x.p===c.p) || {id, title: ''}).filter(x => !by.includes(x))).map(x => `<div>${storyLink(x.id)} ${esc(x.title)}</div>`).join('')],
        (rel.blocked_by || []).length && [t('blocked_by_issues'), rel.blocked_by.map(id => relLink(id, (c.blocked_rel || []).includes(id))).join('')],
        (rel.related || []).length && [t('related'), rel.related.map(id => relLink(id)).join('')],
        rel.duplicate_of && [t('duplicate_of'), relLink(rel.duplicate_of)],
        (rel.duplicates || []).length && [t('duplicates'), rel.duplicates.map(id => relLink(id)).join('')],
        c.priority && c.priority!=='none' && [t('priority_lbl'), esc(t('priorities.' + c.priority))],
        c.assignee && [t('assignee'), agentLink(c.assignee)],
        c.repo && [t('team'), esc(c.repo)],
        [t('github'), c.github ? esc(c.github) : ''],
        c.wanted && [t('wanted_lbl'), t('wanted_since', {when: esc(whenShort(c.wanted))})],
      ]) + wantButton(c)
      // an issue is hypothesis + plan + reports + result, top to bottom
      + (oc && (oc.claim || oc.criterion) ? sect(t('hypothesis'), `<div class="md">${oc.claim ? `${storyLinks(oc.claim)} · ` : ''}${oc.criterion ? `<span class="muted">${t('criterion_lc')}</span> ${storyLinks(oc.criterion)}` : ''}</div>`, null, (oc.aliases || []).join(', ')) : '')
      + (plan ? sect(t('plan'), `<a class="dl" data-doc="${esc(plan)}">${esc(plan.split('/').pop())}</a><details class="sect"><summary>${t('read_here')}</summary><div id="mplan"><span class="muted">${t('loading_lc')}</span></div></details>`) : '')
      + ((c.docs||[]).length ? sect(t('documents'), `<div class="dlist">${c.docs.map(docRow).join('')}</div>`, c.docs.length) : '')
      + (c.result || oc?.verdict ? sect(t('result'), `<div class="md">${oc?.verdict ? `<b>${esc(oc.verdict)}</b>${c.result ? ': ' : ''}` : ''}${c.result ? storyLinks(c.result) : ''}</div>`) : '')
      + ((c.subs || []).length ? sect(t('subs'), list(subRows(c)), subCount(c)) : '')
      + sect(t('description'), `<div class="md">${det}</div>`)
      + sect(t('activity'), activityHtml((lastData.activity || []).filter(m => m.p===c.p && m.move===c.id))),
      after: async v => { if(!plan) return; const r = await loadDoc(plan, c.p); const el = $('mplan'); if(el && MV[MV.length-1]===v) el.innerHTML = r.err ? `<span class="bad">${esc(r.err)}</span>` : mdFull(r.md, c.p, r.path); }};
  },
  ag(key){
    const a = lastData.agents.find(x => x.id===key); if(!a) return null;
    const c = a.issue && BL.find(x => x.id===a.issue && x.p===a.p), own = lastData.progress.filter(x => x.agent===a.name && x.p===a.p);
    return {p: a.p, title: a.name, html: dhead(`${ptag(a.p)}${t('agent')}`, `<span style="color:${acol(a.name)}">${esc(a.name)}</span>`) + props([
        [t('status'), `${sdot(a)} <span style="color:${scol(a.status)}">${esc(sname(a.status))}</span>`],
        [t('issue'), a.issue ? (c ? `${storyLink(c.id)} ${esc(c.title)}` : esc(a.issue)) : ''],
        !a.issue && [t('task'), esc(a.task)],
        [t('running_for'), `${ageEl(a.start_ts)}${a.start_ts != null && !a.end ? ` <span class="bad"${sinceS(a.start_ts)>7200 ? '' : ` data-turn="${+a.start_ts + 7200}" hidden`}>${t('over_2h')}</span>` : ''}`],
        [t('activity'), a.end ? t('finished_lc') : ageEl(a.activity_ts, 'ago_dur')],
        [t('context'), a.context ? t('tokens', {n: ktok(a.context)}) : ''],
        !a.end && [t('tool'), esc(a.tool)],
        a.outcome && [t('delivery_outcome'), `<span class="${a.outcome === 'ok' ? '' : 'bad'}">${esc(t('outcome_' + a.outcome))}</span>`],
        a.session && [t('watch_link'), `${a.link ? `<a href="${esc(a.link)}" target="_blank" rel="noopener">${esc(a.link)}</a>` : `<span class="muted">${t('never')}</span>`} ${watchButton(a)}`],
        [t('branch'), a.branch ? branchLink(a.branch) : ''],
        [t('last_commit'), a.commit ? `<code>${esc(a.commit.split(' ')[0])}</code> ${esc(a.commit.split(' ').slice(1).join(' '))}` : ''],
        a.commit && [t('uncommitted'), a.uncommitted ? t('n_files', {n: a.uncommitted}) : t('nothing')],
        a.worktree && [t('worktree'), `<code>${esc(a.worktree.split('/').pop())}</code>`],
      ])
      + sect(t('progress'), own.length ? own.map(x => `<div class="tline"><span class="muted">${esc(hhmm(x.time))}</span> ${inline(x.text)}</div>`).join('') : `<div class="muted">${t('none_m')}</div>`, own.length || null)
      + (a.final ? sect(t('final'), `<div class="box pre">${esc(a.final)}</div>`) : a.last ? sect(t('last_text'), `<div class="box pre">${esc(a.last)}</div>`) : '')};
  },
  branch(key){
    const repo = lastData.repos.find(x => x.branches.some(b => b.branch===key)), r = repo && repo.branches.find(b => b.branch===key);
    const a = lastData.agents.find(x => x.branch===key && !x.end), c = BL.find(x => x.branch===key);
    if(!r && !a && !c) return null;
    const col = r ? t(Object.fromEntries(BRANCH_COLS)[r.col]) : '';
    return {p: repo && repo.p, title: key, html: dhead(repo ? t('branch_of', {repo: esc(repo.key)}) : t('branch'), esc(key), '', 'mono') + props([
        [t('status'), r ? `${ico('branch')} ${esc(col)}` : `<span class="muted">${t('no_commits_off_main')}</span>`],
        r && [t('last_commit'), ageEl(r.committed_ts, 'ago')],
        r && r.pr && [t('pr'), `#${r.pr}`],
        [t('agent'), a ? `${sdot(a)}${agentLink(a.name)} · ${esc(sname(a.status))}` : ''],
        [t('issue'), c ? `${storyLink(c.id)} ${esc(c.title)}` : ''],
        r && r.worktree && [t('worktree'), `<code>${esc(r.worktree.split('/').pop())}</code>`],
      ]) + (r ? sect(t('last_commit_msg'), `<div class="box pre">${esc(r.subject)}</div>`) : '')};
  },
  commit(key){
    const [p, rk, h] = key.split('|'), repo = lastData.repos.find(x => x.p===p && x.key===rk), x = repo && repo.today.find(y => y.commit===h);
    if(!x) return null;
    return {p, title: h, html: dhead(t('in_main_today', {repo: esc(rk)}), esc(h), ico('merged'), 'mono') + props([[t('time'), esc(x.time)]])
      + sect(t('merge'), `<div class="box pre">${esc(x.subject)}</div>`)};
  },
  pr(key){
    const [p, rk, n] = key.split('|'), repo = lastData.repos.find(x => x.p===p && x.key===rk), x = repo && (repo.prs || []).find(y => String(y.number)===n);
    if(!x) return null;
    const write = canWrite(p);
    return {p, title: `#${x.number}`, html: dhead(t('pr_of', {repo: esc(repo.github)}), `#${x.number} ${esc(x.title)}`) + props([
        [t('branch'), branchLink(x.branch)],
        [t('checks_label'), x.checks.fail ? `<span class="bad">${t('checks', x.checks)}</span>` : t('checks', x.checks)],
        x.review && [t('review'), esc(x.review)],
        x.draft && [t('status'), t('draft')],
        x.url && [t('link'), `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.url)}</a>`],
      ]) + (write ? `<div class="btns"><button onclick="approve('${esc(p)}','${esc(rk)}','${esc(x.branch)}',${x.number})">${t('approve')}</button></div>` : '')};
  },
  lock(key){
    const l = lastData.locks.find(x => x.name===key); if(!l) return null;
    const mine = lockMine(l), h0 = l.holders[0];
    const status = !l.holders.length ? `${dot('var(--ok)')} <b class="ok">${t('lock_free')}</b>`
      : `${lockDot(l)} <b>${mine ? t('you_lc') : esc(l.holders.map(h => h.who).join(', '))}</b>`;
    return {title: lockName(l.name), html: dhead(t('resource'), `${lockDot(l)}${esc(lockName(l.name))}`) + props([
        [t('status'), status],
        h0 && [t('what'), esc(l.holders.map(h => lockWhat(h.what) + (h.slot ? ` [${h.slot}]` : '')).join('; '))],
        h0 && [t('since'), [esc(hhmmIso(h0.since)), ageEl(h0.since_ts, 'ago_dur')].filter(Boolean).join(' · ')],
        mine && [t('until'), l.owner_expires ? `${esc(l.owner_expires)} <span class="muted">${t('auto_release')}</span>` : t('no_limit')],
        l.owner_state==='waiting' && [t('you'), `<span class="warn">${t('you_wait')}</span>`],
        l.next && [t('turn'), `<b>${esc(l.next)}</b>`],
        l.slots.length && [t('slots'), esc(l.slots.join(', '))],
        [t('declared_by'), esc(l.projects.map(pname).join(', '))],
        !lockHere(l) && [t('take'), `<span class="muted">${l.takeable ? t('take_in', {p: esc(l.takers.map(pname).join(', '))}) : t('not_takeable')}</span>`],
      ])
      + (l.busy ? sect(t('busy_title'), `<div class="busyd">${busyNote(l.busy) ? `<div>${busyNote(l.busy)}</div>` : ''}${l.busy.output ? busyText(l.busy) : ''}${l.busy.since ? `<div class="muted busymore">${t('busy_since', {t: esc(whenShort(l.busy.since))})}</div>` : ''}</div>`) : '')
      + sect(t('queue'), list(l.queue.map(q => lrow({icon: dot('var(--warn)'), name: `<span class="nm">${esc(q.who)}</span>`,
          title: `<span class="muted">${esc(q.what)}${q.short ? ` · ${t('short_job')}` : ''}</span>`, meta: ageEl(q.since_ts, 'waits')})).join('') || empty(t('nobody'))), l.queue.length || null)
      + (lockActs(l) ? `<div class="btns">${lockActs(l).replace(/class="sm /g, 'class="')}</div>` : '')};
  },
  rec(key){
    const r = lastData.recurring.find(x => recKey(x)===key); if(!r) return null;
    return {p: r.p, title: r.name, html: dhead(`${ptag(r.p)}${t('recurring_task')}`, `${dot(r.late ? 'var(--bad)' : 'var(--ok)')}${esc(r.name)}`) + props([
        [t('status'), r.inactive ? `<span class="muted">${t('inactive_product')}</span>` : r.late ? `<span class="bad">${t('late_lc')}</span>` : `<span class="ok">${t('on_time')}</span>`],
        [t('every'), esc(r.every)],
        ...r.parts.map(x => [esc(x.label ? x.label[0].toUpperCase() + x.label.slice(1) : t('last')), `<span class="${x.late ? 'bad' : ''}">${x.last ? `${esc(x.last)} · ${ageEl(x.last_ts, 'ago')}` : t('never')}</span>`])])};
  },
  dec(key){
    const x = lastData.decisions.find(y => decKey(y)===key); if(!x) return null;
    return {p: x.p, title: t('decision'), html: dhead(`${ptag(x.p)}${t(x.answer ? 'dec_answered_head' : 'pending_decision')}`, esc(titleParts(x).text) + codeTag(x)) + decDetailRich(x),
      after: async v => {
        fillFree($('mbody'));
        if(!x.page) return;
        const r = await loadDoc(x.page, x.p), el = $('mpage');
        if(!el || MV[MV.length-1]!==v) return;
        // folded under «See more detail», without its first title (the question says it already)
        el.innerHTML = r.err ? `<span class="bad">${esc(r.err)}</span>` : mdFull(r.md.replace(/^\s*# [^\n]*\n+/, ''), x.p, r.path);
      }};
  },
  msg(key, obj){
    const x = obj; if(!x) return null;
    return {p: x.p, title: t('message'), html: dhead(`${ptag(x.p)}${t('message')}`, x.from==='owner' ? t('msg_yours') : t('msg_from_orch')) + props([
        [t('time'), esc(x.time)], x.from==='owner' && [t('status'), x.read ? t('read_lc') : `<span class="warn">${t('unread')}</span>`]])
      + `<div class="box pre">${esc(x.text)}</div>`};
  },
  feed(key, obj){
    const x = obj; if(!x) return null;
    const a = x.who!=='orchestrator' && lastData.agents.find(y => y.name===x.who && y.p===x.p);
    return {p: x.p, title: x.who, html: dhead(x.kind==='note' ? t('orch_note') : t('progress_one'), `<span style="color:${acol(x.who)}">${esc(x.who==='orchestrator' ? t('orchestrator_lc') : x.who)}</span>`)
      + props([[t('time'), esc(x.h)], a && [t('agent'), agentLink(x.who)]])
      + `<div class="box">${inline(x.t)}</div>`};
  },
};
const branchLink = r => `<a class="slink mono" data-k="branch" data-key="${esc(r)}">${esc(r)}</a>`;
const LIVE = new Set(['ep', 'ag', 'branch', 'commit', 'pr', 'rec', 'dec', 'lock']);   // repainted with each poll

// ---- The whole card of a closed issue (/api/issue), by product and id: {issue} or {err}; null while it loads. Kept
// until the backlog changes; a failed load is tried again when the issue is opened again
let FULL = {}, FULL_GEN = 0;
function clearFull(){ FULL = {}; FULL_GEN++; }
function fullCard(c){
  const k = `${c.p}|${c.id}`;
  if(!(k in FULL)){ FULL[k] = null; loadIssue(c.p, c.id, k); }
  return FULL[k];
}
async function loadIssue(p, id, k){
  const gen = FULL_GEN; let x;
  try{ const r = await fetch(api(`/api/issue?p=${encodeURIComponent(p)}&id=${encodeURIComponent(id)}`));
       x = r.ok ? {issue: (await r.json()).issue} : {err: t('issue_failed', {id, text: r.status})}; }
  catch(e){ x = {err: t('issue_failed', {id, text: t('no_connection')})}; }
  if(gen !== FULL_GEN) return;   // the backlog changed meanwhile: what it brought may be stale
  FULL[k] = x;
  const v = MV[MV.length-1]; if(v && v.k==='h' && v.key===id) renderModal(true);
}
function pushView(v){ MV.push(v); history.pushState({n:MV.length}, ''); renderModal(); }
function openView(k, key, more){ if(k==='h') for(const x in FULL) if(FULL[x] && FULL[x].err) delete FULL[x];
  pushView({k, key, obj: SNAP[k] ? SNAP[k][key] : undefined, ...more}); }
// the product a document or wiki page belongs to: that of the view it was opened from, else the one shown
const viewP = () => { for(let i = MV.length - 1; i >= 0; i--) if(MV[i].p) return MV[i].p; return lastData && lastData.ps[0] ? lastData.ps[0].name : ''; };
function openDoc(r){ pushView({k:'doc', key:r, p: viewP()}); }
function closeModal(){ if(MV.length) history.go(-MV.length); }
window.addEventListener('popstate', e => { MV.length = Math.min(MV.length, (e.state && e.state.n) || 0); renderModal(); });
window.addEventListener('keydown', e => { if(e.key==='Escape' && $('pal').hidden && MV.length) closeModal(); });
document.addEventListener('click', e => {
  if(!e.target.closest) return;
  const a = e.target.closest('[data-doc]'); if(a){ e.preventDefault(); if(!$('pal').hidden) closePalette(); openDoc(a.dataset.doc); return; }
  const w = e.target.closest('[data-wiki]');
  if(w){ e.preventDefault(); if(!$('pal').hidden) closePalette(); openWiki(w.dataset.wiki, w.dataset.anchor, w.dataset.hl); return; }
  const an = e.target.closest('[data-anchor]'); if(an){ e.preventDefault(); scrollToAnchor(an.dataset.anchor); return; }
  const wt = e.target.closest('[data-wtab]');
  if(wt){ e.preventDefault(); const g = wt.dataset.wtab; tab('wiki');
    if(g){ FOLD['wiki:'+g] = false; lsSet('folded', JSON.stringify(FOLD)); renderWiki().then(() => $('wtree').querySelector(`[data-lane="${g}"]`)?.closest('.grp')?.scrollIntoView()); }
    return; }
  // a row, link or button with data-k opens its detail (a row only if the tap was not on a control inside it)
  const r = e.target.closest('[data-k]');
  if(r && (r.matches('button, a') || !e.target.closest('button, a, .grip, details'))){ e.preventDefault(); openView(r.dataset.k, r.dataset.key); }
});
async function renderModal(keepScroll){
  const v = MV[MV.length-1]; $('modal').hidden = !v; document.body.style.overflow = v ? 'hidden' : '';
  if(!v) return;
  $('mback').style.display = MV.length > 1 ? '' : 'none';
  const body = $('mbody'), top = keepScroll ? body.scrollTop : 0;
  if(v.k==='wiki'){ if(!keepScroll) return renderWikiView(v); return; }
  if(v.k==='doc'){
    $('mtit').textContent = v.key.split('/').pop(); body.innerHTML = `<span class="muted">${t('loading_lc')}</span>`; body.dataset.sig = '';
    const r = await loadDoc(v.key, v.p);
    if(MV[MV.length-1]===v){ body.innerHTML = r.err ? `<span class="bad">${esc(r.err)}</span>` : mdFull(r.md, v.p, r.path); body.scrollTop = 0; }
    return;
  }
  const r = lastData && VIEWS[v.k] ? VIEWS[v.k](v.key, v.obj) : null;
  if(r && r.p) v.p = r.p;
  const html = r ? r.html : `<div class="muted">${t('gone')}</div>`;
  if(keepScroll && body.dataset.sig === html) return;   // nothing changed: keep it as it is (open details, scroll)
  $('mtit').textContent = r ? r.title : '';
  body.innerHTML = html; body.dataset.sig = html; body.scrollTop = top;
  if(r && r.after) r.after(v);
}
async function loadDoc(r, p){
  const k = `${p}|${r}`;
  if(DOCS[k]) return DOCS[k];
  try{ const res = await fetch(api(`/api/doc?p=${encodeURIComponent(p)}&path=${encodeURIComponent(r)}`)); if(!res.ok) return {err: t('doc_failed', {path: r})};
       return DOCS[k] = await res.json(); }catch(e){ return {err: t('no_connection')}; }
}

// ---- Drag and drop (pointer events, so it works with touch in iOS Safari): only from the ⋮⋮ handle (touch-action:
// none), so scrolling elsewhere is untouched. The row follows the finger, a dashed gap shows where it will land, and
// the list scrolls by itself near the edges. On release the new position goes to the server (/api/move?a=<pos>).
let DRAG = null, DRAG_END = 0;
document.addEventListener('pointerdown', e => {
  const g = e.target.closest && e.target.closest('.grip'); if(!g || e.button > 0 || DRAG) return;
  const row = g.closest('[data-drag]'), list = row && row.parentElement; if(!row) return;
  e.preventDefault();
  const r = row.getBoundingClientRect(), ph = document.createElement('div');
  ph.className = 'ph'; ph.style.height = r.height + 'px';
  const scroller = row.closest('.mbody');
  DRAG = {row, list, ph, scroller, id: row.dataset.key, dy: e.clientY - r.top, y: e.clientY, pid: e.pointerId,
          before: [...list.querySelectorAll('[data-drag]')].map(x => x.dataset.key)};
  list.insertBefore(ph, row);
  Object.assign(row.style, {position:'fixed', left: r.left+'px', top: r.top+'px', width: r.width+'px', zIndex: 60});
  row.classList.add('lift'); document.body.classList.add('dragging');
  requestAnimationFrame(dragTick);
});
function dragPlace(){
  const {row, list, ph, y} = DRAG;
  row.style.top = (y - DRAG.dy) + 'px';
  const items = [...list.querySelectorAll('[data-drag]')].filter(x => x!==row);
  const next = items.find(x => { const b = x.getBoundingClientRect(); return y < b.top + b.height/2; });
  if(next){ if(ph.nextElementSibling!==next) list.insertBefore(ph, next); }
  else if(items.length){ const last = items[items.length-1]; if(last.nextElementSibling!==ph) last.after(ph); }
}
function dragTick(){
  if(!DRAG) return;
  // auto-scroll near the edges of the scrolling area (the modal body, or the page between header and bottom bar)
  const s = DRAG.scroller, EDGE = 64, MAX = 16;
  const top = s ? s.getBoundingClientRect().top : document.querySelector('header').getBoundingClientRect().bottom;
  const bottom = s ? s.getBoundingClientRect().bottom : $('nav').getBoundingClientRect().top;
  let v = 0;
  if(DRAG.y < top + EDGE) v = -MAX * Math.min(1, (top + EDGE - DRAG.y) / EDGE);
  else if(DRAG.y > bottom - EDGE) v = MAX * Math.min(1, (DRAG.y - bottom + EDGE) / EDGE);
  if(v){ if(s) s.scrollTop += v; else window.scrollBy(0, v); }
  dragPlace();
  requestAnimationFrame(dragTick);
}
document.addEventListener('pointermove', e => { if(!DRAG || e.pointerId!==DRAG.pid) return; e.preventDefault(); DRAG.y = e.clientY; dragPlace(); }, {passive:false});
document.addEventListener('touchmove', e => { if(DRAG) e.preventDefault(); }, {passive:false});
function dragEnd(e, cancel){
  if(!DRAG || e.pointerId!==DRAG.pid) return;
  const {row, list, ph, id, before} = DRAG; DRAG = null; DRAG_END = Date.now();
  row.classList.remove('lift'); row.removeAttribute('style'); document.body.classList.remove('dragging');
  list.insertBefore(row, ph); ph.remove();
  const ids = [...list.querySelectorAll('[data-drag]')].map(x => x.dataset.key);
  if(cancel || ids.join()===before.join()){
    if(cancel) before.forEach(x => list.appendChild(list.querySelector(`[data-drag][data-key="${x}"]`)));
    // unfolded sub-issues go back under their issue (they stay put while the row moves)
    list.querySelectorAll('.subs').forEach(s => list.querySelector(`[data-drag][data-key="${s.dataset.of}"]`)?.after(s));
    return;
  }
  const k = ids.indexOf(id);
  if(list.id==='elist'){ const e0 = epicOf(id); return moveTo('project', e0.eid, k, e0.p); }
  // an issue in a status group of its project page: the position counts among all the active issues of the project
  const c = BL.find(x => x.id===id), rest = BL.filter(x => x.epic===c.epic && isActive(x) && x.id!==id).map(x => x.id);
  moveTo('issue', id, k+1 < ids.length ? rest.indexOf(ids[k+1]) : rest.indexOf(ids[k-1]) + 1, c.p);
}
document.addEventListener('pointerup', e => dragEnd(e, false));
document.addEventListener('pointercancel', e => dragEnd(e, true));
// a drag must not open the row it ends on
document.addEventListener('click', e => { if(Date.now() - DRAG_END < 400){ e.stopPropagation(); e.preventDefault(); } }, true);
function moveTo(kind, id, pos, p){
  // on error the rows go back to where they were (post repaints from the server)
  return post(`/api/move?kind=${kind}&id=${encodeURIComponent(id)}&a=${pos}`, p);
}

// ---- Wiki (read-only), like Notion's page tree and Linear's documents: the tab is the tree of pages grouped by type
// (collapsible groups); a page opens in the modal stack (its own screen on the phone) with breadcrumbs, a big title,
// the front matter as discreet metadata, a collapsible table of contents and the body at reading width. [[links]]
// open the page they name; document links open the document viewer (/api/doc) when the server can serve them,
// otherwise they stay as text. The quick search (⌘K or the search bar) filters titles at once and the text on the
// server.
const WIKI = {list: null, pages: {}, p: null};
const WTYPES = ['overview', 'model', 'engine', 'concept', 'comparison', 'howto'];   // known types first, then the rest
const wgroups = () => [...new Set(WTYPES.concat((WIKI.list || []).map(wgroupOf).sort()))].filter(g => (WIKI.list || []).some(p => wgroupOf(p)===g));
const wgroupName = g => { const s = t('wiki_groups.' + g); return s === 'wiki_groups.' + g ? g : s; };
const wtypeName = g => { const s = t('wiki_types.' + g); return s === 'wiki_types.' + g ? g : s; };
const WSTATUS = {'activo': 'var(--ok)', 'active': 'var(--ok)', 'en-prueba': 'var(--prog)', 'testing': 'var(--prog)', 'referencia': 'var(--acc)', 'reference': 'var(--acc)'};
const WSTATUS_ORDER = ['activo', 'active', 'en-prueba', 'testing', 'referencia', 'reference', 'descartado', 'discarded'];
const wikiP = () => lastData && !lastData.joint && lastData.ps[0].wiki ? lastData.ps[0].name : null;
const wfirst = () => (lastData && lastData.ps[0].wiki_first) || [];
const W_ICON = '<svg class="sico" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3.5 1.5h5l3 3v8h-8z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><path d="M8.5 1.5v3h3" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';
const wfetch = async (path) => { let r; try{ r = await fetch(api(`${path}${path.includes('?') ? '&' : '?'}p=${encodeURIComponent(WIKI.p || '')}`)); }catch(e){ throw new Error(t('no_connection')); } if(!r.ok) throw new Error(r.status===404 ? t('not_found') : t('error', {text: r.status})); return r.json(); };
const wgroupOf = p => p.type || 'overview';
async function wikiList(){
  const p = wikiP();
  if(p !== WIKI.p){ WIKI.p = p; WIKI.list = null; WIKI.pages = {}; }
  if(!p) throw new Error(t('no_wiki'));
  try{ WIKI.list = (await wfetch('/api/wiki')).pages; }catch(e){ if(!WIKI.list) throw e; }
  return WIKI.list;
}
function wikiSorted(g){
  const ps = WIKI.list.filter(p => wgroupOf(p)===g), first = wfirst();
  const rank = p => g==='overview' ? (first.indexOf(p.n) + 1 || 99) : (WSTATUS_ORDER.indexOf(p.status) + 1 || 9);
  return ps.sort((a, b) => rank(a) - rank(b) || a.title.localeCompare(b.title, LANG || 'en'));
}
const wrow = p => lrow({icon: W_ICON, title: `<span${WSTATUS[p.status] || !p.status ? '' : ' class="muted"'}>${esc(p.title)}</span>`,
  meta: (p.status ? `${dot(WSTATUS[p.status] || 'var(--muted)', p.status)}<span class="wst">${esc(p.status)}</span>` : ''),
  attrs: ` data-wiki="${esc(p.n)}"`, cls: 'wrow'});
// a wiki list or page that failed is tried again on the next pass that succeeds (the poll), not only when he comes back
function retryWiki(){
  if(WIKI.failed && $('t-wiki').classList.contains('on')) renderWiki();
  const v = WIKI.pageFailed;
  if(v && MV[MV.length-1] === v) renderWikiView(v);
}
async function renderWiki(){
  const el = $('wtree');
  if(!WIKI.list){ el.innerHTML = list(empty(t('loading'))); el.dataset.sig = ''; }
  try{ await wikiList(); WIKI.failed = false; }catch(e){ WIKI.failed = true; el.innerHTML = list(empty(t('wiki_failed', {text: esc(e.message)}))); el.dataset.sig = ''; return; }
  const html = list(wgroups().map(g => { const ps = wikiSorted(g);
    return ps.length ? group('wiki', g, `<span>${esc(wgroupName(g))}</span><span class="n">${ps.length}</span>`, ps.map(wrow).join(''), '') : ''; }).join(''));
  if(el.dataset.sig !== html){ el.innerHTML = html; el.dataset.sig = html; }
  $('wcount').textContent = t('n_pages', {n: WIKI.list.length});
}
function openWiki(n, anchor, hl){ pushView({k: 'wiki', key: n, anchor, hl}); }
// what a link or `code` in a wiki page becomes: a document the server can serve, another wiki page, or text
function wikiOpts(docs){
  const page = u => { const m = u.replace(/#.*/, '').match(/(?:^|\/)([\w.-]+)\.md$/); return m && WIKI.list && WIKI.list.some(p => p.n===m[1]) ? m[1] : null; };
  return {
    code: c => docs[c] ? `<a class="dl" data-doc="${esc(docs[c])}"><code>${esc(c)}</code></a>` : `<code>${esc(c)}</code>`,
    link: (u, tx) => /^https?:/.test(u) ? `<a href="${esc(u)}" target="_blank" rel="noopener">${tx}</a>`
      : docs[u] ? `<a class="dl" data-doc="${esc(docs[u])}">${tx}</a>`
      : page(u) ? `<a class="wl" data-wiki="${esc(page(u))}">${tx}</a>`
      : `<span class="wbroken" title="${esc(u)}">${tx}</span>`,
  };
}
function wikiPageHtml(p){
  const o = wikiOpts(p.docs || {}), r = mdDoc(p.md.replace(/^\s*# .*\n/, ''), o);   // the title goes in the header
  const g = wgroupOf(p);
  const meta = [
    `<span class="wtype">${esc(wtypeName(p.type || 'page'))}</span>`,
    p.status && `<span class="wstat">${dot(WSTATUS[p.status] || 'var(--muted)')}${esc(p.status)}</span>`,
    p.updated && `<span>${t('updated_on', {date: esc(p.updated)})}</span>`,
    ...(p.tags || []).map(x => `<span class="tag">${esc(x)}</span>`),
  ].filter(Boolean).join('');
  const toc = r.toc.length > 2 ? `<details class="toc"><summary>${t('contents')} <span class="n">${r.toc.length}</span></summary><div>${r.toc.map(x => `<a class="l${x.n}" data-anchor="${x.id}">${esc(x.t)}</a>`).join('')}</div></details>` : '';
  const src = (p.sources || []).length ? `<details class="wsrc"><summary>${t('sources')} <span class="n">${p.sources.length}</span></summary><div>${p.sources.map(x => o.code(x)).join(' ')}</div></details>` : '';
  return `<article class="wdoc"><div class="crumbs"><a data-wtab="">${t('tab_wiki')}</a><span>/</span><a data-wtab="${esc(g)}">${esc(wgroupName(g))}</a><span>/</span><span class="cur">${esc(p.title)}</span></div>`
    + `<h1 class="wtitle">${mdInline(p.title, o)}</h1><div class="wmeta">${meta}</div>${toc}${src}<div class="wbody">${r.html}</div>`
    + `<div class="wfoot muted">${esc(p.path)} · ${p.kb} KB</div></article>`;
}
function scrollToAnchor(id){
  const el = document.getElementById(id), body = $('mbody');
  if(el && body.contains(el)) body.scrollTo({top: el.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 8, behavior: 'smooth'});
}
// marks the first occurrence of the search words in the page and scrolls to it
function highlight(q){
  const words = fold(q).split(/\s+/).filter(w => w.length > 1); if(!words.length) return;
  const walk = document.createTreeWalker($('mbody').querySelector('.wbody'), NodeFilter.SHOW_TEXT);
  for(let n; (n = walk.nextNode());){
    const f = fold(n.nodeValue), k = f.indexOf(words[0]); if(k < 0) continue;
    // fold() keeps the length for accented Latin text (one combining mark removed per accented letter after NFKD)
    const r = document.createRange(); r.setStart(n, Math.min(k, n.nodeValue.length)); r.setEnd(n, Math.min(k + words[0].length, n.nodeValue.length));
    const mk = document.createElement('mark'); try{ r.surroundContents(mk); }catch(e){ return; }
    const det = mk.closest('details'); if(det) det.open = true;
    const body = $('mbody'); body.scrollTop += mk.getBoundingClientRect().top - body.getBoundingClientRect().top - body.clientHeight / 3;
    return;
  }
}
async function renderWikiView(v){
  const body = $('mbody');
  $('mtit').textContent = (WIKI.pages[v.key] || {}).title || v.key;
  if(!WIKI.pages[v.key]){ body.innerHTML = `<span class="muted">${t('loading_lc')}</span>`; }
  try{
    if(!WIKI.list) await wikiList();
    WIKI.pages[v.key] = await wfetch(`/api/wiki/page?n=${encodeURIComponent(v.key)}`);
  }catch(e){ if(!WIKI.pages[v.key]){ if(MV[MV.length-1]===v){ body.innerHTML = `<span class="bad">${esc(e.message)}: «${esc(v.key)}»</span>`; WIKI.pageFailed = v; } return; } }
  if(WIKI.pageFailed === v) WIKI.pageFailed = null;
  if(MV[MV.length-1]!==v) return;
  v.p = WIKI.p;
  const p = WIKI.pages[v.key], html = wikiPageHtml(p);
  $('mtit').textContent = p.title;
  if(body.dataset.sig !== html){ body.innerHTML = html; body.dataset.sig = html; body.scrollTop = 0; }
  if(v.anchor) scrollToAnchor('h-' + v.anchor.replace(/^h-/, ''));
  else if(v.hl) highlight(v.hl);
}
// ---- Quick search (⌘K): titles filtered in the page at once, text searched on the server (debounced)
let PAL_T = null, PAL_SEQ = 0, PAL_SEL = 0;
async function openPalette(){
  if(!wikiP()) return;
  $('pal').hidden = false; const q = $('palq'); q.value = ''; q.focus();
  palRender([], null);
  try{ await wikiList(); }catch(e){}
  palInput();
}
function closePalette(){ $('pal').hidden = true; $('palq').blur(); }
function palInput(){
  const q = $('palq').value.trim(); clearTimeout(PAL_T);
  const words = fold(q).split(/\s+/).filter(Boolean);
  const titles = !WIKI.list ? [] : !words.length ? wfirst().map(n => WIKI.list.find(p => p.n===n)).filter(Boolean)
    : WIKI.list.filter(p => words.every(w => fold(`${p.title} ${p.n} ${(p.tags || []).join(' ')}`).includes(w))).slice(0, 12);
  PAL_SEL = 0; palRender(titles, words.length ? 'loading' : null, q);
  if(!words.length) return;
  const seq = ++PAL_SEQ;
  PAL_T = setTimeout(async () => {
    let res = [];
    try{ res = (await wfetch(`/api/wiki/search?q=${encodeURIComponent(q)}`)).results; }catch(e){}
    if(seq !== PAL_SEQ) return;
    palRender(titles, res.filter(r => !titles.some(p => p.n===r.n) || r.hits), q);
  }, 160);
}
function hlText(s, q){
  let h = esc(s); const words = q ? q.split(/\s+/).filter(w => w.length > 1) : [];
  for(const w of words) h = h.replace(new RegExp(w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), m => `<mark>${m}</mark>`);
  return h;
}
function palRender(titles, text, q=''){
  const sec = (s, rows) => rows ? `<div class="palsec">${s}</div>${rows}` : '';
  const items = [
    sec(q ? t('wiki_pages') : t('wiki_general'), titles.map(p => `<div class="palrow" data-wiki="${esc(p.n)}">${W_ICON}<span class="tt">${hlText(p.title, q)}</span><span class="meta">${esc(wtypeName(p.type || ''))}</span></div>`).join('')),
    text==='loading' ? `<div class="palsec muted">${t('wiki_searching')}</div>`
      : text ? sec(t('wiki_in_text'), text.map(r => `<div class="palrow tx" data-wiki="${esc(r.n)}" data-hl="${esc(q)}"><div class="ttl">${W_ICON}<span class="tt">${hlText(r.title, q)}</span><span class="meta">${tn('hits', r.hits)}</span></div><div class="snip">${hlText(r.excerpt, q)}</div></div>`).join('')) || (q && !titles.length ? `<div class="palsec muted">${t('wiki_nothing')}</div>` : '') : '',
  ].join('');
  $('palres').innerHTML = items;
  palMark();
}
function palMark(){ const rows = [...$('palres').querySelectorAll('.palrow')]; rows.forEach((r, k) => r.classList.toggle('sel', k===PAL_SEL)); rows[PAL_SEL]?.scrollIntoView({block: 'nearest'}); }
function palKey(e){
  const rows = $('palres').querySelectorAll('.palrow');
  if(e.key==='ArrowDown'){ PAL_SEL = Math.min(rows.length - 1, PAL_SEL + 1); palMark(); e.preventDefault(); }
  else if(e.key==='ArrowUp'){ PAL_SEL = Math.max(0, PAL_SEL - 1); palMark(); e.preventDefault(); }
  else if(e.key==='Enter' && rows[PAL_SEL]){ rows[PAL_SEL].click(); e.preventDefault(); }
  else if(e.key==='Escape'){ closePalette(); e.stopPropagation(); }
}
window.addEventListener('keydown', e => { if((e.metaKey || e.ctrlKey) && e.key.toLowerCase()==='k' && wikiP()){ e.preventDefault(); $('pal').hidden ? openPalette() : closePalette(); } });

// ---- The product picker: the header title opens a sheet (the Backlog's project filter component) with every product
// of the registry, its state in a dot and a line, and «all products» for the joint view
function renderPicker(){
  const ps = OVER.projects, ok = ps.filter(o => !o.broken);
  const needsOf = o => ({mode: o.mode, orch: o.orchestrator, health: o.orchestrator_health, paused: o.paused, questions: o.decisions, late: o.late.length});
  const line = o => o.broken ? (o.mode && o.mode !== 'active' ? o.mode : '') : [o.mode==='inactive' ? '' : orchLine(o.orchestrator, true), tn('agents_count', o.agents), o.decisions ? tn('decisions_count', o.decisions) : '', o.late.length ? tn('late_count', o.late.length) : ''].filter(Boolean).join(' · ');
  const row = (v, title, color, meta, tag='') => lrow({icon: dot(color), title: `${esc(title)}${tag}`, meta, cls: v===P ? 'prow on' : 'prow',
    attrs: ` onclick="pick('${esc(v)}')" style="cursor:pointer"`});
  // a product whose config does not load stays here, red and tagged (its error shows when picked); one with config
  // warnings gets a tag too
  const tag = o => o.broken ? `<span class="ro bad">${t('config_broken_tag')}</span>`
    : (o.warnings || []).length ? `<span class="ro warn">${esc(tn('config_warnings_tag', o.warnings.length))}</span>` : '';
  const tags = o => langTag(o) + tag(o);
  const html = list(ps.map(o => row(o.name, o.title, o.broken ? 'var(--bad)' : (o.warnings || []).length && productNeeds(needsOf(o)).level==='ok' ? 'var(--warn)' : NEED_COLOR[productNeeds(needsOf(o)).level],
      esc(line(o)), tags(o))).join('')
    + (ok.length > 1 ? row('*', t('all_products'), 'var(--muted)', esc(tn('products_count', ok.length) + (ps.length > ok.length ? ' · ' + tn('broken_count', ps.length - ok.length) : ''))) : ''));
  if($('plist').dataset.sig !== html){ $('plist').innerHTML = html; $('plist').dataset.sig = html; }
  const cur = ps.find(p => p.name===P);
  $('hname').textContent = P==='*' ? t('all_products') : cur ? cur.title : t('title');
  // the browser tab says the product too: «<title> · foreman»
  document.title = cur && P!=='*' ? `${cur.title} · ${t('title')}` : t('title');
  $('hpick').querySelector('svg').style.display = ps.length > 1 ? '' : 'none';
}
function openPicker(){ if(OVER && OVER.projects.length > 1) $('psheet').hidden = false; }   // broken ones count: they are listed
function closePicker(){ $('psheet').hidden = true; }
function pick(v){
  closePicker();
  P = v; lsSet('product', v); ALL_CLOSED = false; lastSignature = null; lastEtag = null; lastBlEtag = null; lastBlSig = null; BRAW = null; lastData = null; epic = ''; lsSet('project_filter', '');
  if(MV.length) closeModal();
  load();
}

// ---- Polling, actions and navigation
const keyOf = x => (x.closest('[id]')?.id || '') + '|' + (x.querySelector('summary')?.textContent || '');
const clock = () => new Date().toTimeString().slice(0, 8);
let lastEtag = null, lastOver = 0;
// the backlog: its last answer (BRAW) and ETag, and what the Backlog tab was last painted from
let BRAW = null, lastBlEtag = null, lastBlSig = null;
// the backlog's closed issues: only the recent ones (closed=recent) until the owner asks for the older ones in this
// page; a reload or another product goes back to the light load
let ALL_CLOSED = false;
function showOlder(b){
  if(b){ b.onclick = null; b.querySelector('.tt').textContent = t('loading_lc'); }
  ALL_CLOSED = true; lastBlEtag = null; lastSignature = null; load();
}
function render(d, blSig){
  BL = d.backlog;
  renderNow(d); renderYou(d); renderWork(d); renderSystem(d);
  // the Backlog tab: its cards with what is live about them, the recent moves and who may write
  if(blSig !== lastBlSig){ lastBlSig = blSig; renderBacklog(d); }
  // the Wiki tab only for a product with a wiki
  const wiki = !!wikiP();
  $('navwiki').style.display = wiki ? '' : 'none';
  if(!wiki && $('t-wiki').classList.contains('on')) tab('now');
  // reopened on the Wiki tab: it was drawn before the data came (no product, no texts); draw it now, and again until it loads
  else if(wiki && $('t-wiki').classList.contains('on') && (WIKI.p !== wikiP() || !WIKI.list)) renderWiki();
}
async function load(){
  try{
    // the registry (products, their language) every 30 s or when the product changes
    if(!OVER || Date.now() - lastOver > 30000 || (P !== '*' && !OVER.projects.some(p => p.name===P))){
      const o = await fetch(api('/api/projects'), {cache:'no-cache'});
      if(!o.ok) throw new Error(o.status===403 ? t('wrong_key') : t('error', {text: o.status}));
      OVER = await o.json(); lastOver = Date.now(); setSkew(o);
      if(versionCheck(OVER.version)) return;
      if(lastData){ renderChips(lastData); renderMac(); }   // the machine's stats come with the registry
      const first = (OVER.projects.find(p => !p.broken) || OVER.projects[0] || {}).name || '';
      // the home is «All products» when there are several, until the owner picks one (then his choice is kept)
      const several = OVER.projects.filter(p => !p.broken).length > 1;
      if(P !== '*' && !OVER.projects.some(p => p.name===P)) P = several ? '*' : first;
      if(P==='*' && !several) P = first;
    }
    const cur = OVER.projects.find(p => p.name===P);
    await loadStrings((cur && cur.language) || OVER.language || 'en');
    renderPicker(); renderAbout();
    if(!P){ $('headline').innerHTML = card(`<span class="muted">${t('no_products')} ${esc(OVER.errors.join('; '))}</span>`); return; }
    // a product whose config does not load has no data: its error replaces the tabs until it is fixed
    document.body.classList.toggle('broken', !!(cur && cur.broken));
    $('broken').innerHTML = cur && cur.broken ? card(`<div class="headline"><div class="ico" style="background:var(--bad)">!</div><div style="min-width:0"><div class="t">${t('config_broken_title', {name: esc(cur.name)})}</div><div class="s">${t('config_broken_text')}</div></div></div><div class="wrap bad brokenerr">${esc(cur.error)}</div>`) : '';
    if(cur && cur.broken){ lastSignature = null; lastEtag = null; lastData = null; markFresh(); $('clock').textContent = t('updated_at', {t: clock()}); return; }
    // no-cache: the browser revalidates with the ETag and an unchanged answer comes back as a bodiless 304. The state
    // (live: agents, locks, progress) and the backlog (only changes with its files) are two answers
    const q = encodeURIComponent(P);
    const [r, rb] = await Promise.all([fetch(api(`/api/state?p=${q}`), {cache:'no-cache'}), fetch(api(`/api/backlog?p=${q}${ALL_CLOSED ? '' : '&closed=recent'}`), {cache:'no-cache'})]);
    for(const x of [r, rb]) if(!x.ok) throw new Error(x.status===403 ? t('wrong_key') : t('error', {text: x.status}));
    markFresh();
    $('clock').textContent = t('updated_at', {t: clock()});
    setSkew(r);
    const etag = r.headers.get('ETag'), blEtag = rb.headers.get('ETag');
    // the backlog is parsed only when it changed
    if(!BRAW || !blEtag || blEtag !== lastBlEtag){ BRAW = await rb.json(); lastBlEtag = blEtag; lastSignature = null; clearFull(); }
    // same bytes as last time: nothing to parse or paint but the ages
    if(etag && etag === lastEtag && lastSignature){ paintAges(); return; }
    const raw = await r.json();
    if(DRAG) return;   // never repaint under a finger that is dragging a row
    lastEtag = etag;
    // the page polls data only: reload it when index.html, app.js or app.css changed on disk, so a restart never
    // leaves old code running
    if(versionCheck(raw.version)) return;
    const signature = (etag || JSON.stringify(raw)) + (lastBlEtag || JSON.stringify(BRAW)) + LANG;
    if(signature === lastSignature){ paintAges(); return; }   // no changes: do not repaint (so open details stay open)
    lastSignature = signature;
    const openSet = new Set([...document.querySelectorAll('.tab details[open]')].map(keyOf));
    const d = normalize(raw, BRAW);
    // the wiki settings of the product shown come with the registry
    d.ps.forEach(p => { const o = OVER.projects.find(x => x.name===p.name) || {}; p.wiki_first = o.wiki_first || []; });
    lastData = d;
    render(d, [lastBlEtag || JSON.stringify(BRAW), JSON.stringify(d.ps.map(p => [p.name, p.writable, p.targets, p.issue_live])), JSON.stringify(d.moves), LANG].join('|'));
    document.querySelectorAll('.tab details').forEach(x => { if(openSet.has(keyOf(x))) x.open = true; });
    const v = MV[MV.length-1];
    if(v && LIVE.has(v.k)) renderModal(true);   // the open detail follows the data
  }catch(e){
    // a pass that failed may be this page's code against a newer server: ask the version alone and reload if it moved
    try{
      const v = await fetch(api('/api/version'), {cache:'no-store'});
      if(v.ok && versionCheck((await v.json()).version)) return;
    }catch(e2){}
    markStale(e);
  }
}
// ---- A pass that failed: a banner in the panel's language says so and how old the data on screen is, that data is
// dimmed until a pass succeeds, and the 5 s poll retries on its own
let lastOk = 0;
const isNet = e => !!e && (e.name === 'TypeError' || e.name === 'AbortError');   // what fetch throws when nothing answers
function markFresh(){
  lastOk = Date.now(); $('err').textContent = ''; document.body.classList.remove('stale');
  retryWiki();
}
function markStale(e){
  const age = lastOk ? Math.max(60, Math.round((Date.now() - lastOk) / 1000)) : null, dr = age == null ? '' : dur(Math.round(age / 60) * 60);
  const key = (isNet(e) ? 'offline_' : 'panel_error_') + (age == null ? 'none' : 'stale');
  $('err').textContent = t(key, {dur: dr, text: isNet(e) ? '' : (e && e.message) || ''});
  if(lastOk) document.body.classList.add('stale');
}
async function decide(b){
  if(!confirm(t('confirm_answer', {op: b.dataset.op}))) return;
  post(`/api/decide?id=${encodeURIComponent(b.dataset.id)}&op=${encodeURIComponent(b.dataset.op)}`, b.dataset.p);
}
// his own words as the answer: they resolve the decision, marked free for the orchestrator to interpret
async function decideFree(b){
  const k = b.dataset.fk, v = (FREE_DRAFT.get(k) || '').trim();
  if(!v || !confirm(t('confirm_free', {text: v.length > 80 ? v.slice(0, 80) + '…' : v}))) return;
  let err = '';
  try{
    const r = await fetch(api(`/api/decide?id=${encodeURIComponent(b.dataset.id)}&free=1${b.dataset.ch ? '&change=1' : ''}&p=${encodeURIComponent(b.dataset.p)}`), {method:'POST', body: v});
    if(!r.ok) err = (await r.json().catch(() => ({}))).error || `${r.status}`;
  }catch(e){ err = t('no_connection'); }
  if(!err){ FREE_OPEN.delete(k); FREE_DRAFT.delete(k); CHANGE_OPEN.delete(k); }
  lastSignature = null; lastEtag = null; lastBlEtag = null; lastBlSig = null; await load();
  if(err) $('err').textContent = t('error', {text: err});
}
async function approve(p, repo, branch, pr){
  if(!confirm(t('confirm_approve', {branch}))) return;
  post(`/api/approve?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}&pr=${pr}`, p);
}
async function send(){
  const v = $('txt').value.trim(); if(!v || !lastData || lastData.joint) return;
  try{ await fetch(api(`/api/message?p=${encodeURIComponent(lastData.ps[0].name)}`), {method:'POST', body: v}); $('txt').value=''; }catch(e){}
  lastSignature = null; lastEtag = null; load();
}
function tab(name){
  if(MV.length) closeModal();
  document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x.id==='t-'+name));
  document.querySelectorAll('#nav button').forEach(x=>x.classList.toggle('on',x.dataset.t===name));
  lsSet('tab', name);
  window.scrollTo(0,0);
  if(name==='wiki' && wikiP()) renderWiki();   // on opening the page there is no data yet: render() draws it then
}
document.querySelectorAll('#nav button').forEach(b=>b.onclick=()=>tab(b.dataset.t));
{ const s = lsGet('tab', ''); if(s && $('t-'+s)) tab(s); }
async function refresh(){
  const b=$('refresh'); b.classList.add('spin');
  lastOver = 0; await load(); setTimeout(()=>b.classList.remove('spin'),300);
}
// pull down to refresh (also in the home-screen app, which lacks it)
let y0=null, dy=0; const ptr=$('ptr');
addEventListener('touchstart',e=>{ y0 = scrollY<=0 && !e.target.closest('.grip, .modal') ? e.touches[0].clientY : null; dy=0; },{passive:true});
addEventListener('touchmove',e=>{ if(y0==null || DRAG) return; dy=Math.max(0,e.touches[0].clientY-y0);
  ptr.style.height=Math.min(dy/2,50)+'px'; ptr.textContent = dy>120 ? t('ptr_release') : t('ptr_pull'); },{passive:true});
addEventListener('touchend',async()=>{ if(y0==null) return; y0=null;
  if(dy>120){ ptr.textContent=t('ptr_loading'); await refresh(); } ptr.style.height='0'; });
// poll every 5 s while the page is visible; on coming back (the phone unlocked, the app reopened) at once
load(); setInterval(() => { if(!document.hidden) load(); }, 5000);
document.addEventListener('visibilitychange', () => { if(!document.hidden) load(); });
