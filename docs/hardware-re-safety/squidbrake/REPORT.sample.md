# Squidbrake observe-mode pilot report

- mode: `observe`
- total events: 15 (in-scope 13, out-of-scope 2)
- would_have_held: 7  (53.8% of in-scope)
- allowed: 6
- false positives: 2  (28.6% of would_have_held)
- true catch rate: 28.6%  (operator verdict legit or denied)
- annotation coverage: 57.1%

## Holds by rule

| rule | count |
|---|---|
| hw-mcp-openocd-energize | 2 |
| hw-mcp-flashrom-write | 1 |
| hw-mcp-serial-energize | 1 |
| unknown-hw-mcp-default-hold | 1 |
| hw-cli-flash-erase | 1 |
| hw-cli-fuse | 1 |

## Operator verdicts

| verdict | count |
|---|---|
| <unannotated> | 3 |
| benign | 2 |
| denied | 1 |
| legit | 1 |
