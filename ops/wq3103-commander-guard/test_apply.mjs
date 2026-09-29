// wq#3103: test-to-failure of apply.sh against a SCRATCH systemd unit (dc-scratch-3103, port 318xx) shaped like
// desktop-commander-mcp. Covers deploy, flip gating, flip revert, rollback, deploy auto-rollback, and the dead-man
// timer firing when apply.sh itself is killed. Never touches the live unit. Needs passwordless sudo (Lumen has it).
import http from 'node:http';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync, execFileSync, spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';

const DIR = path.dirname(new URL(import.meta.url).pathname);
const APPLY = path.join(DIR, 'apply.sh');
const UNIT = 'dc-scratch-3103', PORT = 31850, BIN = '/tmp/dcs-bin', MODE = '/etc/lumen/dcs.mode', LOGDIR = '/tmp/dcs-logs';
const TEAM = 'testteam', ISS = 'https://testteam.cloudflareaccess.com', AUD = crypto.randomBytes(32).toString('hex');
const k1 = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
const certsSrv = http.createServer((q, r) => { r.writeHead(200, { 'content-type': 'application/json' }); r.end(JSON.stringify({ keys: [{ ...k1.publicKey.export({ format: 'jwk' }), kid: 'kid1', alg: 'RS256', use: 'sig' }] })); });
await new Promise(r => certsSrv.listen(31851, '127.0.0.1', r));
const enc = o => Buffer.from(JSON.stringify(o)).toString('base64url');
function jwt(aud = AUD) { const n = Math.floor(Date.now() / 1000), h = enc({ alg: 'RS256', kid: 'kid1' }), p = enc({ iss: ISS, aud: [aud], exp: n + 600, nbf: n - 5, common_name: 'svc' });
  return `${h}.${p}.${crypto.sign('RSA-SHA256', Buffer.from(`${h}.${p}`), k1.privateKey).toString('base64url')}`; }
const sh = (cmd, o = {}) => spawnSync('bash', ['-c', cmd], { encoding: 'utf8', ...o });
const sudo = (cmd) => sh(`sudo -n bash -c '${cmd}'`);
const H = { 'content-type': 'application/json', accept: 'application/json, text/event-stream' };
const INIT = JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 't', version: '0' } } });
async function post(headers = {}) { try { return (await fetch(`http://127.0.0.1:${PORT}/mcp`, { method: 'POST', headers: { ...H, ...headers }, body: INIT })).status; } catch (e) { return 0; } }
let pass = 0, fail = 0;
const ok = (n, c, x = '') => { c ? pass++ : fail++; console.log(`${c ? 'PASS' : 'FAIL'} ${n}${x ? ' :: ' + x : ''}`); };
const active = () => sh(`systemctl is-active ${UNIT}`).stdout.trim();
const listen = () => sh(`ss -tlnH 'sport = :${PORT}' | awk '{print $4}' | paste -sd,`).stdout.trim();
const ENV = `UNIT=${UNIT} PORT=${PORT} BIN=${BIN} MODE_FILE=${MODE} LOGDIR=${LOGDIR} AUD=${AUD} TEAM=${TEAM} ` +
  `EXTRA_DROPIN_LINES='Environment=ACCESS_GUARD_ISS=${ISS}\\nEnvironment=ACCESS_GUARD_CERTS_URL=http://127.0.0.1:31851/certs\\nEnvironment=ACCESS_GUARD_REFETCH_MS=1000'`;
// async on purpose: the certs server and the tunnel-traffic sender live in THIS process, a sync spawn would freeze them
const apply = (verb, extra = '') => new Promise(res => { const p = spawn('bash', ['-c', `sudo -n env ${ENV} ${extra} bash ${APPLY} ${verb}`]); let out = '';
  p.stdout.on('data', d => { out += d; }); p.stderr.on('data', d => { out += d; }); p.on('close', status => res({ status, stdout: out, stderr: '' })); });
async function tunnelTraffic(ms, tok = jwt()) { const end = Date.now() + ms; while (Date.now() < end) { await post({ 'cf-access-jwt-assertion': tok, 'cf-ray': 'test-ray' }); await sleep(700); } }

function cleanup() { sh(`sudo -n bash -c 'systemctl stop ${UNIT}-guard-rollback.timer ${UNIT}-guard-flipback.timer ${UNIT} 2>/dev/null; rm -rf /etc/systemd/system/${UNIT}.service /etc/systemd/system/${UNIT}.service.d ${MODE}*; systemctl daemon-reload; systemctl reset-failed 2>/dev/null'; rm -rf ${BIN} ${LOGDIR}`); }
cleanup();
fs.mkdirSync(BIN, { recursive: true }); fs.mkdirSync(LOGDIR, { recursive: true });
fs.writeFileSync(`/tmp/${UNIT}.service`, `[Unit]\nDescription=wq3103 scratch commander\n[Service]\nUser=lumen\nEnvironment=PATH=/home/lumen/.npm-global/bin:/usr/bin:/bin\n` +
  `ExecStart=/home/lumen/.npm-global/bin/supergateway --stdio "python3 ${DIR}/fixture_mcp_stdio.py" --port ${PORT} --outputTransport streamableHttp --streamableHttpPath /mcp --healthEndpoint /health --logLevel info\nRestart=always\nRestartSec=2\n`);
sudo(`cp /tmp/${UNIT}.service /etc/systemd/system/${UNIT}.service; systemctl daemon-reload; systemctl start ${UNIT}`);
for (let i = 0; i < 40 && (await post()) === 0; i++) await sleep(500);
ok('scratch unit up, UNGUARDED baseline: anonymous 200, binds *', (await post()) === 200 && /^(\*|0\.0\.0\.0|\[::\]):/.test(listen()), listen());

// A. deploy (tunnel evidence skipped; evidence path is exercised in E)
let r = await apply('deploy', 'SKIP_TUNNEL_EVIDENCE=1');
ok('A deploy exit 0', r.status === 0, (r.stdout + r.stderr).split('\n').slice(-4).join(' | '));
ok('A unit active, loopback only', active() === 'active' && listen().split(',').every(a => a.startsWith('127.0.0.1:')), listen());
ok('A shadow: anonymous passes', (await post()) === 200);
ok('A dead-man disarmed', sh(`systemctl is-active ${UNIT}-guard-rollback.timer`).stdout.trim() !== 'active');
ok('A mode file = shadow, root-owned', fs.readFileSync(MODE, 'utf8').trim() === 'shadow' && fs.statSync(MODE).uid === 0);
ok('A child of Commander does not inherit NODE_OPTIONS', (await (await fetch(`http://127.0.0.1:${PORT}/mcp`, { method: 'POST', headers: H, body: INIT })).text()).includes('"childNodeOptions":null'));

// B. flip refused without evidence
r = await apply('flip'); ok('B flip refused with no tunnel evidence (exit 3)', r.status === 3, String(r.status));
ok('B still shadow', fs.readFileSync(MODE, 'utf8').trim() === 'shadow');

// C. a would-be-denied tunnel request (wrong AUD) blocks the flip even with valid ones present
await post({ 'cf-access-jwt-assertion': jwt(), 'cf-ray': 'r1' }); await post({ 'cf-access-jwt-assertion': jwt(crypto.randomBytes(32).toString('hex')), 'cf-ray': 'r2' });
r = await apply('flip'); ok('C flip refused when a tunnel request would be denied (exit 3)', r.status === 3, String(r.status));

// D. flip revert: valid evidence but no traffic after the flip -> flips itself back
await sleep(1200);; // fresh evidence only: the touch below moves apply.sh's 'since' past the earlier would-deny line (never touch the system journal)
sudo(`touch ${'/etc/systemd/system/' + UNIT + '.service.d/20-wq3103-commander-guard.conf'}`); await sleep(1100);
await post({ 'cf-access-jwt-assertion': jwt(), 'cf-ray': 'r3' });
r = await apply('flip', 'FLIP_WAIT=8');
ok('D flip with no post-flip traffic reverts to shadow (exit 4)', r.status === 4 && fs.readFileSync(MODE, 'utf8').trim() === 'shadow', `${r.status} ${fs.readFileSync(MODE, 'utf8').trim()}`);

// E. flip success with live tunnel traffic during the wait
await post({ 'cf-access-jwt-assertion': jwt(), 'cf-ray': 'r4' });
const bg = tunnelTraffic(12000);
r = await apply('flip', 'FLIP_WAIT=20'); await bg;
ok('E flip with post-flip tunnel traffic succeeds', r.status === 0, (r.stdout + r.stderr).split('\n').slice(-3).join(' | '));
ok('E enforce: anonymous 403, valid tunnel JWT 200', (await post()) === 403 && (await post({ 'cf-access-jwt-assertion': jwt(), 'cf-ray': 'r5' })) === 200);
ok('E flip-back safety timer disarmed', sh(`systemctl is-active ${UNIT}-guard-flipback.timer`).stdout.trim() !== 'active');

// F. rollback
r = await apply('rollback');
ok('F rollback restores unguarded unit', r.status === 0 && active() === 'active' && !fs.existsSync(MODE) && (await post()) === 200 && /^(\*|0\.0\.0\.0|\[::\]):/.test(listen()), listen());

// G. deploy WITH evidence wait and no traffic -> automatic rollback, unit healthy again
r = await apply('deploy', 'EVIDENCE_WAIT=6');
ok('G deploy without tunnel traffic auto-rolls back (exit 1)', r.status === 1 && !fs.existsSync(MODE) && active() === 'active' && (await post()) === 200, `${r.status}`);
ok('G nothing left armed', sh(`systemctl is-active ${UNIT}-guard-rollback.timer`).stdout.trim() !== 'active');

// H. apply.sh SIGKILLed right after arming the dead-man -> the timer must do the rollback
r = await apply('deploy', 'SKIP_TUNNEL_EVIDENCE=1 TEST_DIE_AFTER_ARM=1 DEADMAN_SEC=12');
ok('H apply.sh died after arm (dead-man armed)', sh(`systemctl is-active ${UNIT}-guard-rollback.timer`).stdout.trim() === 'active');
sudo(`mkdir -p /etc/lumen; echo shadow > ${MODE}; mkdir -p /etc/systemd/system/${UNIT}.service.d; printf "[Service]\\nEnvironment=NODE_OPTIONS=--require=/nonexistent.cjs\\n" > /etc/systemd/system/${UNIT}.service.d/20-wq3103-commander-guard.conf; systemctl daemon-reload; systemctl restart ${UNIT}`);
await sleep(3000); ok('H broken half-applied state is really broken (unit not serving)', (await post()) === 0);
for (let i = 0; i < 40 && !((await post()) === 200); i++) await sleep(1000);
ok('H dead-man timer rolled it back on its own', (await post()) === 200 && !fs.existsSync(MODE) && active() === 'active');

cleanup(); certsSrv.close();
console.log(`${pass}/${pass + fail} passed`); process.exit(fail ? 1 : 0);
