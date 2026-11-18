# wq#5082 prompt-archive-sweep

Design: `fleet-intel/designs/orchestrator-strengthening.md` §5 Q2. Archival is a sweep, not a question.

Every 2 min (`prompt-archive-sweep.timer`, user lumen) `/home/lumen/bin/prompt-archive-sweep` moves
`/home/lumen/fleet-intel/prompts/wq<N>-<slug>.md` to `prompts/_archive/` when wq#N is `done`, and logs one
line to `/home/lumen/logs/prompt-archive-sweep.log`.

Never moved: templates (`orchestrator*.md`, `*template*`, `limpet-build.md`), files whose ticket is not done
or not in queue.db, files with no `wq<N>-` prefix (session-briefing §2a lists these as UNTICKETED), symlinks.

Binary source of truth: lumen-bin (PR #291). Tests: `bin/tests/test_prompt_archive_sweep.py`.

    bash install.sh            # install + enable timer
    bash install.sh --verify   # deploy-verify contract (read-only)
    prompt-archive-sweep --dry-run
