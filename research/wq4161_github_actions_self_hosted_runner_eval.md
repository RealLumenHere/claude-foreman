# wq#4161 — EVALUATE: GitHub Actions self-hosted runner on fleet

## Verdict
**Do not install `actions/runner` (GitHub's official self-hosted runner) for Foreman CI.**
Foreman's actual git host is a self-hosted Gitea instance (`origin` resolves to
`http://100.121.17.5:3000/...`, confirmed via `git config --get remote.origin.url` in this
worktree, scheme+host only — path/credentials withheld per instinct 4), not `github.com`.
`lumen-pr`'s own forge detection (`/home/lumen/bin/lumen-pr:282-283`) encodes exactly this
split: `github.com|www.github.com) FORGE=github ;; *) FORGE=gitea ;;` — and a Gitea host is
what every PR URL in recent reports shows (e.g.
`/home/lumen/foreman/reports/wq3709_judgment_wq3701_m80s_fworker_role_gap.md` links a PR at
the same `100.121.17.5:3000` Gitea host).

GitHub Actions self-hosted runners register to a repo/org/enterprise through the
**github.com (or GHES) REST API** — there is no path for `actions/runner` to attach to a
plain Gitea repo. Installing the Docker image the ticket names (`actions/runner`) against
Foreman today would have nothing to register against; it is evaluating the wrong product
for this repo's actual host. The on-host equivalent for a Gitea repo is **Gitea Actions**
(`gitea/act_runner`), a different binary with its own registration token flow, which runs
the same `uses:`/`steps:` YAML syntax but is not `actions/runner` and was not what this
ticket asked to install.

## Prior art (fleet-search, 2026-10-18)
- GitHub's own hardening docs are explicit that self-hosted runners carry risk beyond a
  GitHub-hosted runner: "Self-hosted runners for GitHub do not have guarantees around
  running in ephemeral clean VMs... We recommend that you only use self-hosted runners
  with private repositories" — cited via
  https://www.sysdig.com/blog/how-threat-actors-are-using-self-hosted-github-actions-runners-as-backdoors
  and https://docs.github.com/en/actions/concepts/security/compromised-runners. The
  practical risk: a workflow triggered from a PR (even same-repo, and especially
  `pull_request_target`/fork PRs on a public repo) runs arbitrary attacker-controlled code
  on the runner host with whatever network/credential access that host has — a self-hosted
  runner on fleet hardware would need the same `--add-dir`/token-isolation discipline the
  rest of this fleet already applies to BUILD workers, not a bare install.
  https://www.sciencedirect.com/science/article/pii/S0164121226002517 ranks workflow
  permission scope (SR02) as the largest share of real-world self-hosted-runner risk
  (36.4%), ahead of network exposure — i.e. the `GITHUB_TOKEN`/secrets scope granted to the
  workflow matters more than the runner's network placement.
- Standard mitigation pattern is **ephemeral, single-job runners** torn down after each job
  (GitHub Docs, "Self-hosted runners reference":
  https://docs.github.com/en/actions/reference/runners/self-hosted-runners — "If you use
  ephemeral runners... each job runs in a fresh instance"), built as a container that
  registers, runs one job, and exits — e.g.
  https://github.com/cwpearson/github-runner-docker,
  https://dev.to/wayofthepie/ephemeral-self-hosted-github-actions-runners-1h5m. This
  pattern is Docker-in-Docker-friendly (one container per job, no shared state) and is the
  shape to copy if Foreman ever runs on github.com — not the long-lived shared-registration
  runner a naive `actions/runner` install produces by default.

## Fleet state checked
- **M80s fworker host is not online.** Physical bring-up (MEBx/vPro AMT config + Ubuntu
  install) is blocked on physical keyboard/monitor or remote KVM access per
  `/home/lumen/kb/ISSUE-LOG.md` issue #1244 (wq#3703), which produced a bring-up checklist
  (`fleet-config/docs/M80_PHYSICAL_BRINGUP.md`) rather than completing the install. The most
  recent M80s-tagged work (`/home/lumen/foreman/reports/wq3859_judgment_wq3849_m80s-llm-deployment.md`)
  still describes M80s hardware as "doesn't exist yet" from this session's reach. No report
  newer than wq#3859 shows the node onboarded (checked: no `m80` entries in
  `/home/lumen/foreman/harness/fleet-exec-policy.yaml` beyond an unrelated comment match).
  Per instinct 3, this is queued as a physical-gated follow-up, not a blocking question.

## Recommendation
1. **This ticket's premise is based on the wrong forge.** Before any runner work, confirm
   with Frank (or close as "evaluated, wrong target") whether Foreman should move to
   github.com, or whether the real ask is a **Gitea Actions `act_runner`** evaluation —
   that is the product that can actually attach to the current Gitea host. Filing that as a
   distinct follow-up ticket (not guessing which one Frank meant) is the correct next step;
   I'm not blocking this ticket on that question since the forge mismatch is itself the
   deliverable finding.
2. If/when Foreman moves to github.com, or a new github.com-hosted repo needs CI: use the
   ephemeral single-job container pattern above, scope the workflow's `GITHUB_TOKEN`
   permissions to the minimum the job needs (the biggest real-world risk factor per the
   ScienceDirect study above), and never enable the runner for `pull_request_target` on
   untrusted forks.
3. Installing anything (runner or `act_runner`) on the M80s host is blocked until the
   physical bring-up (wq#3703 checklist) completes — track that as the hardware
   prerequisite, independent of which runner product gets chosen.

No code change to Foreman itself follows from this evaluation; the deliverable is this
research doc plus the forge-mismatch finding.
