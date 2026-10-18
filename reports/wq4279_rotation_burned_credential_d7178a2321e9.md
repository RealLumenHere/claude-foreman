# wq#4279: aws alarm sha256 d7178a2321e9, settled as third-party public, not rotated

## Fired on
- wq#4267 attempt 1: `_runs/wq4267/mask-hits-1.json`, seen in the session transcript
- wq#4268 attempt 1 (also fired): `_runs/wq4268/mask-hits-1.json`, seen in `/tmp/fworker-wq4268/search1.json`

## Replay before settlement
`mask_replay.py 4267` and `mask_replay.py 4268` each exited 1 with one unaccounted value:
`aws len=16 sha256=d7178a2321e9 prefix='AKIA'`. In both, the context window is an Amazon
product URL ending `...&SubscriptionId=AKIA<aws 16 chars>`, taken from a `fleet-search web`
result for the Polaris Freedom pool cleaner (wq#4266 prior-art).

## Identification
Amazon affiliate and PA-API links put the publisher's AWS access key ID in the
`SubscriptionId` query param, and search engines index those URLs. The key belongs to a
third-party publisher, it is public, and it is not tied to any fleet account. I re-derived
the hash from `search1.json` with a regex on `SubscriptionId=AKIA([0-9A-Z]{16})` and passed
the match straight to sha256, so the value was never printed: 1 match, len 16, `d7178a2321e9`.

## Settlement
- Not rotated, because there is nothing of ours to rotate. Precedent: ISSUE-LOG row 1080
  (the public Sentry key in a CSP header).
- `mask_replay.py record-rotated --sha256-12 d7178a2321e9 --ticket wq4279` with the note
  "NOT ROTATED - third-party public value".
- After settlement, `mask_replay.py 4267` and `mask_replay.py 4268` both report "No rotation".
- ISSUE-LOG row 1437.

## Leak path
The leak path is `fleet-search web` passing Amazon `SubscriptionId` params through
unchanged. `fleet-search` lives in the bin repo, outside this lane, so the fix is filed as
wq#4281 (strip the param before emit). Until that lands, every Amazon hit can fire this
alarm again with a new hash.
