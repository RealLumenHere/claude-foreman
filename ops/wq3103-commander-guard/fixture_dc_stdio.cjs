'use strict';
// wq#3266 stdio MCP fixture shaped like desktop-commander's start_process: spawns the command the way
// terminal-manager.js does (env TERM=xterm-256color), answers at timeout_ms or exit, and reports the timeout_ms it
// received plus the shell's session id. `control_cmd` is spawned WITHOUT the TERM marker (stays in DC's session) as
// the negative control for the reaper.
const { spawn } = require('child_process');
const fs = require('fs');
const readline = require('readline');

const sid = pid => { try { return Number(fs.readFileSync(`/proc/${pid}/stat`, 'utf8').split(') ')[1].split(' ')[3]); } catch (_e) { return null; } };
const reply = (id, result) => process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id, result }) + '\n');

readline.createInterface({ input: process.stdin }).on('line', line => {
  let msg; try { msg = JSON.parse(line); } catch (_e) { return; }
  if (!('id' in msg)) return;
  if (msg.method === 'initialize') {
    return reply(msg.id, { protocolVersion: msg.params.protocolVersion, capabilities: { tools: {} },
      serverInfo: { name: 'wq3266-fixture', version: '0', dcPid: process.pid,
        dcNodeOptions: process.env.NODE_OPTIONS || null, dcRole: process.env.COMMANDER_PRELOAD_ROLE || null } });
  }
  if (msg.method !== 'tools/call') return reply(msg.id, {});
  const a = msg.params.arguments || {};
  const t0 = Date.now();
  const env = { ...process.env, TERM: 'xterm-256color' };
  const child = spawn(a.command || 'true', { shell: '/bin/bash', env, windowsHide: true });
  const control = a.control_cmd ? spawn(a.control_cmd, { shell: '/bin/bash' }) : null;
  let out = '';
  child.stdout.on('data', d => { out += d; });
  let done = false;
  const finish = () => {
    if (done) return; done = true;
    reply(msg.id, { content: [{ type: 'text', text: JSON.stringify({ pid: child.pid, controlPid: control && control.pid,
      dcPid: process.pid, dcSid: sid(process.pid), childSid: sid(child.pid), timeoutSeen: a.timeout_ms ?? null,
      elapsedMs: Date.now() - t0, out }) }] });
  };
  child.on('exit', () => setTimeout(finish, 50));
  setTimeout(finish, typeof a.timeout_ms === 'number' ? a.timeout_ms : 5000);
});
