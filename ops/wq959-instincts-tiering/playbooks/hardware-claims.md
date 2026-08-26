# PLAYBOOK: hardware-claims (v1, 2026-08-26, wq#959)

Domain rules moved verbatim from `instincts.yaml` (tier-2 restructure, R1 §6). Trigger:
stating any hardware spec or model-fit claim. The tier-2 index entry
`hardware-claims-domain` points here. Source: Frank caught fabricated specs 2026-08-20.

## Specs come from disk
(added 2026-08-20)

HARDWARE SPECS: NEVER state a hardware spec (VRAM, TDP, core count, model-fit, tok/s)
from memory. ALWAYS read it from disk first: BC-250 specs at
/home/lumen/projects/bc250/BC250_PROJECT.md (16GB GDDR6 unified, ~13GB usable, 30-35B
MoE max at IQ2 quant, ~58 tok/s); Mac fleet specs via lumen_state.py. If the spec is
not on disk, web-search for it. If you cannot verify, say so. Frank is investing real
money based on these numbers.

## Model-fit claims come from project docs
(added 2026-08-20)

MODEL FIT CLAIMS: BC-250 = 30-35B MoE class at extreme quantization. NOT 70B. Mac Pro
6,1 (Isis/Hathor/Nephthys) = 32B max on unified memory. When asked what hardware can
run what model, READ the project docs, do not estimate.
