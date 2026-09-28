// wq#2768 test-to-failure for access-jwt-guard.cjs, against real supergateway 3.4.3 + stdio fixture.
import http from 'node:http';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import path from 'node:path';

const DIR = path.dirname(new URL(import.meta.url).pathname);
const GUARD = process.env.GUARD || path.join(DIR, 'access-jwt-guard.cjs');
const LOOP = '/home/lumen/bin/loopback-listen.cjs';
const SG = '/home/lumen/.npm-global/bin/supergateway';
const FIX = '/home/lumen/foreman/ops/wq2751-listener-audit/fixture_mcp_stdio.py';
const TEAM = 'testteam', ISS = 'https://testteam.cloudflareaccess.com';
const AUD = crypto.randomBytes(32).toString('hex'), AUD2 = crypto.randomBytes(32).toString('hex');
const k1 = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
const k2 = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
const kEvil = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
const jwk = (k, kid) => ({ ...k.publicKey.export({ format: 'jwk' }), kid, alg: 'RS256', use: 'sig' });
let served = [jwk(k1, 'kid1')];
let certsHits = 0;
const certsSrv = http.createServer((q, r) => { certsHits++; r.writeHead(200, { 'content-type': 'application/json' }); r.end(JSON.stringify({ keys: served })); });
await new Promise(r => certsSrv.listen(0, '127.0.0.1', r));
const CERTS = `http://127.0.0.1:${certsSrv.address().port}/certs`;

const enc = o => Buffer.from(JSON.stringify(o)).toString('base64url');
function jwt({ key = k1.privateKey, kid = 'kid1', alg = 'RS256', iss = ISS, aud = [AUD], exp = 600, nbf = -5, extra = { email: 'f@x' } } = {}) {
  const now = Math.floor(Date.now() / 1000);
  const h = enc({ alg, kid, typ: 'JWT' }), p = enc({ iss, aud, exp: now + exp, nbf: now + nbf, iat: now, ...extra });
  const sig = alg === 'RS256' ? crypto.sign('RSA-SHA256', Buffer.from(`${h}.${p}`), key).toString('base64url') : '';
  return `${h}.${p}.${sig}`;
}

function start(port, env) {
  const p = spawn(SG, ['--stdio', `python3 ${FIX}`, '--port', String(port), '--outputTransport', 'streamableHttp',
    '--streamableHttpPath', '/mcp', '--healthEndpoint', '/health', '--logLevel', 'none'], {
    env: { PATH: process.env.PATH, HOME: process.env.HOME, NODE_OPTIONS: `--require=${LOOP} --require=${GUARD}`,
      LOOPBACK_LISTEN_PORT: String(port), ACCESS_GUARD_PORT: String(port), ACCESS_GUARD_TEAM: TEAM,
      ACCESS_GUARD_AUD: `${AUD},${AUD2}`, ACCESS_GUARD_ISS: ISS, ACCESS_GUARD_CERTS_URL: CERTS, ACCESS_GUARD_REFETCH_MS: '1000', ...env },
    stdio: ['ignore', 'ignore', 'pipe'] });
  p.log = ''; p.stderr.on('data', d => { p.log += d; });
  return p;
}
async function up(port) { for (let i = 0; i < 60; i++) { try { const r = await fetch(`http://127.0.0.1:${port}/health`); if (r.status === 200) return; } catch (_) {} await sleep(250); } throw new Error('no health on ' + port); }
const H = { 'content-type': 'application/json', accept: 'application/json, text/event-stream' };
async function mcp(port, token, method = 'initialize', url = '/mcp') {
  const body = method === 'initialize'
    ? { jsonrpc: '2.0', id: 1, method, params: { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 't', version: '0' } } }
    : { jsonrpc: '2.0', id: 2, method, params: {} };
  const r = await fetch(`http://127.0.0.1:${port}${url}`, { method: 'POST', headers: { ...H, ...(token ? { 'cf-access-jwt-assertion': token } : {}) }, body: JSON.stringify(body) });
  return { status: r.status, text: await r.text() };
}
let pass = 0, fail = 0;
function ok(name, cond, extra = '') { if (cond) pass++; else fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${extra ? ' :: ' + extra : ''}`); }

const P = 31990 + Math.floor(Math.random() * 5);
const g = start(P, {}); await up(P);
ok('health open without token', (await fetch(`http://127.0.0.1:${P}/health`)).status === 200);
ok('health with query still open', (await fetch(`http://127.0.0.1:${P}/health?x=1`)).status === 200);
ok('POST /health guarded', (await fetch(`http://127.0.0.1:${P}/health`, { method: 'POST' })).status === 403);
ok('GET / guarded', (await fetch(`http://127.0.0.1:${P}/`)).status === 403);
let r = await mcp(P, null); ok('no token -> 403', r.status === 403, String(r.status));
r = await mcp(P, jwt()); ok('valid user token -> initialize', r.status === 200 && r.text.includes('wq2751-fixture'), String(r.status));
r = await mcp(P, jwt(), 'tools/list'); ok('valid token -> tools/list body intact', r.status === 200 && r.text.includes('wq2751_echo'), r.text.slice(0, 80));
r = await mcp(P, jwt({ extra: { common_name: 'svc.access' } })); ok('valid service-token JWT -> 200', r.status === 200);
r = await mcp(P, jwt({ aud: [AUD2] })); ok('second pinned aud accepted', r.status === 200);
r = await mcp(P, jwt({ aud: AUD })); ok('aud as string accepted', r.status === 200);
r = await mcp(P, jwt({ aud: [crypto.randomBytes(32).toString('hex')] })); ok('wrong aud -> 403', r.status === 403);
r = await mcp(P, jwt({ iss: 'https://evil.cloudflareaccess.com' })); ok('wrong iss -> 403', r.status === 403);
r = await mcp(P, jwt({ exp: -120 })); ok('expired -> 403', r.status === 403);
r = await mcp(P, jwt({ exp: -10 })); ok('within leeway -> 200', r.status === 200);
r = await mcp(P, jwt({ nbf: 300 })); ok('nbf future -> 403', r.status === 403);
r = await mcp(P, jwt({ key: kEvil.privateKey })); ok('forged signature same kid -> 403', r.status === 403);
r = await mcp(P, jwt({ alg: 'none' })); ok('alg none -> 403', r.status === 403);
const t = jwt(); const [a, b, c] = t.split('.');
const tampered = `${a}.${enc({ ...JSON.parse(Buffer.from(b, 'base64url')), aud: [AUD], email: 'attacker@x', exp: Math.floor(Date.now()/1000) + 99999 })}.${c}`;
r = await mcp(P, tampered); ok('payload tampered -> 403', r.status === 403);
r = await mcp(P, 'garbage'); ok('malformed -> 403', r.status === 403);
r = await mcp(P, jwt({ key: k2.privateKey, kid: 'kid2' })); ok('unknown kid -> 403', r.status === 403);
served = [jwk(k1, 'kid1'), jwk(k2, 'kid2')]; await sleep(1100);
r = await mcp(P, jwt({ key: k2.privateKey, kid: 'kid2' })); ok('rotated-in kid2 accepted after refetch', r.status === 200);
const before = certsHits; await Promise.all(Array.from({ length: 20 }, () => mcp(P, jwt({ kid: 'nope' }))));
ok('unknown-kid flood rate-limited', certsHits - before <= 2, `fetches=${certsHits - before}`);
ok('log never contains token', !g.log.includes(t.split('.')[2]) && g.log.includes('"verdict"'));
g.kill();

const P2 = P + 10;
const d = start(P2, { ACCESS_GUARD_CERTS_URL: 'http://127.0.0.1:9/certs' }); await up(P2);
r = await mcp(P2, jwt()); ok('certs unreachable -> 503 fail closed', r.status === 503, String(r.status));
d.kill();

const P3 = P + 20;
const s = start(P3, { ACCESS_GUARD_MODE: 'shadow' }); await up(P3);
r = await mcp(P3, null); ok('shadow mode passes no-token', r.status === 200);
ok('shadow mode logs the would-be deny', s.log.includes('"reason":"missing"'));
s.kill();

for (const [n, env] of [['bad aud', { ACCESS_GUARD_AUD: 'abc' }], ['no team', { ACCESS_GUARD_TEAM: '' }], ['bad mode', { ACCESS_GUARD_MODE: 'off' }], ['bad port', { ACCESS_GUARD_PORT: 'x' }]]) {
  const x = start(P + 30, env); const code = await new Promise(res => x.on('exit', res));
  ok(`misconfig (${n}) exits 78`, code === 78, String(code));
}
certsSrv.close();
console.log(`${pass}/${pass + fail} passed`); process.exit(fail ? 1 : 0);
