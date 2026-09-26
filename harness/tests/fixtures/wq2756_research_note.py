# wq#2756 research note -- NOT a mask_replay accounting fixture (deliberately: it contains no
# reconstructed plaintext, so it will not and should not match anything in a tracked-text search,
# and this hash was NOT added to rotated_ledger.json because that ledger's contract is "CONFIRMED
# via a 401 check" and there is no live credential here to 401-test). This file exists only so
# the next person who hits this hash in a replay does not redo the investigation from scratch.
#
# value_sha256_12 = 5d04e64dac64, label=kv-secret, prefix="GITEA_TOKEN=", body len=25.
# first_seen: snapshot:20260925T231301Z-home_lumen_logs_supergateway_desktop-commander-mcp.log,
# in the same MCP log snapshot as two OTHER test_gitea_rotate_finish.sh literals that DID
# hash-confirm exactly: b21f55c9fc8b ("FAKE_NEW_TOKEN_1234567890123456789", the file's current
# committed content) and 75c89af73fa6 ("placeholder000000000000000000000", also current
# committed content). This third value sits in the same "Authorization: token X" test-mock
# pattern in the same file family, but `git log --all` on bin/tests/test_gitea_rotate_finish.sh
# shows only its final committed form (2 near-duplicate commits, aa5f110/3981459, both matching
# today's content) -- no revision anywhere in git history contains a 25-char token literal.
#
# Working theory, not proof: an uncommitted intermediate edit-in-progress state from the same
# development session that produced the other two (MCP logs capture every tool call, including
# drafts that never reach a commit). Left UNACCOUNTED on purpose per the standing rule -- never
# close a rotation row on a citation that cannot be checked -- so it keeps surfacing in future
# replays until either the exact string is found or a maintainer makes a documented call on it.
