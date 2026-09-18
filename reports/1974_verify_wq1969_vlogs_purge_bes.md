# wq#1974 — verify-pass: wq#1969 VictoriaLogs (Bes) purge/expire research (PR#603)

**Verdict: CONFIRM** — every checkable factual claim in the deliverable reproduces exactly
against live Bes state and the upstream VictoriaLogs docs/changelog; no fabricated
citations found.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/603 (merged, squash 2d97d51).
Deliverable: `research/VLOGS_PURGE_WQ1747.md` (211 lines, docs-only, no code, no
execution — matches its own "Scope: research only" claim).

## What the diff does

Adds a research doc answering wq#1747's ask: whether VictoriaLogs v1.19.0 on Bes can
delete/expire the 1,061 leaked-secret rows early, and if not, the safest remediation.
Recommends in-place upgrade + one scoped `POST /delete/run_task`, over the two
alternatives the ticket floated (snapshot+re-ingest, second short-retention instance).

## Verification performed

1. **Live-probed Bes directly** (read-only HTTP, same unauthenticated surface the doc
   used) rather than trusting the doc's own quoted output:
   - `curl .../flags` → confirmed no `-httpAuth.*`/`-delete.*`/`-forceMergeAuthKey` set
     (Finding 0's "completely unauthenticated" claim, exact match).
   - `curl .../metrics | grep app_version` → `v1.19.0`, exact match.
   - `* | count()` → `17,098,588` rows now vs. `17,095,006` cited (small delta consistent
     with ~5.5 hours of continued ingestion since the doc was written, not a discrepancy).
   - `syslog_id:supergateway | count()` → `938,284`, exact match to the doc's Finding 0
     correction of the ticket's 1,061 figure.
   - `/select/logsql/streams` → `_stream="{}"` holds 17,098,587 of the rows (one
     unrelated `test-deploy` stream), exact match to the doc's "single default stream"
     claim underpinning its rejection of the second-instance option.
2. **Independently fetched the VictoriaLogs CHANGELOG and main docs** via direct `curl`
   (WebFetch is not granted to this worker — kb/ISSUE-LOG.md #136 — worked around the
   same way the author would have had to): confirmed (a) "ability to delete stored logs"
   lands under `v1.38.0`, dated `2025-11-14`, exact match; (b) zero hits anywhere in the
   changelog for "retention filter"/"retentionFilter"/"per-stream retention", exact match
   to Finding 2's "no equivalent, OSS or enterprise" claim; (c) `v1.52.0` is the newest
   numbered header with `tip` above it, and `deleteAuthKey`+`SSRF`+`1635` all appear only
   in that unreleased `tip` section, exact match to the doc's "ships after v1.52.0" claim;
   (d) the two "backwards-incompatible" storage-format notes are dated `2025-03-16`
   (v1.17.0) and `2025-02-19` (v1.11.0) — both before v1.19.0 (2025-04-17) — and no third
   one exists through v1.52.0, exact match to Finding 3's upgrade-safety claim; (e)
   `/delete/run_task`, `/delete/active_tasks`, `/delete/stop_task`, `-delete.enable`, and
   `/internal/force_merge?partition_prefix=...` all appear verbatim in the main docs, and
   the "unnecessary... under normal conditions" / "isn't recommended to delete logs on a
   frequent basis... rare exceptional cases... GDPR compliance or removal of accidentally
   written security-sensitive data" phrasing the doc paraphrases checks out near-verbatim.
3. **Checked the doc's own risk disclosures for gaps**: it discloses the 1,061-row filter
   was never re-derived (only the stream-level 938,284 was confirmed) and flags this as a
   mandatory pre-delete dry-run in Steps §5, not a swept-under-the-rug gap. It discloses
   the ingestion-retry-on-restart behavior was not verified. Both are honest scope limits
   on a research ticket, not defects.
4. **Tried to break the recommendation logic itself**: forced-merge-as-delete (ruled out,
   confirmed the endpoint takes a date not a filter and performs compaction only,
   verified against docs directly); per-stream retention (ruled out, confirmed no such
   feature exists at any version); second-instance-by-stream (ruled out on the doc's own
   Finding 0 evidence, which I independently reproduced — there is in fact only one live
   stream today, so a second instance would only intercept future rows, not the 1,061
   already ingested). No hole found in any of the three rejections.
5. **Checked file placement against repo convention**: unlike wq#1970/PR#601 (contradicted
   by wq#1972 partly for this reason), `research/VLOGS_PURGE_WQ1747.md` **is** one of the
   files that prior verify-pass caught already misplaced at
   `foreman/research/VLOGS_PURGE_WQ1747.md` (double-nested,
   `/home/lumen/foreman/foreman/research/...` on disk) — confirmed still misplaced as of
   this session. This is the same pre-existing, repo-wide `router.py:114` bug wq#1972
   root-caused, not a new defect introduced by wq#1969's author (the ticket text itself
   spells the deliverable path as `foreman/research/VLOGS_PURGE_WQ1747.md`, so the author
   followed instructions literally). Already tracked and in progress: wq#1976 (`claimed`
   as of this session) names this exact file among the 5 to relocate. Not re-filed here;
   not treated as grounds for CONTRADICT since it is a dispatch/routing defect external
   to the ticket's actual research content, already owned elsewhere.

## Conclusion

CONFIRM. wq#1969/PR#603's factual claims (live Bes state, VictoriaLogs version history,
delete-API availability, retention-filter non-existence, forced-merge behavior,
storage-format upgrade safety) all reproduce exactly against independent live probes and
upstream docs fetched fresh this session. The three-way comparison against the ticket's
alternatives holds up under adversarial re-derivation. The one defect present (file
placement) is a pre-existing systemic bug already tracked under wq#1976, not something
introduced by or unique to this PR. Did not re-implement per this ticket's instruction —
verify only.
