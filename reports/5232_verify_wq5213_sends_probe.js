'use strict';
// wq#5232 verify probe (NOT committed to ccc): boot the real app like
// app-chrome-probe.js and record, per chrome page, which IPC channels main sent
// to it AFTER the page finished loading (= deliverable), shared vs standalone.
// argv --solo => CCC_SHARED_CHROME=0 (control).
const fs = require('fs');
const os = require('os');
const path = require('path');
const solo = process.argv.includes('--solo');
if (solo) process.env.CCC_SHARED_CHROME = '0';
const { app, BaseWindow } = require('electron');
const SRC = '/home/lumen/worktrees/wq5232-a1/src';

const nArg = process.argv.find((a) => /^--n=\d+$/.test(a));
const N = nArg ? Number(nArg.slice(4)) : 8;
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ccc-verify-probe-'));
fs.mkdirSync(path.join(dir, 'fakebin'));
fs.writeFileSync(path.join(dir, 'fakebin', 'ssh'), '#!/bin/sh\ncat >/dev/null 2>&1\nexit 255\n', { mode: 0o755 });
process.env.PATH = path.join(dir, 'fakebin') + ':' + process.env.PATH;
process.env.HOME = dir;
const userData = path.join(dir, 'userData');
fs.mkdirSync(userData);
const tabs = [];
for (let i = 1; i <= N; i++) tabs.push({ id: i, kind: 'chat', url: 'about:blank', title: 'bench ' + i, slept: false, lastAccessedAt: 1000 + i });
fs.writeFileSync(path.join(userData, 'ccc-state.json'), JSON.stringify({ tabs, activeOrch: '*', fixedColumns: 3, pageOffset: 0, zoomFactor: 1, tasks: [] }));
app.setPath('userData', userData);

const T0 = Date.now();
const sends = new Map();   // wc.id -> { loaded:false, before:Set, after:Set, wc }
app.on('web-contents-created', (_e, wc) => {
  const rec = { loaded: false, before: new Set(), after: new Set(), wc };
  sends.set(wc.id, rec);
  wc.on('did-finish-load', () => { rec.loaded = true; rec.loads = (rec.loads || []).concat([{ at: Date.now() - T0, listeners: wc.listenerCount('did-finish-load') }]); });
  rec.regs = [];
  for (const m of ['on', 'once']) {
    const o = wc[m].bind(wc);
    wc[m] = (ev, fn) => {
      if (ev === 'did-finish-load') {
        const line = (new Error().stack.split('\n').find((l) => l.includes('/src/main.js')) || '').replace(/.*main\.js:/, 'main.js:').replace(/\).*/, '');
        rec.regs.push({ at: Date.now() - T0, m, line, afterLoad: rec.loaded });
      }
      return o(ev, fn);
    };
  }
  const orig = wc.send.bind(wc);
  wc.send = (ch, ...a) => { (rec.loaded ? rec.after : rec.before).add(ch); return orig(ch, ...a); };
});

require(path.join(SRC, 'main.js'));

app.whenReady().then(() => setTimeout(async () => {
  const out = {};
  for (const rec of sends.values()) {
    const wc = rec.wc;
    if (wc.isDestroyed()) continue;
    const url = wc.getURL();
    if (!url.startsWith('file:')) continue;
    const page = path.basename(url);
    let text = '';
    try { text = await wc.executeJavaScript('(document.body && document.body.innerText || "").replace(/\\s+/g, " ")'); } catch (_e) {}
    const key = page + '#' + wc.id;
    out[key] = { pid: wc.getOSProcessId(), delivered: [...rec.after].sort(), beforeLoad: [...rec.before].sort(), textLen: text.length, text: text.slice(0, 160), loads: rec.loads || [], regs: rec.regs, listenersNow: wc.listenerCount('did-finish-load') };
  }
  console.log('VERIFY ' + (solo ? 'solo' : 'shared') + ' ' + JSON.stringify(out));
  try { fs.rmSync(dir, { recursive: true, force: true }); } catch (_e) {}
  app.exit(0);
}, Number(process.env.PROBE_SETTLE_MS || 15000)));
