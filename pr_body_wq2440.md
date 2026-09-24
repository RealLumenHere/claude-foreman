## What

First application of real fleet web search (wq#2439): a prior-art sweep across every active
project and owned device. Adds `reports/2440_prior_art_sweep.md` — for each project/device
it records what already exists, what to adopt, and what we can stop building. No code
changes; documentation only.

## Why

Frank, 2026-09-24: "there are already solutions to projects, projects further along than
us, hardware optimizations on systems we own that were never found." The fleet kept
reinventing things (QuestStack, the BC-250 Vulkan path, ScreenPad Linux drivers) that the
community had already shipped. wq#2439 gave the fleet real search; this is the one-time
audit that turns that into stopped-reinvention.

## How

For each active project (QuestFree, FreeMiko/Miko3, ScreenPad Plus RTD, Nest RE, memory,
dispatch, node registry) and each owned device (BC-250, MacPro6,1 + D700/D300, i5-1145/1185,
RK3399) ran `fleet-search {web,github,reddit,hn,so,read}` for existing tools, further-along
projects, and unapplied mods. Findings cite URLs. Exploit mechanics are deliberately left to
the per-project validation tickets (e.g. wq#2441) — the report names tools and links only.

Highlights: Quest RE → `starseed12345/QuestStack` (203★, released); ScreenPad → four
`asus-screenpad-*` repos; BC-250 → RADV/Vulkan per elektricm docs (drop ROCm on gfx1013);
memory → Obsidian over existing markdown. New spikes prior art justifies: D700/D300 GPU
inference on MacPro6,1 (registry says CPU-only; an r/ollama writeup shows full GPU accel on
the exact GPUs), and Freeko sideload as the quick Miko-launcher win.

## Testing

Documentation-only change; no automated tests apply. Every cited finding was retrieved live
via `fleet-search` this session (GitHub API star counts, repo READMEs, web/community
results) and URLs are included for re-verification.

## Risk / rollback

None to running systems — adds two files under `reports/`/root, touches no code or config.
Rollback is deleting the report. Recommendations are advisory; each adopt/spike call is
routed to its own ticket rather than actioned here.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
