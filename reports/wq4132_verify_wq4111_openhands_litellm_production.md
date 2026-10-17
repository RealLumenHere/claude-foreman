# wq#4132 — verify pass on wq#4111 (OPENHANDS: production deployment — configure LiteLLM connect)

Author: claude-sonnet-5. Verifier: claude-sonnet-5@high (this session). Archetype 8
adversarial verify. PR: lumen/claude-foreman#2076 (forge PR lookup 404'd against the
Gitea API from this worker — no auth token available to this lane; the shipping
commits `fc6b6d2d`/`50ca09f6` are already in this worktree's `main` history, which is
authoritative and matches the diff described).

## Verdict: CONFIRM

## What wq#4111 claims
1. `probe.py`: an unprivileged, host-side HTTP probe (no docker, no secrets) that checks
   LiteLLM's `/health/readiness`, OpenHands' `/`, and classifies `GET /api/conversations`
   as `ok`/`spa_fallback`/`down`/`unknown`. Live run shown in the README:
   `litellm_ready=true openhands_up=true api_routing=spa_fallback`.
2. New lead beyond wq#4007: `GET /api/conversations` returns the SPA's `index.html`
   (200, `text/html`, same content-length as `/`) while `POST` to the same path returns
   a real FastAPI `405 application/json` — proving the API router has a route there, but
   something ahead of it (likely a `StaticFiles(html=True)` catch-all) swallows GET
   before the router sees it.
3. `configure_and_run_task.py`: a privileged-lane script (BUILD workers can't run it —
   docker is denied) that attaches `openhands` to `docker_default`, points
   `LLM_BASE_URL`/`LLM_MODEL` at `http://litellm:4000/v1` / `deepseek-v4-pro`, recreates,
   verifies the container's *own* network namespace can reach LiteLLM, discovers the
   installed headless CLI's flags via `--help` rather than guessing, and runs one bounded
   test task, checking `openhands.db`'s conversation count went N→N+1.
4. `test_probe.py`: 4 mocked unit tests on `classify_api_routing`, claimed 4/4 pass.
5. Explicitly scoped to what a BUILD worker can do; defers all docker/compose/key work to
   a privileged lane, same posture as wq#4018's rotation script.

## What I tried to break
- **Re-ran the probe live**, same host, same session: `python3 probe.py` →
  `litellm /health/readiness: OK`, `openhands UI root: OK`,
  `openhands API routing (GET /api/conversations): spa_fallback` — byte-for-byte the
  same classification the README quotes. Not stale/cached; this process re-hit the
  live services.
- **Independently reproduced the GET/POST asymmetry claim** without importing the PR's
  own code (raw `urllib.request`, separate from `probe.py`'s `_get`): `GET
  /api/conversations` → `200`, `content-type: text/html`, `content-length: 2959`,
  body starts with `<!DOCTYPE html>`; `GET /` → `200`, `content-length: 2959` (same
  value — confirms "same content-length as /", not an approximation); `POST
  /api/conversations` → `405`, `content-type: application/json`,
  body `{"detail":"Method Not Allowed"}`. All three match the README's prose exactly.
- **Ran the test suite**: `python3 test_probe.py -v` → 4/4 pass
  (`test_json_response_is_ok`, `test_spa_shell_is_flagged`, `test_unreachable_is_down`,
  `test_unexpected_shape_is_unknown`), and read the mocks — each patches `probe._get`
  with a distinct `(status, headers, body)` tuple that actually drives a different
  branch of `classify_api_routing`, not a tautological assertion.
- **Checked the "BUILD worker can't touch docker" claim live, in my own session**:
  `docker ps` → `Permission to use Bash with command docker ps has been denied.` Same
  refusal signature wq#4007's failure.json recorded. Confirms the stated capability
  boundary is real, not an excuse to skip verification.
- **Checked the "`/home/lumen/containers/openhands/` is outside every `--add-dir`"
  claim live**: `ls -la /home/lumen/containers/openhands/` → blocked, "the following
  part requires approval" (outside allowed dirs). Matches.
- **Cross-checked the wq#4007 citations against the actual ticket**, not just the PR's
  recap of it: `wq show 4007` detail field states verbatim "throwaway container on
  docker_default reaches http://litellm:4000 (38 models, deepseek-v4-pro completion
  OK)" and "do NOT widen LiteLLM publish" and "add /alive probe to lumen_state (NOT
  /api/health, which is SPA HTML)" — all three facts `configure_and_run_task.py`/
  `probe.py`/README build on are accurately carried over, not invented (instinct 8
  fabrication check).
- **Read `configure_and_run_task.py` for logic bugs** (it can't be executed here —
  no docker — so this is static review, same constraint the author had): the
  `already_wired`/`--dry-run` branch matrix is correct — dry-run always returns 0 before
  any mutating call regardless of whether the container is already wired; the "already
  wired" path correctly skips `attach_network`/`write_dotenv`/`recreate` and falls
  through to verification only. `write_dotenv` opens with `os.O_CREAT, 0o600` before
  writing, so the `.env` is never briefly world-readable. `discover_headless_entrypoint`
  tries both CLI shapes and raises instead of guessing if neither exposes a task flag —
  matches the ticket's "never guess an unpinned image version's flags" doctrine line.
  `python3 -m py_compile` on all three scripts: clean.
- **Looked for overclaim**: the ticket title says "production deployment" but the
  landed diff is prep work only (probe + privileged-lane script + tests) — the actual
  network attach/recreate/task-run has NOT happened yet; `configure_and_run_task.py` is
  written to be run next by a privileged lane, not already executed. The README's "What
  the privileged lane runs next" section says this plainly and doesn't claim the
  production wiring is done, so this isn't a hidden overclaim, just an accurately
  self-described partial completion under the lane's real capability limits.
- **Checked the one claim I could NOT independently verify**: that `deepseek-v4-pro` is
  a real, working model behind LiteLLM. `curl`/`urllib` against
  `http://127.0.0.1:4000/v1/models` both get connection-reset (no master key available
  to this lane, consistent with wq#4018 rotating that key out of reach) — this is the
  same wall the PR itself documents hitting, so I can't go further than it did; the
  model-id claim traces to wq#4007's own verified finding (confirmed above), not a
  fresh guess by wq#4111.

## Probe run
`python3 probe.py` → `{"litellm_ready": true, "openhands_up": true, "api_routing":
"spa_fallback"}` (exit 0). `python3 test_probe.py -v` → 4/4 pass.

No contradiction found. Not escalating to opus-5@high.
