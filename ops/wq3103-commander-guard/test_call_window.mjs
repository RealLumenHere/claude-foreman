// wq#3266 test-to-failure for the call-window clamp + start_process detach, against real supergateway 3.4.3 with the
// composed preload and a DC-shaped stdio fixture on a scratch port. MAX_CALL_WINDOW_MS=3000 keeps it fast;
// accept_real_dc.mjs runs the ticket's acceptance against real desktop-commander with the 55000 default.
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
const MAX = 3000;
const certsSrv = http.createServer((q, r) => { r.writeHead(200, { 'content-type': 'application/json' }); r.end('{"keys":[]}'); });
await new Promise(r => certsSrv.listen(0, '127.0.0.1', r));

const T = fs.mkdtempSync(path.join(os.tmpdir(), 'wq3266-'));
for (const [src, dst] of [[path.join(DIR, 'commander-preload.cjs'), 'commander-preload.cjs'],
  [path.join(DIR, 'dc-call-window.cjs'), 'dc-call-window.cjs'],
  [path.join(OPS, 'wq2751-listener-audit/loopback-listen.cjs'), 'loopback-listen.cjs'],
  [path.join(OPS, 'wq2768-access-guard/access-jwt-guard.cjs'), 'access-jwt-guard.cjs']]) fs.copyFileSync(src, path.join(T, dst));
const DB = path.join(T, 'handles.db');
const P = 31960 + Math.floor(Math.random() * 30);
const g = spawn(SG, ['--stdio', `node ${path.join(DIR, 'fixture_dc_stdio.cjs')}`, '--port', String(P), '--outputTransport',
  'streamableHttp', '--streamableHttpPath', '/mcp', '--healthEndpoint', '/health', '--logLevel', 'none'], {
  env: { PATH: process.env.PATH, HOME: process.env.HOME, NODE_OPTIONS: `--require=${path.join(T, 'commander-preload.cjs')}`,
    LOOPBACK_LISTEN_PORT: String(P), ACCESS_GUARD_PORT: String(P), ACCESS_GUARD_TEAM: 'testteam', ACCESS_GUARD_AUD: 'a'.repeat(64),
    ACCESS_GUARD_ISS: 'https://testteam.cloudflareaccess.com', ACCESS_GUARD_CERTS_URL: `http://127.0.0.1:${certsSrv.address().port}/certs`,
    ACCESS_GUARD_MODE: 'shadow', MAX_CALL_WINDOW_MS: String(MAX), DC_HANDLE_DB: DB },
  stdio: ['ignore', 'ignore', 'pipe'] });
g.log = ''; g.stderr.on('data', d => { g.log += d; });
for (let i = 0; ; i++) { try { if ((await fetch(`http://127.0.0.1:${P}/health`)).status === 200) break; } catch (_) {} if (i > 60) throw new Error('no health'); await sleep(250); }

async function rpc(method, params) {
  const r = await fetch(`http://127.0.0.1:${P}/mcp`, { method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) });
  const t = await r.text();
  const line = t.split('\n').find(l => l.startsWith('data:'));
  return JSON.parse(line ? line.slice(5) : t);
}
const call = async (name, args) => JSON.parse((await rpc('tools/call', { name, arguments: args })).result.content[0].text);
const alive = pid => { try { process.kill(pid, 0); return true; } catch (_e) { return false; } };
const warnRows = () => { try { const d = new DatabaseSync(DB); const r = d.prepare('SELECT * FROM call_window_warn').all(); d.close(); return r; } catch (_e) { return []; } };
let pass = 0, fail = 0;
function ok(name, cond, extra = '') { if (cond) pass++; else fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${extra ? ' :: ' + extra : ''}`); }

const init = (await rpc('initialize', { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 't', version: '0' } })).result.serverInfo;
// the dc-role preload strips both before DC code runs; the detach checks below prove it loaded
ok('DC env carries no NODE_OPTIONS/role once the dc-role preload ran', init.dcRole === null && init.dcNodeOptions === null, JSON.stringify(init));

// (b) detach: sleep 241 must outlive the call window; the in-session control proves the reaper really ran
let r = await call('start_process', { command: 'sleep 241', control_cmd: 'sleep 242', timeout_ms: 1000 });
ok('start_process shell runs in its own session', r.childSid === r.pid && r.dcSid !== r.childSid, JSON.stringify(r));
await sleep(4000);
ok('DC child reaped after the call', !alive(r.dcPid));
ok('control (DC session) reaped after the call', !alive(r.controlPid));
ok('start_process sleep 241 still alive after the reap (kill -0)', alive(r.pid));
try { process.kill(r.pid, 'SIGKILL'); } catch (_e) { }

// (a)+(c) clamp + WARN
ok('no WARN rows before any clamp', warnRows().length === 0);
let t0 = Date.now();
r = await call('start_process', { command: 'sleep 200', timeout_ms: 120000 });
const wall = Date.now() - t0;
ok(`timeout_ms 120000 clamped to what is left of ${MAX} after DC startup`, r.timeoutSeen >= 1000 && r.timeoutSeen <= MAX - 500, String(r.timeoutSeen));
ok(`call returned within the window end to end (${wall}ms)`, wall <= MAX, String(wall));
try { process.kill(r.pid, 'SIGKILL'); } catch (_e) { }
let w = warnRows();
ok('one WARN row in DC_HANDLE_DB', w.length === 1 && w[0].tool === 'start_process' && w[0].requested_ms === 120000 && w[0].clamped_ms === r.timeoutSeen
  && w[0].line.startsWith('WARN call-window:'), JSON.stringify(w));
ok('one WARN line on stderr (journal)', (g.log.match(/"event":"clamp"/g) || []).length === 1);

for (const name of ['interact_with_process', 'read_process_output']) {
  r = await call(name, { command: 'true', timeout_ms: 90000 });
  ok(`${name} clamped too`, r.timeoutSeen <= MAX - 500, String(r.timeoutSeen));
}
r = await call('start_process', { command: 'true', timeout_ms: 1500 });
ok('timeout_ms under the window passes untouched', r.timeoutSeen === 1500);
r = await call('read_file', { command: 'true', timeout_ms: 90000 });
ok('other tools are not clamped', r.timeoutSeen === 90000);
w = warnRows();
ok('WARN rows only for clamped calls (3)', w.length === 3, String(w.length));

r = await call('start_process', { command: 'echo "no=${NODE_OPTIONS-unset} role=${COMMANDER_PRELOAD_ROLE-unset}"', timeout_ms: 2000 });
ok('DC shells inherit neither NODE_OPTIONS nor the role', r.out.trim() === 'no=unset role=unset', r.out.trim());

g.kill(); certsSrv.close(); fs.rmSync(T, { recursive: true, force: true });
console.log(`${pass}/${pass + fail} passed`); process.exit(fail ? 1 : 0);
