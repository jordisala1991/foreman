"""The page a «See conversation» click opens in its own tab while Remote Control turns on (GET /agent/wait): the panel's
look (the new panel's tokens, light and dark following the system), the agent's name, «Opening the conversation…» with a
quiet progress bar, and, when it fails, the reason with a button to close the tab. The page itself asks
/api/agent/watch (with the key it was opened with) and goes to the link when it comes, so both panels only open this
address inside the click. One self-contained file: no font, script or image from outside the panel."""

# ruff: noqa: E501  (the page's CSS and script are written as they are served)
from __future__ import annotations

from html import escape

# the page's own words (it is not part of either panel's bundle): language -> text
WORDS = {
    "es": {
        "title": "Abriendo la conversación",
        "opening": "Abriendo la conversación…",
        "hint": "Se activa el control remoto de la sesión; tarda unos segundos y se abre la app de Claude.",
        "failed": "No se ha podido abrir la conversación",
        "close": "Cerrar",
        "no_connection": "Sin conexión con el panel",
    },
    "en": {
        "title": "Opening the conversation",
        "opening": "Opening the conversation…",
        "hint": "Remote Control is being turned on for the session; it takes a few seconds and the Claude app opens.",
        "failed": "Could not open the conversation",
        "close": "Close",
        "no_connection": "No connection to the panel",
    },
}

# the CSP of this page: its own style and script, and fetches to the panel only
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

CSS = """
:root { color-scheme: light dark; --bg: #f5f5f7; --surface: #ffffff; --border: #e3e3e8; --text: #16171a; --text2: #4f535b;
  --accent: #5e5ce6; --track: #ececf0; --red: #c9252c; --red-border: #eaa3a6; --shadow: 0 8px 24px rgba(20, 20, 30, 0.14); }
@media (prefers-color-scheme: dark) { :root { --bg: #0d0e10; --surface: #16181c; --border: #272b32; --text: #ededef;
  --text2: #a3a8b2; --accent: #7a78f0; --track: #24272d; --red: #ff7070; --red-border: rgba(255, 112, 112, 0.55);
  --shadow: 0 8px 24px rgba(0, 0, 0, 0.5); } }
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; background: var(--bg); color: var(--text); }
body { min-height: 100vh; display: grid; place-items: center; padding: 24px;
  font: 15px/1.45 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased; -webkit-text-size-adjust: 100%; }
.card { width: 100%; max-width: 420px; background: var(--surface); border: 1px solid var(--border); border-radius: 16px;
  box-shadow: var(--shadow); padding: 32px 24px 28px; text-align: center; }
.avatar { width: 56px; height: 56px; margin: 0 auto 16px; border-radius: 50%; display: grid; place-items: center;
  font-size: 20px; font-weight: 700; color: #16171a; }
h1 { margin: 0 0 4px; font-size: 20px; line-height: 1.25; font-weight: 700; letter-spacing: -0.01em; overflow-wrap: anywhere; }
.state { margin: 0; font-size: 15px; color: var(--text2); }
.bar { position: relative; height: 4px; margin: 24px auto 16px; max-width: 260px; border-radius: 2px; background: var(--track);
  overflow: hidden; }
.bar::after { content: ""; position: absolute; top: 0; bottom: 0; width: 38%; border-radius: 2px; background: var(--accent);
  animation: slide 1.6s ease-in-out infinite; }
@keyframes slide { 0% { left: -40%; } 100% { left: 102%; } }
@media (prefers-reduced-motion: reduce) { .bar::after { animation: none; left: 31%; opacity: .7; } }
.hint { margin: 0; font-size: 13px; line-height: 1.45; color: var(--text2); }
.fail .bar, .fail .hint.wait { display: none; }
.fail .state { color: var(--red); font-weight: 600; }
.reason { display: none; margin: 12px 0 0; padding: 10px 12px; border: 1px solid var(--red-border); border-radius: 10px;
  font-size: 13px; text-align: left; overflow-wrap: anywhere; }
.fail .reason { display: block; }
button { display: none; min-height: 44px; margin: 20px auto 0; padding: 0 22px; border: 1px solid var(--border);
  border-radius: 12px; background: var(--surface); color: var(--text); font: inherit; font-weight: 600; cursor: pointer; }
.fail button { display: inline-block; }
button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
"""

# the script reads everything from data- attributes (nothing of the query is put into code)
SCRIPT = """
const d = document.body.dataset, q = new URLSearchParams(location.search), card = document.querySelector('.card');
const fail = (why) => { card.classList.add('fail'); document.querySelector('.state').textContent = d.failed;
  document.querySelector('.reason').textContent = why; document.querySelector('button').focus(); };
document.querySelector('button').onclick = () => { window.close(); };
(async () => {
  try {
    const r = await fetch('/api/agent/watch?' + new URLSearchParams({id: q.get('id') || '', p: q.get('p') || '', t: q.get('t') || ''}),
      {method: 'POST'});
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.link) location.replace(j.link); else fail(j.error || String(r.status));
  } catch (e) { fail(d.noconn); }
})();
"""

AVATARS = ["#9cc0ff", "#f2a9cb", "#e3c49c", "#ffb98a", "#86d4e6", "#d6b3f0", "#a8c5d9", "#c9d98f"]


def initials(name: str) -> str:
    """The letters on the avatar, as the cards' (the first of the first two words longer than 3 letters)."""
    words = [w for w in name.split() if len(w) > 3][:2]
    return ("".join(w[0] for w in words) or name[:2]).upper()


def avatar_colour(name: str) -> str:
    h = 7
    for c in name:
        h = (h * 31 + ord(c)) % 997
    return AVATARS[h % len(AVATARS)]


def page(name: str, lang: str) -> str:
    """The waiting page for the agent called `name` (its card's title), in `lang` (es, else English)."""
    w = WORDS.get(lang, WORDS["en"])
    name = " ".join(name.split())[:120] or "—"
    return f"""<!doctype html>
<html lang="{escape(lang if lang in WORDS else "en")}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark"><title>{escape(w["title"])} · {escape(name)}</title>
<style>{CSS}</style></head>
<body data-failed="{escape(w["failed"])}" data-noconn="{escape(w["no_connection"])}">
<main class="card" role="status" aria-live="polite">
<div class="avatar" aria-hidden="true" style="background:{avatar_colour(name)}">{escape(initials(name))}</div>
<h1>{escape(name)}</h1>
<p class="state">{escape(w["opening"])}</p>
<div class="bar" aria-hidden="true"></div>
<p class="hint wait">{escape(w["hint"])}</p>
<p class="reason"></p>
<button type="button">{escape(w["close"])}</button>
</main>
<script>{SCRIPT}</script>
</body></html>
"""
