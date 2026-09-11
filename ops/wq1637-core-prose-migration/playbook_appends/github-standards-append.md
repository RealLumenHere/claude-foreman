Target: /home/lumen/instructions/playbooks/github-standards.md — append at end of file.
Source ticket: wq#1637, matches instincts_candidates.yaml's `gitea-credential-domain`.

---

## GITEA CREDENTIAL — one file, one rotation point (wq#529, 2026-08-13)
Gitea (Anubis, http://100.121.17.5:3000, user lumen) token lives ONLY in
`/home/lumen/.secrets/gitea-lumen.token` (0600), read by a host-scoped git credential helper.
The 4 Gitea repos have clean remote URLs — never re-embed a token in a remote, `git remote -v`
prints it everywhere. Rotation helper: `/home/lumen/bin/gitea-rotate-finish`. Masking rule
(cost a live token once): mask protocol-agnostically `^[a-z]+://user:pass@`, never anchor on
`https://`, and remember hosts may be %3a-encoded.
