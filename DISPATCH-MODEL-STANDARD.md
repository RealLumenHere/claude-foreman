# Dispatch model standard — pointer

The router is the only model authority (orchestrator-strengthening Q5, wq#5083).
Do not keep a model table here or in any prompt template; it drifts.

MODEL line for any ticket = the router's answer:

    python3 /home/lumen/foreman/orchestrator/router.py assign <ticket-id> --dry-run

Routing table, lineup and the Fable security exclusion live in
`foreman/research/models.yaml` (`routing:`, `models:`, `security_exclusion:`), read by
`foreman/orchestrator/router.py`. Change models there, then run `router.py check`.

RULE: every dispatch prompt starts with  MODEL: <model @ effort>  taken from that command.
