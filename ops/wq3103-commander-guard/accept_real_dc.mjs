// wq#3266 acceptance against REAL desktop-commander behind real supergateway on scratch ports (nothing live):
//  1. start_process 'sleep 241' timeout_ms=4000, then kill -0 <pid> 15s later must succeed
//  2. start_process timeout_ms=120000 must return at <=55s (default MAX_CALL_WINDOW_MS, no env override)
// Negative control: the same probe 1 through a gateway WITHOUT the preload must find the pid dead, so probe 1 can fail.
// DC runs with HOME and DC_HANDLE_DB in a temp dir, so the live DC config and handle store are untouched.
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { DatabaseSync } from 'node:sqlite';
import { setTimeout as sleep } from 'node:timers/promises';
import path from 'node:path';

const DIR = path.dirname(new URL(import.meta.url).pathname);
const OPS = path.resolve(DIR, '..');
const SG = process.env.SG || '/home/lumen/.npm-global/bin/supergateway';
const DC = process.env.DC || '/home/lumen/.npm-global/bin/desktop-commander';
const certsSrv = http.createServer((q, r) => { r.writeHead(200, { 'content-type': 'application/json' }); r.end('{"keys":[]}'); });
await new Promise(r => certsSrv.listen(0, '127.0.0.1', r));
const T = fs.mkdtempSync(path.join(os.tmpdir(), 'wq3266-dc-'));
for (const [src, dst] of [[path.join(DIR, 'commander-preload.cjs'), 'commander-preload.cjs'],
  [path.join(DIR, 'dc-call-window.cjs'), 'dc-call-window.cjs'],
  [path.join(OPS, 'wq2751-listener-audit/loopback-listen.cjs'), 'loopback-listen.cjs'],
  [path.join(OPS, 'wq2768-access-guard/access-jwt-guard.cjs'), 'access-jwt-guard.cjs']]) fs.copyFileSync(src, path.join(T, dst));
const DB = path.join(T, 'handles.db');

function gateway(port, preload) {
  const env = { PATH: process.env.PATH, HOME: T, DC_HANDLE_DB: DB };
  if (preload) Object.assign(env, { NODE_OPTIONS: `--require=${path.join(T, 'commander-preload.cjs')}`, LOOPBACK_LISTEN_PORT: String(port),
    ACCESS_GUARD_PORT: String(port), ACCESS_GUARD_TEAM: 'testteam', ACCESS_GUARD_AUD: 'a'.repeat(64),
    ACCESS_GUARD_ISS: 'https://testteam.cloudflareaccess.com', ACCESS_GUARD_CERTS_URL: `http://127.0.0.1:${certsSrv.address().port}/certs`,
    ACCESS_GUARD_MODE: 'shadow' });
  const g = spawn(SG, ['--stdio', DC, '--port', String(port), '--outputTransport', 'streamableHttp', '--streamableHttpPath', '/mcp',
    '--healthEndpoint', '/health', '--logLevel', 'none'], { env, stdio: ['ignore', 'ignore', 'pipe'] });
  g.log = ''; g.stderr.on('data', d => { g.log += d; });
  return g;
}
async function up(port) { for (let i = 0; i < 80; i++) { try { if ((await fetch(`http://127.0.0.1:${port}/health`)).status === 200) return; } catch (_) {} await sleep(250); } throw new Error('no health on ' + port); }
async function startProcess(port, command, timeout_ms) {
  const t0 = Date.now();
  const r = await fetch(`http://127.0.0.1:${port}/mcp`, { method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'start_process', arguments: { command, timeout_ms } } }) });
  const t = await r.text();
  const elapsedMs = Date.now() - t0;
  const line = t.split('\n').find(l => l.startsWith('data:'));
  const text = JSON.parse(line ? line.slice(5) : t).result.content.map(c => c.text).join('\n');
  const m = text.match(/PID (\d+)/);
  return { pid: m && Number(m[1]), elapsedMs, text };
}
const alive = pid => { try { process.kill(pid, 0); return true; } catch (_e) { return false; } };
let pass = 0, fail = 0;
function ok(name, cond, extra = '') { if (cond) pass++; else fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${extra ? ' :: ' + extra : ''}`); }

const P = 32000 + Math.floor(Math.random() * 40);
const g = gateway(P, true), ctl = gateway(P + 50, false);
await up(P); await up(P + 50);

const [a, c] = await Promise.all([startProcess(P, 'sleep 241', 4000), startProcess(P + 50, 'sleep 241', 4000)]);
ok('preload: start_process returned a PID', !!a.pid, a.text.slice(0, 120));
ok('control: start_process returned a PID', !!c.pid, c.text.slice(0, 120));
await sleep(15000);
ok(`preload: kill -0 ${a.pid} succeeds 15s later`, alive(a.pid));
ok(`control (no preload): kill -0 ${c.pid} fails 15s later (probe can fail)`, !alive(c.pid));
for (const p of [a.pid, c.pid]) try { process.kill(p, 'SIGKILL'); } catch (_e) { }

const b = await startProcess(P, 'sleep 120', 120000);
ok(`timeout_ms=120000 returned at ${(b.elapsedMs / 1000).toFixed(1)}s (<=55s)`, b.elapsedMs <= 55000, b.text.slice(0, 120));
try { process.kill(b.pid, 'SIGKILL'); } catch (_e) { }
let rows = [];
try { const d = new DatabaseSync(DB); rows = d.prepare('SELECT tool, requested_ms, clamped_ms, line FROM call_window_warn').all(); d.close(); } catch (_e) { }
ok('WARN row in the handle db for the clamp', rows.length === 1 && rows[0].requested_ms === 120000 && rows[0].clamped_ms < 55000, JSON.stringify(rows));

g.kill(); ctl.kill(); certsSrv.close(); fs.rmSync(T, { recursive: true, force: true });
console.log(`${pass}/${pass + fail} passed`); process.exit(fail ? 1 : 0);
