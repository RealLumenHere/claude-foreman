# Dispatch model standard (every dispatch prompt leads with a MODEL: line)

Basis (from models.yaml measurements): opus-5@high shows NO measured edge over
sonnet-5@high, and sonnet@high ~= opus@low cost. So Sonnet is the value default.

DEFAULT PICK:
- sonnet-5 @high   -> build, integration, debugging, PR shepherding, rebases, most tickets.
- sonnet-5 @xhigh  -> careful conflict-resolution / reapply / subtle work.
- opus-5 @high|xhigh-> deep RCA, novel design, escalation only when sonnet stalls.
- haiku-4-5        -> mechanical/high-volume (bulk pushes, formatting, simple sweeps).
- fable-5          -> rare, hardest reasoning (2x opus cost, weekly-capped).
- LiteLLM (cerebras/deepseek/local) -> NON-Claude calls only (Minerva, messenger,
  embeddings). Never the coding path — that stays on the Max sub.

RULE: every dispatch prompt Claude hands Frank starts with:  MODEL: <model @ effort>
so Frank never has to ask which model to select.
