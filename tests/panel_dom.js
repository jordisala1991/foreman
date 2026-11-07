// node tests/panel_dom.js APP.js BASEURL KEY PRODUCT TAB SCENARIO.js
// Runs the panel's app.js, whole, over a minimal DOM (elements made on demand; an inline onclick is run when the
// element is clicked) with a localStorage holding PRODUCT and TAB, and its fetch pointed at BASEURL (a running panel).
// Then runs the scenario in the same context; the scenario prints one line of JSON. Used by tests/test_panel_stuck.py.
'use strict';
const fs = require('fs'), vm = require('vm');
const [appPath, base, key, product, tab, scenarioPath] = process.argv.slice(2);

const registry = {}, listeners = {};
const ids = html => [...html.matchAll(/<(\w+)([^>]*?)\sid="(\w+)"([^>]*)>/g)];
function el(id){
  const e = {id, style: {}, dataset: {}, children: [], hidden: false, textContent: '', value: '', innerHTML: '',
    classes: new Set(),
    get classList(){ const c = this.classes;
      return {add: n => c.add(n), remove: n => c.delete(n), contains: n => c.has(n), toggle: (n, on) => ((on ?? !c.has(n)) ? c.add(n) : c.delete(n))}; },
    querySelector: () => el('?'), querySelectorAll: () => [], closest: () => null, addEventListener(){}, focus(){},
    scrollIntoView(){}, setAttribute(){}, getBoundingClientRect: () => ({top: 0, bottom: 0, height: 0}),
    insertAdjacentHTML(where, html){
      for(const m of ids(html)){
        const x = el(m[3]); x.attrs = m[2] + ' ' + m[4];
        x.remove = () => { delete registry[m[3]]; };
        registry[m[3]] = x;
      }
    },
    click(){
      const m = /onclick="([^"]*)"/.exec(this.attrs || '');
      if(m) vm.runInContext('(function(event){' + m[1].replace(/&quot;/g, '"') + '})', ctx).call(this, {target: this});
      if(this.onclick) this.onclick();
    }};
  return e;
}
const document = {
  getElementById: id => registry[id] || (id === 'csheet' ? null : (registry[id] = el(id))),
  querySelector: () => el('?'), querySelectorAll: sel => (sel === '.tab' ? ['now', 'you', 'work', 'backlog', 'system', 'wiki'].map(n => document.getElementById('t-' + n)) : []), body: el('body'),
  documentElement: el('html'), hidden: false,
  addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
};
const store = {tab, product};
const real = global.fetch;
const ctx = vm.createContext({
  document, console, URLSearchParams, Date, JSON, Math, setTimeout, setInterval, clearTimeout, Promise, Set, Map,
  location: {search: '?t=' + key, hash: '', href: base + '/?t=' + key}, history: {pushState(){}, replaceState(){}},
  localStorage: {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; }},
  sessionStorage: {getItem: () => null, setItem(){}, removeItem(){}},
  navigator: {}, scrollTo(){}, scrollY: 0, matchMedia: () => ({matches: false, addEventListener(){}}),
  addEventListener(){}, innerWidth: 390, requestAnimationFrame: f => setTimeout(f, 0),
  fetch: (u, o) => real(String(u).startsWith('http') ? u : base + u, o),
  confirm: () => true,
});
ctx.window = ctx;
// the keys a test presses, and the pause that lets the page's own loads finish
ctx.press = key => (listeners.keydown || []).forEach(f => f({key, stopImmediatePropagation(){}, preventDefault(){}}));
ctx.settle = (ms = 600) => new Promise(r => setTimeout(r, ms));
ctx.dom = {registry};
try{
  vm.runInContext(fs.readFileSync(appPath, 'utf8'), ctx, {filename: 'app.js'});
}catch(e){ console.error('app.js: ' + e.stack); process.exit(2); }
(async () => {
  await ctx.settle(1500);
  const out = await vm.runInContext('(async () => {' + fs.readFileSync(scenarioPath, 'utf8') + '})()', ctx);
  console.log(JSON.stringify(out));
  process.exit(0);
})().catch(e => { console.error(e.stack); process.exit(3); });
