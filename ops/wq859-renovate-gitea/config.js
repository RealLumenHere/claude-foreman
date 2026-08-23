// wq#859 — Renovate global config for the Lumen fleet's self-hosted Gitea (Anubis, :3000).
// endpoint comes from the shared fleet /home/lumen/agent/.env (GITEA_URL, not sensitive).
// token is a DEDICATED renovate-scoped Gitea token (write:repository, write:issue, read:user)
// in /home/lumen/services/renovate-gitea/.env — deliberately not the shared GITEA_TOKEN, which
// is scoped for other fleet automation and lacks issue permissions Renovate needs for the
// Dependency Dashboard. Both are loaded via the systemd unit's EnvironmentFile= — never
// hardcode either here.
module.exports = {
  platform: 'gitea',
  endpoint: process.env.GITEA_URL,
  token: process.env.RENOVATE_TOKEN,
  gitAuthor: 'Renovate Bot <renovate@lumen.local>',

  autodiscover: true,
  autodiscoverFilter: ['lumen/*'],

  onboardingConfig: {
    extends: ['config:recommended'],
  },

  // Frank reviews PRs by hand; auto-merge stays off until trust builds per-repo.
  automerge: false,
  platformAutomerge: false,

  cacheDir: '/home/lumen/services/renovate-gitea/cache',
  prConcurrentLimit: 5,
  branchConcurrentLimit: 10,
};
