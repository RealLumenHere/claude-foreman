// wq#3103 test-to-failure for the Commander preload (loopback bind + Access guard + NODE_OPTIONS strip +
// runtime mode file), against real supergateway 3.4.3 + stdio fixture on scratch ports. Touches nothing live.
import http from 'node:http';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import { spawn, execFileSync } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import path from 'node:path';

const DIR = path.dirname(new URL(import.meta.url).pathname);
const OPS = path.resolve(DIR, '..');
const SG = process.env.SG || '/home/lumen/.npm-global/bin/supergateway';
const FIX = path.join(DIR, 'fixture_mcp_stdio.py');
const TEAM = 'testteam', ISS = 'https://testteam.cloudflareaccess.com';
const AUD = crypto.randomBytes(32).toString('hex');
const k1 = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
const jwk = (k, kid) => ({ ...k.publicKey.export({ format: 'jwk' }), kid, alg: 'RS256', use: 'sig' });
const certsSrv = http.createServer((q, r) => { r.writeHead(200, { 'content-type': 'application/json' }); r.end(JSON.stringify({ keys: [jwk(k1, 'kid1')] })); });
await new Promise(r => certsSrv.listen(0, '127.0.0.1', r));
const CERTS = `http://127.0.0.1:${certsSrv.address().port}/certs`;
const enc = o => Buffer.from(JSON.stringify(o)).toString('base64url');
function jwt(extra = { email: 'f@x' }) {
  const now = Math.floor(Date.now() / 1000);
  const h = enc({ alg: 'RS256', kid: 'kid1', typ: 'JWT' }), p = enc({ iss: ISS, aud: [AUD], exp: now + 600, nbf: now - 5, iat: now, ...extra });
  return `${h}.${p}.${crypto.sign('RSA-SHA256', Buffer.from(`${h}.${p}`), k1.privateKey).toString('base64url')}`;
}

// install the four preload files together in a temp dir, exactly like /home/lumen/bin
const T = fs.mkdtempSync(path.join(os.tmpdir(), 'wq3103-'));
for (const [src, dst] of [[path.join(DIR, 'commander-preload.cjs'), 'commander-preload.cjs'],
  [path.join(DIR, 'dc-call-window.cjs'), 'dc-call-window.cjs'],
  [path.join(OPS, 'wq2751-listener-audit/loopback-listen.cjs'), 'loopback-listen.cjs'],
  [path.join(OPS, 'wq2768-access-guard/access-jwt-guard.cjs'), 'access-jwt-guard.cjs']]) fs.copyFileSync(src, path.join(T, dst));
const MODEFILE = path.join(T, 'guard.mode');

function start(port, env) {
  const p = spawn(SG, ['--stdio', `python3 ${FIX}`, '--port', String(port), '--outputTransport', 'streamableHttp',
    '--streamableHttpPath', '/mcp', '--healthEndpoint', '/health', '--logLevel', 'none'], {
    env: { PATH: process.env.PATH, HOME: process.env.HOME, NODE_OPTIONS: `--require=${path.join(T, 'commander-preload.cjs')}`,
      LOOPBACK_LISTEN_PORT: String(port), ACCESS_GUARD_PORT: String(port), ACCESS_GUARD_TEAM: TEAM, ACCESS_GUARD_AUD: AUD,
      ACCESS_GUARD_ISS: ISS, ACCESS_GUARD_CERTS_URL: CERTS, ACCESS_GUARD_MODE: 'shadow', ACCESS_GUARD_MODE_FILE: MODEFILE, ...env },
    stdio: ['ignore', 'ignore', 'pipe'] });
  p.log = ''; p.stderr.on('data', d => { p.log += d; });
  return p;
}
async function up(port) { for (let i = 0; i < 60; i++) { try { if ((await fetch(`http://127.0.0.1:${port}/health`)).status === 200) return; } catch (_) {} await sleep(250); } throw new Error('no health on ' + port); }
const H = { 'content-type': 'application/json', accept: 'application/json, text/event-stream' };
async function init(port, token) {
  const r = await fetch(`http://127.0.0.1:${port}/mcp`, { method: 'POST', headers: { ...H, ...(token ? { 'cf-access-jwt-assertion': token } : {}) },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 't', version: '0' } } }) });
  return { status: r.status, text: await r.text() };
}
let pass = 0, fail = 0;
function ok(name, cond, extra = '') { if (cond) pass++; else fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${extra ? ' :: ' + extra : ''}`); }
const listeners = port => execFileSync('ss', ['-tlnH', `sport = :${port}`]).toString().split('\n').filter(Boolean).map(l => l.trim().split(/\s+/)[3]);

const P = 31900 + Math.floor(Math.random() * 40);
fs.writeFileSync(MODEFILE, 'shadow\n');
const g = start(P, {}); await up(P);

const L = listeners(P);
ok('binds 127.0.0.1 only (not * / ::)', L.length > 0 && L.every(a => a.startsWith('127.0.0.1:')), L.join(','));
let r = await init(P, null);
ok('shadow via mode file: anonymous initialize passes', r.status === 200, String(r.status));
ok('shadow logs the would-be deny', g.log.includes('"reason":"missing"') && g.log.includes('"mode":"shadow"'));
// wq#3266: the stdio child gets only the dc-role preload (no guard, no listener); test_call_window.mjs proves
// DC strips it again before spawning shells
ok('child does NOT inherit the gateway NODE_OPTIONS, only the dc role',
  r.text.includes(`"childNodeOptions":"--require=${path.join(T, 'commander-preload.cjs')}"`) && r.text.includes('"childPreloadRole":"dc"'), r.text.slice(0, 300));
ok('child still sees guard env (harmless, but documented)', r.text.includes('"childGuardPort":"' + P + '"'));

fs.writeFileSync(MODEFILE, 'enforce\n'); await sleep(1300);
r = await init(P, null); ok('flip to enforce WITHOUT restart: anonymous -> 403', r.status === 403, String(r.status));
r = await init(P, jwt()); ok('enforce: valid user JWT -> 200', r.status === 200 && r.text.includes('wq3103-fixture'), String(r.status));
r = await init(P, jwt({ common_name: 'svc.access' })); ok('enforce: valid service-token JWT (the mcpw path) -> 200', r.status === 200);
ok('enforce: /health still open', (await fetch(`http://127.0.0.1:${P}/health`)).status === 200);
ok('log mode field follows the file', g.log.includes('"mode":"enforce"'));

fs.writeFileSync(MODEFILE, 'off\n'); await sleep(1300);
r = await init(P, null); ok('garbage mode file -> enforce (fail closed)', r.status === 403, String(r.status));
fs.writeFileSync(MODEFILE, 'shadow\n'); await sleep(1300);
r = await init(P, null); ok('back to shadow', r.status === 200);
fs.chmodSync(MODEFILE, 0o000); await sleep(1300);
r = await init(P, null); ok('unreadable mode file -> enforce (fail closed)', r.status === 403, String(r.status));
fs.chmodSync(MODEFILE, 0o644); fs.rmSync(MODEFILE); await sleep(1300);
r = await init(P, null); ok('missing mode file -> env ACCESS_GUARD_MODE (shadow)', r.status === 200, String(r.status));
ok('log never contains a token signature', !g.log.includes(jwt().split('.')[2].slice(0, 20)));
g.kill();

// misconfig must still fail closed at startup (exit 78), with the composed preload
for (const [n, env] of [['bad aud', { ACCESS_GUARD_AUD: 'abc' }], ['no loopback port', { LOOPBACK_LISTEN_PORT: '' }]]) {
  const x = start(P + 50, env); const code = await new Promise(res => x.on('exit', res));
  ok(`misconfig (${n}) exits 78`, code === 78, String(code));
}
certsSrv.close(); fs.rmSync(T, { recursive: true, force: true });
console.log(`${pass}/${pass + fail} passed`); process.exit(fail ? 1 : 0);
