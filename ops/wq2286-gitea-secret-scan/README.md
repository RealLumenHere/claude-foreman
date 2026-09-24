# wq#2286 - Gitea pre-receive secret scan (Anubis)

Gitea has no secret scanning or push protection, so a committed token was only caught by the
local layers (wq#2276 recorded all lumen/* repos as forge-unsupported). `scan.py` is a git
pre-receive hook that refuses a push carrying a token shape.

- Scans the objects that arrive with the push (blobs, commit messages, tags). Existing history
  is not rescanned on every push. Ref deletes are skipped.
- Token shapes: GitHub/GitLab (the wq#2243 sweep patterns), Cloudflare, Anthropic, OpenAI,
  OpenRouter, Slack, AWS access key, PEM private key headers.
- The value is never printed. A finding shows label, path:line and `sha256:<12 hex>`, the same
  fingerprint the rotation tickets use.
- Fails open on its own errors and after a 25 s budget (syslog `lumen-secret-scan`). A finding
  always refuses.

## A fixture that trips it
Put `secret-scan:allow` on the same line, or add the value's 12-hex sha to
`/etc/lumen-secret-scan/allow.txt` on Anubis. Build the fake at runtime (`"gh" + "p_" + ...`) so
the source file is not a hit either.

## Deploy
```
tar c -C ops/wq2286-gitea-secret-scan . | ssh anubis@100.121.17.5 \
  'rm -rf /tmp/ss && mkdir /tmp/ss && tar x -C /tmp/ss && sudo -n bash /tmp/ss/install.sh'
```
It puts `hooks/pre-receive.d/50-secret-scan` in every repo (Gitea's wrapper runs everything in
that directory). Re-run it after creating a repo; `install.sh --check` lists repos without it.

## Roll back
`sudo bash install.sh --remove` on Anubis, or delete one repo's `50-secret-scan`.

## Audit
`ssh anubis 'cd /tmp && sudo -n -u gitea sh -c "python3 - audit /var/lib/gitea/data/repositories/lumen/*.git"' < scan.py`
2026-09-24: 9 distinct values, all in claude-foreman test fixtures or a `SYNTHETIC_BEARER`
constant; every other repo clean. Seeded into allow.txt.
